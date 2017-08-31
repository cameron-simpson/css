#!/usr/bin/python
#
# Fuse interface to a Store.
# Uses llfuse: https://bitbucket.org/nikratio/python-llfuse/
# Formerly used fusepy: https://github.com/terencehonles/fusepy
# but that doesn't work with Python 3 and has some other problems.
#       - Cameron Simpson <cs@cskk.id.au>
#

from collections import namedtuple
from logging import getLogger, FileHandler as LogFileHandler, Formatter as LogFormatter
import errno
import os
from os import O_CREAT, O_RDONLY, O_WRONLY, O_RDWR, O_APPEND, O_TRUNC, O_EXCL
import stat
import sys
from threading import Thread, RLock
from types import SimpleNamespace as NS
from cs.lex import texthexify, untexthexify
from cs.logutils import X, debug, info, warning, error, exception, DEFAULT_BASE_FORMAT
from cs.pfx import Pfx, PfxThread
from cs.obj import O
from cs.queues import IterableQueue
from cs.range import Range
from cs.serialise import put_bs, get_bs, put_bsdata, get_bsdata
from cs.threads import locked
from . import defaults
from .archive import strfor_Dirent, write_Dirent_str
from .debug import dump_Dirent
from .dir import Dir, FileDirent, SymlinkDirent, HardlinkDirent, D_FILE_T, decode_Dirent
from .parsers import scanner_from_filename, scanner_from_mime_type
from .paths import resolve
from .store import MissingHashcodeError

import llfuse
FuseOSError = llfuse.FUSEError

LOGGER_NAME = __name__
LOGGER_FILENAME = 'vtfuse.log'

# OSX setxattr option values
XATTR_NOFOLLOW = 0x0001
XATTR_CREATE   = 0x0002
XATTR_REPLACE  = 0x0004

XATTR_NAME_BLOCKREF = b'x-vt-blockref'

def mount(mnt, E, S, syncfp=None, subpath=None, readonly=False):
  ''' Run a FUSE filesystem, return the Thread running the filesystem.
      `mnt`: mount point
      `E`: Dirent of root Store directory
      `S`: backing Store
      `syncfp`: if not None, a file to which to write sync lines
      `subpath`: relative path from `E` to the directory to attach to the mountpoint
      `readonly`: forbid data modification operations
  '''
  log = getLogger(LOGGER_NAME)
  log.propagate = False
  log_handler = LogFileHandler(LOGGER_FILENAME)
  log_formatter = LogFormatter(DEFAULT_BASE_FORMAT)
  log_handler.setFormatter(log_formatter)
  log.addHandler(log_handler)
  FS = StoreFS(E, S, syncfp=syncfp, subpath=subpath, readonly=readonly)
  return FS._vt_runfuse(mnt)

def umount(mnt):
  ''' Unmount the filesystem mounted at `mnt`, return umount(8) exit status.
  '''
  return subprocess.call(['umount', mnt])

def handler(method):
  ''' Decorator for FUSE handlers.
      Prefixes exceptions with the method name, associated with the
      Store, prevents anything other than a FuseOSError being raised.
  '''
  def handle(self, *a, **kw):
    ##X("OP %s %r %r ...", method.__name__, a, kw)
    try:
      with Pfx(method.__name__):
        with self._vt_core.S:
          result = method(self, *a, **kw)
          ##X("OP %s %r %r => %r", method.__name__, a, kw, result)
          return result
    except FuseOSError:
      raise
    except MissingHashcodeError as e:
      error("raising IOError from missing hashcode: %s", e)
      raise FuseOSError(errno.EIO) from e
    except OSError as e:
      error("raising FuseOSError from OSError: %s", e)
      raise FuseOSError(e.errno) from e
    except Exception as e:
      exception("unexpected exception, raising EINVAL from .%s(*%r,**%r): %s:%s", method.__name__, a, kw, type(e), e)
      raise FuseOSError(errno.EINVAL) from e
    except BaseException as e:
      error("UNCAUGHT EXCEPTION")
      raise RuntimeError("UNCAUGHT EXCEPTION") from e
  return handle

def log_traces_queued(Q):
  for logcall, citation, elapsed, ctx, msg, *a in Q:
    dolog(logcall, citation, elapsed, ctx, msg, *a)

def dolog(logcall, citation, elapsed, ctx, msg, *a):
  logcall("%fs uid=%s/gid=%s/pid=%s %s " + msg,
          elapsed, ctx[0], ctx[1], ctx[2], citation ,*a)

class FileHandle(O):
  ''' Filesystem state for open files.
  '''

  def __init__(self, fs, E, for_read, for_write, for_append, lock=None):
    ''' Initialise the FileHandle with filesystem, dirent and modes.
    '''
    O.__init__(self)
    if lock is None:
      lock = fs._lock
    self.fs = fs
    self.log = fs.log
    self.logQ = fs.logQ
    self.E = E
    self.Eopen = E.open()
    self.for_read = for_read
    self.for_write = for_write
    self.for_append = for_append
    self._lock = lock

  def __str__(self):
    fhndx = getattr(self, 'fhndx', None)
    return "<FileHandle:fhndx=%d:%s>" % (fhndx, self.E,)

  def write(self, data, offset):
    ''' Write data to the file.
    '''
    fp = self.Eopen._open_file
    with fp:
      with self._lock:
        fp.seek(offset)
        written = fp.write(data)
    self.E.touch()
    return written

  def read(self, offset, size):
    ''' Read data from the file.
    '''
    if size < 1:
      raise ValueError("FileHandle.read: size(%d) < 1" % (size,))
    fp = self.Eopen._open_file
    with fp:
      with self._lock:
        fp.seek(offset)
        data = fp.read(size)
    return data

  def truncate(self, length):
    ''' Truncate the file, mark it as modified.
    '''
    self.Eopen._open_file.truncate(length)
    self.E.touch()

  def flush(self):
    ''' Commit file contents to Store.
        Chooses a scanner based on the Dirent.name.
    '''
    X("FileHandle.flush: self.E.name=%r", self.E.name)
    mime_type = self.E.meta.mime_type
    if mime_type is None:
      scanner = None
    else:
      X("look up scanner from mime_type %r", mime_type)
      scanner = scanner_from_mime_type(mime_type)
    if scanner is None:
      X("look up scanner from filename %r", self.E.name)
      scanner = scanner_from_filename(self.E.name)
    self.Eopen.flush(scanner)
    ## no touch, already done by any writes
    X("FileHandle.Flush DONE")

  def close(self):
    ''' Close the file, mark its parent directory as changed.
    '''
    self.Eopen.close()
    self.E.parent.change()

class Inode(NS):
  ''' An Inode associates an inode number and a Dirent.
  '''

  def __init__(self, inum, E):
    NS.__init__(self)
    self.inum = inum
    try:
      Einum = E.inum
    except AttributeError:
      E.inum = inum
    else:
      raise AttributeError("Inode.__init__(inum=%d,...): Dirent %s already has a .inum: %d"
                           % (inum, E, Einum))
    self.E = E
    self.krefcount = 0

  def __iadd__(self, delta):
    ''' Increment krefcount.
    '''
    if delta < 1:
      raise ValueError("Inode.__iadd__(%d, delta=%s): expected delta >= 1"
                       % (self.inum, delta))
    self.krefcount += delta

  def __isub__(self, delta):
    ''' Decrement krefcount.
    '''
    if delta < 1:
      raise ValueError("Inode.__isub__(%d, delta=%s): expected delta >= 1"
                       % (self.inum, delta))
    if self.krefcount < delta:
      error("Inode%d.__isub__(delta=%s): krefcount(%d) < delta"
                       % (self.inum, delta, self.krefcount))
      self.krefcount = 0
      ##raise ValueError("Inode.__isub__(%d, delta=%s): krefcount(%d) < delta" % (self.inum, delta, self.krefcount))
    else:
      self.krefcount -= delta

class Inodes(object):
  ''' Inode information for a filesystem.
  '''

  def __init__(self, fs, inodes_datatext=None):
    self.fs = fs                # main filesystem
    self.krefcount = {}         # kernel inode reference counts
    self._allocated = Range()   # range of allocated inode numbers
    self._inode_map = {}        # mapping from inum->Inode record,
                                # for all inodes which have been accessed
                                # or instantiated
    if inodes_datatext is None:
      # initialise an empty Dir
      self._hardlinks_dir, self._hardlinked = Dir('inodes'), Range()
    else:
      # Access the inode information (a Range and a Dir).
      # Return the Dir and update ._allocated.
      self._hardlinks_dir, self._hardlinked = self._load_inode_data(inodes_datatext, self._allocated)
    self._lock = RLock()

  def _load_inode_data(self, idatatext, allocated):
    ''' Decode the permanent inode numbers and the Dirent containing their Dirents.
    '''
    idata = untexthexify(idatatext)
    # load the allocated hardlinked inode values
    taken_data, offset1 = get_bsdata(idata)
    offset = 0
    hardlinked = Range()
    while offset < len(taken_data):
      start, offset = get_bs(taken_data, offset)
      end, offset = get_bs(taken_data, offset)
      allocated.add(start, end)
      hardlinked.add(start, end)
    # load the Dir containing the hardlinked Dirents
    hardlinked_dir, offset1 = decode_Dirent(idata, offset1)
    if offset1 < len(idata):
      warning("unparsed idatatext at offset %d: %r", offset1, idata[offset1:])
    return hardlinked_dir, hardlinked

  @locked
  def encode(self):
    ''' Transcribe the permanent inode numbers and the Dirent containing their Dirents.
    '''
    # record the spans of allocated inodes
    taken = b''.join( put_bs(S.start) + put_bs(S.end)
                      for S in self._hardlinked.spans() )
    # ... and append the Dirent.
    return put_bsdata(taken) + self._hardlinks_dir.encode()

  def _ipathelems(self, inum):
    ''' Path to an inode's Dirent.
    '''
    # this works because all leading bytes have a high bit, avoiding
    # collision with final bytes
    return [ str(b) for b in put_bs(inum) ]

  def new(self, E):
    ''' Allocate a new Inode for the supplied Dirent `E`; return the Inode.
    '''
    try:
      Einum = E.inum
    except AttributeError:
      span0 = self._allocated.span0
      next_inum = span0.end
      return self._add_Dirent(next_inum, E)
    raise ValueError("%s: already has .inum=%d" % (E, Einum))

  @locked
  def _add_Dirent(self, inum, E):
    if inum in self._allocated:
      raise ValueError("inum %d already allocated", inum)
    try:
      E = self._inode_map[inum]
    except KeyError:
      I = Inode(inum, E)
      self._allocated.add(inum)
      self._inode_map[inum] = I
      return I
    raise ValueError("inum %d already in _inode_map (but not in _allocated?)", inum)

  def _get_hardlink_Dirent(self, inum):
    ''' Retrieve the Dirent associated with `inum` from the hard link directory.
        Raises KeyError if the lookup fails.
    '''
    D = self._hardlinks_dir
    pathelems = self._ipathelems(inum)
    lastelem = pathelems.pop()
    for elem in pathelems:
      D = D[elem]
    return D[lastelem]

  def _add_hardlink_Dirent(self, inum, E):
    ''' Add the Dirent `E` to the hard link directory.
    '''
    if E.isdir:
      raise RuntimeError("cannot save Dir to hard link tree: %s" % (E,))
    D = self._hardlinks_dir
    pathelems = self._ipathelems(inum)
    lastelem = pathelems.pop()
    for elem in pathelems:
      try:
        D = D.chdir[elem]
      except KeyError:
        D = D.mkdir(elem)
    if elem in D:
      raise RuntimeError("inum %d already in hard link dir", inum)
    D[lastelem] = E

  @locked
  def inode(self, inum):
    I = self._inode_map.get(inum)
    if I is None:
      # not in the cache, must be in the hardlink tree
      E = self._get_hardlink_Dirent(inum)
      I = Inode(inum, E)
      self._inode_map[inum] = I
    return I

  __getitem__ = inode

  def __contains__(self, inum):
    try:
      I = self.inode[inum]
    except KeyError:
      return False
    else:
      return True

  def hardlink_for(self, E):
    ''' Create a new HardlinkDirent wrapping `E` and return the new Dirent.
    '''
    if E.ishardlink:
      raise RuntimeError("attempt to make hardlink for existing hardlink E=%s" % (E,))
    if E.type != D_FILE_T:
      raise ValueError("may not hardlink Dirents of type %s" % (E.type,))
    # use the inode number of the source Dirent
    inum = self.fs.E2i(E)
    if inum in self._hardlinked:
      error("make_hardlink: inum %d of %s already in hardlinked: %s",
            inum, E, self._hardlinked)
    self._add_hardlink_Dirent(inum, E)
    self._hardlinked.add(inum)
    H = HardlinkDirent.to_inum(inum, E.name)
    self._inode_map[inum] = Inode(inum, E)
    E.meta.nlink = 1
    return E

  @locked
  def inum_for_Dirent(self, E):
    ''' Allocate a new inode number for Dirent `E` and return it.
    '''
    allocated = self._allocated
    inum = None
    for span in allocated.spans():
      if span.start >= 2:
        inum = span.start - 1
        break
    if inum is None:
      inum = self.allocated.end
    self._add_Dirent(inum, E)
    self.allocated.add(inum)
    return inum

  @locked
  def dirent(self, inum):
    ''' Locate the Dirent for inode `inum`, return it.
        Raises ValueError if the `inum` is unknown.
    '''
    with Pfx("dirent(%d)", inum):
      return self[inum].E

class _StoreFS_core(object):
  ''' The core functionality supporting FUSE operations.
      The StoreFS_LLFUSE class subclasses the appropriate FUSE
      module and present shims that call the logic here.
      TODO: medium term: see if this can be made into a VFS layer
      to support non-FUSE operation, for example a VT FTP client
      or the like.
  '''

  def __init__(self, E, S, syncfp=None, subpath=None, readonly=False):
    ''' Initialise a new FUSE mountpoint.
        `E`: the root directory reference
        `S`: the backing Store
        `syncfp`: if not None, a file to which to write sync lines
        `subpath`: relative path to mount Dir
        `readonly`: forbid data modification
    '''
    O.__init__(self)
    if not E.isdir:
      raise ValueError("not dir Dir: %s" % (E,))
    self.S = S
    self.E = E
    self.subpath = subpath
    self.readonly = readonly
    if subpath:
      # locate subdirectory to display at mountpoint
      mntE, mntP, tail_path = resolve(E, subpath)
      if tail_path:
        raise ValueError("subpath %r does not resolve", subpath)
      if not mntE.isdir:
        raise ValueError("subpath %r is not a directory", subpath)
      self.mntE = mntE
    else:
      self.mntE = E
      mntP = None
    # set up a queue to collect logging requests
    # and a thread to process then asynchronously
    self.log = getLogger(LOGGER_NAME)
    self.logQ = IterableQueue()
    T = Thread(name="log-queue(%s)" % (self,),
               target=log_traces_queued,
               args=(self.logQ,))
    T.daemon = True
    T.start()
    self.syncfp = syncfp
    self._syncfp_last_dirent_text = None
    self.do_fsync = False
    self._fs_uid = os.geteuid()
    self._fs_gid = os.getegid()
    self._lock = S._lock
    self._path_files = {}
    self._file_handles = []
    self._inodes = Inodes(self, E.meta.get('fs_inode_data'))
    # preassign inode 1, llfuse seems to presume it :-(
    self.mnt_inum = 1
    self._inodes._add_Dirent(self.mnt_inum, self.mntE)

  def __str__(self):
    if self.subpath:
      return "<%s S=%s /=%s %r=%s>" % (self.__class__.__name__, self.S, self.E, self.subpath, self.mntE)
    else:
      return "<%s S=%s /=%s>" % (self.__class__.__name__, self.S, self.E)

  def __del__(self):
    self.logQ.close()

  def __getitem__(self, inum):
    ''' Lookup inode numbers.
    '''
    return self._inodes[inum]

  def _sync(self):
    with Pfx("_sync"):
      if defaults.S is None:
        raise RuntimeError("RUNTIME: defaults.S is None!")
      # update the inode table state
      self.E.meta['fs_inode_data'] = texthexify(self._inodes.encode())
      text = strfor_Dirent(self.E)
      if self.syncfp is not None:
        with self._lock:
          last_text = self._syncfp_last_dirent_text
          if last_text is not None and text == last_text:
            text = None
        if text is not None:
          write_Dirent_str(self.syncfp, text, etc=self.E.name)
          self.syncfp.flush()
          self._syncfp_last_dirent_text = text
          # debugging
          dump_Dirent(self.E, recurse=False)
          dump_Dirent(self._inodes._hardlinks_dir, recurse=False)

  def _resolve(self, path):
    ''' Call paths.resolve and return its result.
    '''
    return resolve(self.mntE, path)

  def _namei2(self, path):
    ''' Look up path. Raise FuseOSError(ENOENT) if missing. Return Dirent, parent.
    '''
    E, P, tail_path = self._resolve(path)
    if tail_path:
      raise FuseOSError(errno.ENOENT)
    return E, P

  def _namei(self, path):
    ''' Look up path. Raise FuseOSError(ENOENT) if missing. Return Dirent.
    '''
    E, P = self._namei2(path)
    return E

  @locked
  def E2i(self, E):
    ''' Compute the inode number for a Dirent.
        HardlinkDirents have a persistent .inum mapping to the Meta['iref'] field.
        Others do not and keep a private ._inum, not preserved after umount.
    '''
    try:
      inum = E.inum
    except AttributeError:
      I = self._inodes.new(E)
      inum = I.inum
    return inum

  def i2E(self, inum):
    ''' Return the Dirent associated with the supplied `inum`.
    '''
    return self._inodes.dirent(inum)

  def _Estat(self, E):
    ''' Stat a Dirent.
    '''
    inum = self.E2i(E)
    if E.ishardlink:
      E2 = self._inodes.dirent(inum)
    else:
      E2 = E
    return E2.meta.stat()

  def open2(self, P, name, flags, ctx):
    ''' Open a regular file given `P` parent Dir and `name`, allocate FileHandle, return FileHandle index.
        Increments the kernel reference count.
    '''
    if not P.isdir:
      error("parent (name=%r) not a directory, raising ENOTDIR", P.name)
      raise FuseOSError(errno.ENOTDIR)
    if name in P:
      if flags & O_EXCL:
        raise FuseOSError(errno.EEXIST)
      E = P[name]
    elif not flags & O_CREAT:
      raise FuseOSError(errno.ENOENT)
    else:
      E = FileDirent(name)
      P[name] = E
    return self.open(E, flags, ctx)

  def open(self, E, flags, ctx):
    ''' Open a regular file `E`, allocate FileHandle, return FileHandle index.
        Increments the kernel reference count.
    '''
    for_read = (flags & O_RDONLY) == O_RDONLY or (flags & O_RDWR) == O_RDWR
    for_write = (flags & O_WRONLY) == O_WRONLY or (flags & O_RDWR) == O_RDWR
    for_append = (flags & O_APPEND) == O_APPEND
    debug("for_read=%s, for_write=%s, for_append=%s",
          for_read, for_write, for_append)
    if (for_write or for_append) and self.readonly:
      raise OSError(errno.EROFS)
    FH = FileHandle(self, E, for_read, for_write, for_append, lock=self._lock)
    inum = self.E2i(E)
    I = self._inodes[inum]
    I += 1
    if flags & O_TRUNC:
      FH.truncate(0)
    fhndx = self._new_file_handle_index(FH)
    FH.fhndx = fhndx
    return fhndx

  def make_hardlink(self, E):
    return self._inodes.make_hardlink(E)

  @locked
  def _fh(self, fhndx):
    try:
      fh = self._file_handles[fhndx]
    except IndexError:
      error("cannot look up FileHandle index %r", fhndx)
      raise
    return fh

  def _fh_remove(self, fhndx):
    self._file_handles[fhndx] = None

  def _fh_close(self, fhndx):
    fh = self._fh(fhndx)
    fh.close()
    self._fh_remove(fhndx)

  @locked
  def _new_file_handle_index(self, file_handle):
    ''' Allocate a new FileHandle index for a `file_handle`.
        TODO: linear allocation cost, may need recode if things get
          busy; might just need a list of released fds for reuse.
    '''
    fhs = self._file_handles
    for fhndx, fh in enumerate(fhs):
      if fh is None:
        fhs[fhndx] = file_handle
        return fhndx
    fhs.append(file_handle)
    return len(fhs) - 1

  def _Eaccess(self, E, amode, ctx):
    with Pfx("_Eaccess(E=%r,amode=%s,ctx=%r)", E, amode, ctx):
      uid, gid, pid, umask = ctx.uid, ctx.gid, ctx.pid, ctx.umask
      if E.ishardlink:
        E2 = self._inodes.dirent(E.inum)
        warning("map hardlink %s => %s", E, E2)
      else:
        E2 = E
      # test the access against the caller's uid/gid
      # pass same in as default file ownership in case there are no metadata
      return E2.meta.access(amode, uid, gid,
                            default_uid=uid, default_gid=gid)

class StoreFS_LLFUSE(llfuse.Operations):
  ''' Class providing filesystem operations, suitable for passing
      to a FUSE() constructor.
  '''

  def __init__(self, E, S, syncfp=None, subpath=None, options=None, readonly=False):
    ''' Initialise a new FUSE mountpoint.
        `E`: the root directory reference
        `S`: the backing Store
        `syncfp`: if not None, a file to which to write sync lines
        `subpath`: relative path to mount Dir
        `readonly`: forbid data modification
    '''
    self._vt_core = _StoreFS_core(E, S, syncfp=syncfp, subpath=subpath, readonly=readonly)
    self.log = self._vt_core.log
    self.logQ = self._vt_core.logQ
    llf_opts = set(llfuse.default_options)
    # Not available on OSX. TODO: detect 'darwin' and make conditional
    if 'nonempty' in llf_opts:
      warning("llf_opts=%r: drop 'nonempty' option, not available on OSX",
              sorted(llf_opts))
      llf_opts.discard('nonempty')
    if options is not None:
      for opt in options:
        if opt.startswith('-'):
          llf_opts.discard(opt[1:])
        else:
          llf_opts.add(opt)
    self._vt_llf_opts = llf_opts

  # debugging aid
  def __getattr__(self, attr):
    warning("UNKNOWN ATTR: StoreFS.__getattr__: attr=%r", attr)
    def attrfunc(*a, **kw):
      warning("CALL UNKNOWN ATTR: %s(a=%r,kw=%r)", attr, a, kw)
      raise RuntimeError("CALL UNKNOWN ATTR %s(*%r,**%r)", attr, a, kw)
    return attrfunc

  def __str__(self):
    return "<%s %s>" % (self.__class__.__name__, self._vt_core)

  def _vt_runfuse(self, mnt):
    ''' Run the filesystem once.
    '''
    S = self._vt_core.S
    with S:
      llfuse.init(self, mnt, self._vt_llf_opts)
      def mainloop():
        llfuse.main()
        llfuse.close()
        S.close()
      T = PfxThread(target=mainloop)
      S.open()
      T.start()
      return T

  def _vt_i2E(self, inode):
    try:
      E = self._vt_core.i2E(inode)
    except ValueError as e:
      warning("access(inode=%d): %s", inode, e)
      raise FuseOSError(errno.EINVAL)
    return E

  def _vt_EntryAttributes(self, E):
    ''' Compute an llfuse.EntryAttributes object from `E`.meta.
    '''
    st = self._vt_core._Estat(E)
    EA = llfuse.EntryAttributes()
    EA.st_ino = self._vt_core.E2i(E)
    ## EA.generation
    ## EA.entry_timeout
    ## EA.attr_timeout
    EA.st_mode = st.st_mode
    EA.st_nlink = st.st_nlink
    uid = st.st_uid
    if uid is None or uid < 0:
      uid = self._vt_core._fs_uid
    gid = st.st_gid
    if gid is None or gid < 0:
      gid = self._vt_core._fs_gid
    EA.st_uid = uid
    EA.st_gid = gid
    ## EA.st_rdev
    EA.st_size = st.st_size
    ## EA.st_blksize
    ## EA.st_blocks
    EA.st_atime_ns = int(st.st_atime * 1000000000)
    EA.st_ctime_ns = int(st.st_ctime * 1000000000)
    EA.st_mtime_ns = int(st.st_mtime * 1000000000)
    return EA

  @staticmethod
  def _vt_str(bs):
    if isinstance(bs, bytes):
      try:
        s = bs.decode('utf-8')
      except UnicodeDecodeError as e:
        warning("decode %r: %e, falling back to surrogateescape", bs, e)
        s = bs.decode('utf-8', errors='surrogateescape')
    else:
      warning("_vt_str: expected bytes, got %s %r, passing unchanged", type(bs), bs)
      s = bs
    return s

  @staticmethod
  def _vt_bytes(s):
    if isinstance(s, str):
      bs = s.encode('utf-8')
    else:
      warning("_vt_bytes: expected str, got %s %r, passing unchanged", type(s), s)
      bs = s
    return bs

  ##############
  # FUSE support methods.

  @handler
  def access(self, inode, mode, ctx):
    E = self._vt_i2E(inode)
    return self._vt_core._Eaccess(E, mode, ctx)

  @handler
  def create(self, parent_inode, name_b, mode, flags, ctx):
    ''' Create a new file and open it. Return file handle index and EntryAttributes.
    '''
    if self._vt_core.readonly:
      raise FuseOSError(errno.EROFS)
    core = self._vt_core
    I = core[parent_inode]
    I += 1
    name = self._vt_str(name_b)
    P = self._vt_i2E(parent_inode)
    if name in P:
      warning("create(parent_inode=%d:%s,name=%r): already exists - surprised!",
              parent_inode, P, name)
    fhndx = core.open2(P, name, flags|O_CREAT, ctx)
    E = core._fh(fhndx).E
    E.meta.chmod(mode)
    P[name] = E
    return fhndx, self._vt_EntryAttributes(E)

  @handler
  def destroy(self):
    # TODO: call self.forget with all kreffed inums?
    self._vt_core._sync()

  @handler
  def flush(self, fh):
    ''' Handle close() system call.
    '''
    FH = self._vt_core._fh(fh)
    FH.flush()

  @handler
  def forget(self, ideltae):
    core = self._vt_core
    for inum, nlookup in ideltae:
      I = core[inum]
      I -= nlookup

  @handler
  def fsync(self, fh, datasync):
    self._vt_core._fh(fh).flush()

  @handler
  def fsyncdir(self, fh, datasync):
    # TODO: commit dir? implies flushing the whole tree
    warning("fsyncdir does nothing at present")

  @handler
  def getattr(self, inode, ctx):
    E = self._vt_core.i2E(inode)
    return self._vt_EntryAttributes(E)

  @handler
  def getxattr(self, inode, xattr_name, ctx):
    # TODO: test for permission to access inode?
    E = self._vt_core.i2E(inode)
    if xattr_name == XATTR_NAME_BLOCKREF:
        return E.block.encode()
    # bit of a hack: pretend all attributes exist, empty if missing
    # this is essentially to shut up llfuse, which otherwise reports ENOATTR
    # with a stack trace
    return E.meta.getxattr(xattr_name, b'')

  @handler
  def link(self, inode, new_parent_inode, new_name_b, ctx):
    if self._vt_core.readonly:
      raise FuseOSError(errno.EROFS)
    core = self._vt_core
    I = core[parent_inode]
    I += 1
    new_name = self._vt_str(new_name_b)
    # TODO: test for write access to new_parent_inode
    Esrc = core.i2E(inode)
    if not Esrc.isfile and not Esrc.ishardlink:
      raise FuseOSError(errno.EPERM)
    Pdst = core.i2E(new_parent_inode)
    if new_name in Pdst:
      raise FuseOSError(errno.EEXIST)
    # the final component must be a directory in order to create the new link
    if not Pdst.isdir:
      raise FuseOSError(errno.ENOTDIR)
    if Esrc.ishardlink:
      # point Esrc at the master Dirent in ._inodes
      inum = Esrc.inum
      Esrc = core.i2E(inum)
    else:
      # new hardlink, update the source
      # keep Esrc as the master
      # obtain EsrcLink, the HardlinkDirent wrapper for Esrc
      # put EsrcLink into the enclosing Dir, replacing Esrc
      src_name = Esrc.name
      inum0 = core.E2i(Esrc)
      EsrcLink = core.make_hardlink(Esrc)
      Esrc.parent[src_name] = EsrcLink
      inum = EsrcLink.inum
      if inum != inum0:
        raise RuntimeError("new hardlink: original inum %d != linked inum %d"
                           % (inum0, inum))
    # install the destination hardlink
    # make a new hardlink object referencing the inode
    # and attach it to the target directory
    EdstLink = HardlinkDirent.to_inum(inum, new_name)
    Pdst[new_name] = EdstLink
    # increment link count on underlying Dirent
    Esrc.meta.nlink += 1
    return self._vt_EntryAttributes(Esrc)

  @handler
  def listxattr(self, inode, ctx):
    # TODO: ctx allows to access inode?
    E = self._vt_core.i2E(inode)
    xattrs = set(E.meta.listxattrs())
    xattrs.add(XATTR_NAME_BLOCKREF)
    return list(xattrs)

  @handler
  def lookup(self, parent_inode, name_b, ctx):
    core = self._vt_core
    I = core[parent_inode]
    I += 1
    name = self._vt_str(name_b)
    # TODO: test for permission to search parent_inode
    if parent_inode == core.mnt_inum:
      P = core.mntE
    else:
      P = core.i2E(parent_inode)
    if name == '.':
      E = P
    elif name == '..':
      E.parent
    else:
      try:
        E = P[name]
      except KeyError:
        ##warning("lookup(parent_inode=%s, name=%r): ENOENT", parent_inode, name)
        ##raise FuseOSError(errno.ENOENT)
        EA = llfuse.EntryAttributes()
        EA.st_ino = 0
        EA.entry_timeout = 1.0
        return EA
    return self._vt_EntryAttributes(E)

  @handler
  def mkdir(self, parent_inode, name_b, mode, ctx):
    if self._vt_core.readonly:
      raise FuseOSError(errno.EROFS)
    core = self._vt_core
    I = core[parent_inode]
    I += 1
    name = self._vt_str(name_b)
    # TODO: test for permission to search and write parent_inode
    P = core.i2E(parent_inode)
    if not P.isdir:
      error("parent (%r) not a directory, raising ENOTDIR", P.name)
      raise FuseOSError(errno.ENOTDIR)
    if name in P:
      raise FuseOSError(errno.EEXIST)
    E = Dir(name, parent=P)
    E.meta.chmod(mode & 0o7777)
    E.touch()
    P[name] = E
    return self._vt_EntryAttributes(E)

  @handler
  def mknod(self, parent_inode, name_b, mode, rdev, ctx):
    if self._vt_core.readonly:
      raise FuseOSError(errno.EROFS)
    core = self._vt_core
    I = core[parent_inode]
    I += 1
    name = self._vt_str(name_b)
    P = core.i2E(parent_inode)
    if not P.isdir:
      error("parent (%r) not a directory, raising ENOTDIR", P.name)
      raise FuseOSError(errno.ENOTDIR)
    if name in P:
      raise FuseOSError(errno.EEXIST)
    if stat.S_ISREG(mode):
      E = FileDirent(name)
    else:
      # TODO: support pipes'n'stuff one day...
      raise FuseOSError(errno.ENOTSUP)
    E.meta.chmod(mode & 0o7777)
    E.touch()
    P[name] = E
    return self._vt_EntryAttributes(E)

  @handler
  def open(self, inode, flags, ctx):
    ''' Open an existing file, return file handle index.
    '''
    E = self._vt_i2E(inode)
    if flags & (O_CREAT|O_EXCL):
      warning("open(ionde=%d:%s,flags=0o%o): unexpected O_CREAT(0o%o) or O_EXCL(0o%o)",
              inode, E, flags, O_CREAT, O_EXCL)
      flags &= ~(O_CREAT|O_EXCL)
    for_write = (flags & O_WRONLY) == O_WRONLY or (flags & O_RDWR) == O_RDWR
    for_append = (flags & O_APPEND) == O_APPEND
    if (for_write or for_append) and self._vt_core.readonly:
      raise FuseOSError(errno.EROFS)
    fhndx = self._vt_core.open(E, flags, ctx)
    if for_write or for_append:
      E.change()
    return fhndx

  @handler
  def opendir(self, inode, ctx):
    # TODO: check for permission to read
    class _OpenDir:
      ''' An "open" Dir: keeps a list of the names from open time
          and a reference to the Dir so that it can validate the names
          at readdir time.
      '''
      def __init__(self, D):
        self.D = D
        self.names = list(D.keys())
    E = self._vt_core.i2E(inode)
    if not E.isdir:
      raise FuseOSError(errno.ENOTDIR)
    OD = _OpenDir(E)
    fhndx = self._vt_core._new_file_handle_index(OD)
    return fhndx

  @handler
  def read(self, fhndx, off, size):
    FH = self._vt_core._fh(fhndx)
    chunks = []
    while size > 0:
      data = FH.read(off, size)
      if len(data) == 0:
        break
      chunks.append(data)
      off += len(data)
      size -= len(data)
    return b''.join(chunks)

  @handler
  def readdir(self, fhndx, off):
    # TODO: if rootdir, generate '..' for parent of mount
    OD = self._vt_core._fh(fhndx)
    def entries():
      o = off
      D = OD.D
      names = OD.names
      while True:
        if o == 0:
          name = '.'
          E = D[name]
        elif o == 1:
          name = '..'
          E = D[name]
        else:
          o2 = o - 2
          if o2 >= len(names):
            break
          name = names[o2]
          if name == '.' or name == '..':
            # already special cased
            E = None
          else:
            E = D.get(name)
        if E is not None:
          # yield name, attributes and next offset
          yield self._vt_bytes(name), self._vt_EntryAttributes(E), o + 1
        o += 1
    return entries()

  @handler
  def readlink(self, inode, ctx):
    # TODO: check for permission to read the link?
    E = self._vt_core.i2E(inode)
    if not E.issym:
      raise FuseOSError(errno.EINVAL)
    return self._vt_bytes(E.pathref)

  @handler
  def release(self, fhndx):
    with Pfx("_fh_close(fhndx=%d)", fhndx):
      self._vt_core._fh_close(fhndx)

  @handler
  def releasedir(self, fhndx):
    self._vt_core._fh_remove(fhndx)

  @handler
  def removexattr(self, inode, xattr_name, ctx):
    if self._vt_core.readonly:
      raise FuseOSError(errno.EROFS)
    # TODO: test for inode ownership?
    if xattr_name == XATTR_NAME_BLOCKREF:
      # TODO: should we support this as "force recompute"?
      # feels like that would be a bug workaround
      raise FuseOSError(errno.EINVAL)
    E = self._vt_core.i2E(inode)
    meta = E.meta
    try:
      meta.delxattr(xattr_name)
    except KeyError:
      raise FuseOSError(errno.ENOATTR)

  @handler
  def rename(self, parent_inode_old, name_old_b, parent_inode_new, name_new_b, ctx):
    if self._vt_core.readonly:
      raise FuseOSError(errno.EROFS)
    name_old = self._vt_str(name_old_b)
    name_new = self._vt_str(name_new_b)
    Psrc = self._vt_core.i2E(parent_inode_old)
    if name_old not in Psrc:
      raise FuseOSError(errno.ENOENT)
    if not self._vt_core._Eaccess(Psrc, os.X_OK|os.W_OK, ctx):
      raise FuseOSError(errno.EPERM)
    Pdst = self._vt_core.i2E(parent_inode_new)
    if not self._vt_core._Eaccess(Pdst, os.X_OK|os.W_OK, ctx):
      raise FuseOSError(errno.EPERM)
    E = Psrc[name_old]
    del Psrc[name_old]
    E.name = name_new
    Pdst[name_new] = E

  @handler
  def rmdir(self, parent_inode, name_b, ctx):
    if self._vt_core.readonly:
      raise FuseOSError(errno.EROFS)
    name = self._vt_str(name_b)
    P = self._vt_core.i2E(parent_inode)
    if not self._vt_core._Eaccess(P, os.X_OK|os.W_OK, ctx):
      raise FuseOSError(errno.EPERM)
    try:
      E = P[name]
    except KeyError:
      raise FuseOSError(errno.ENOENT)
    else:
      if not E.isdir:
        raise FuseOSError(errno.ENOTDIR)
      if E.entries:
        raise FuseOSError(errno.ENOTEMPTY)
      del P[name]

  @handler
  def setattr(self, inode, attr, fields, fhndx, ctx):
    # TODO: test CTX for permission to chmod/chown/whatever
    # TODO: sanity check fields for other update_* flags?
    if self._vt_core.readonly:
      raise FuseOSError(errno.EROFS)
    E = self._vt_core.i2E(inode)
    with Pfx(E):
      M = E.meta
      if fields.update_atime:
        ##info("ignoring update_atime st_atime_ns=%s", attr.st_atime_ns)
        pass
      if fields.update_mtime:
        M.mtime = attr.st_mtime_ns / 1000000000.0
      if fields.update_mode:
        M.chmod(attr.st_mode&0o7777)
        extra_mode = attr.st_mode & ~0o7777
        typemode = stat.S_IFMT(extra_mode)
        extra_mode &= ~typemode
        if typemode != M.unix_typemode:
          warning("update_mode: E.meta.typemode 0o%o != attr.st_mode&S_IFMT 0o%o",
                  M.unix_typemode, typemode)
        if extra_mode != 0:
          warning("update_mode: ignoring extra mode bits: 0o%o", extra_mode)
      if fields.update_uid:
        M.uid = attr.st_uid
      if fields.update_gid:
        M.gid = attr.st_gid
      if fields.update_size:
        # TODO: what calls this? do we sanity check file sizes etc?
        warning("UNIMPLEMENTED: update_size st_size=%s", attr.st_size)
      return self._vt_EntryAttributes(E)

  @handler
  def setxattr(self, inode, xattr_name, value, ctx):
    if self._vt_core.readonly:
      raise FuseOSError(errno.EROFS)
    # TODO: check perms (ownership?)
    if xattr_name == XATTR_NAME_BLOCKREF:
      # TODO: support this as a "switch out the content action"?
      raise FuseOSError(errno.EINVAL)
    E = self._vt_core.i2E(inode)
    E.meta.setxattr(xattr_name, value)

  @handler
  def statfs(self, ctx):
    # TODO: get free space from the current Store
    #       implies adding some kind of method to stores?
    st = os.statvfs(".")
    fst = llfuse.StatvfsData()
    for attr in 'f_bsize', 'f_frsize', 'f_blocks', 'f_bfree', 'f_bavail', 'f_files', 'f_ffree', 'f_favail':
      setattr(fst, attr, getattr(st, attr))
    return fst

  @handler
  def symlink(self, parent_inode, name_b, target_b, ctx):
    if self._vt_core.readonly:
      raise FuseOSError(errno.EROFS)
    with Pfx("SYMLINK parent_iode=%r, name_b=%r, target_b=%r, ctx=%r", parent_inode, name_b, target_b, ctx):
      core = self._vt_core
      I = core[parent_inode]
      I += 1
      name = self._vt_str(name_b)
      target = self._vt_str(target_b)
      # TODO: check search/write on P
      P = core.i2E(parent_inode)
      if not P.isdir:
        raise FuseOSError(errno.ENOTDIR)
      if name in P:
        raise FuseOSError(errno.EEXIST)
      E = SymlinkDirent(name, {'pathref': target})
      P[name] = E
      return self._vt_EntryAttributes(E)

  @handler
  def unlink(self, parent_inode, name_b, ctx):
    if self._vt_core.readonly:
      raise FuseOSError(errno.EROFS)
    name = self._vt_str(name_b)
    # TODO: check search/write on P
    P = self._vt_core.i2E(parent_inode)
    if not P.isdir:
      raise FuseOSError(errno.ENOTDIR)
    try:
      del P[name]
    except KeyError:
      raise FuseOSError(errno.ENOENT)

  @handler
  def write(self, fhndx, off, buf):
    if self._vt_core.readonly:
      raise FuseOSError(errno.EROFS)
    FH = self._vt_core._fh(fhndx)
    written = FH.write(buf, off)
    if written != len(buf):
      warning("only %d bytes written, %d supplied", written, len(buf))
    return written

StoreFS = StoreFS_LLFUSE

if __name__ == '__main__':
  from .vtfuse_tests import selftest
  selftest(sys.argv)
