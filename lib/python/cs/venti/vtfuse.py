#!/usr/bin/python
#
# Fuse interface to a Store.
# Uses fusepy: https://github.com/terencehonles/fusepy
#       - Cameron Simpson <cs@zip.com.au>
#

from functools import partial
from collections import namedtuple
from logging import getLogger, FileHandler, Formatter
import errno
import os
from os import O_CREAT, O_RDONLY, O_WRONLY, O_RDWR, O_APPEND, O_TRUNC, O_EXCL
from os.path import basename
from pprint import pformat
import stat
import sys
from threading import Thread, RLock
import time
from types import SimpleNamespace as NS
from cs.debug import DummyMap, TracingObject
from cs.lex import texthexify, untexthexify
from cs.logutils import X, XP, debug, info, warning, error, Pfx, DEFAULT_BASE_FORMAT
from cs.obj import O, obj_as_dict
from cs.py.func import funccite, funcname
from cs.queues import IterableQueue
from cs.range import Range
from cs.serialise import put_bs, get_bs, put_bsdata, get_bsdata
from cs.threads import locked
from . import defaults
from .archive import strfor_Dirent, write_Dirent_str
from .block import Block
from .debug import dump_Dirent
from .dir import Dir, FileDirent, SymlinkDirent, HardlinkDirent, D_FILE_T, decode_Dirent
from .file import File
from .meta import NOUSERID, NOGROUPID
from .paths import resolve

# TODO: provide a hook to select the legacy fuse3 class
FUSE_CLASS = 'llfuse'

if FUSE_CLASS == 'llfuse':
  import llfuse
  from llfuse import FUSEError as FuseOSError
  FuseOSError = llfuse.FUSEError
elif FUSE_CLASS == 'fuse3':
  # my slightly hacked python-fuse with crude python 3 porting hacks
  from fuse3 import FUSEOSError

LOGGER_NAME = 'cs.venti.vtfuse'     # __qualname__ ?
LOGGER_FILENAME = 'vtfuse.log'

# OSX setxattr option values
XATTR_NOFOLLOW = 0x0001
XATTR_CREATE   = 0x0002
XATTR_REPLACE  = 0x0004

# records associated with an open file
# TODO: no support for multiple links or path-=open renames
OpenFile = namedtuple('OpenFile', ('path', 'E', 'fp'))

def mount(mnt, E, S, syncfp=None, subpath=None):
  ''' Run a FUSE filesystem on `mnt` with Dirent `E` and backing Store `S`.
      `mnt`: mount point
      `E`: Dirent of root Store directory
      `S`: backing Store
      `syncfp`: if not None, a file to which to write sync lines
      `subpath`: relative path from `E` to the directory to attach to the mountpoint
  '''
  log = getLogger(LOGGER_NAME)
  log.propagate = False
  handler = FileHandler(LOGGER_FILENAME)
  formatter = Formatter(DEFAULT_BASE_FORMAT)
  handler.setFormatter(formatter)
  log.addHandler(handler)
  FS = StoreFS(E, S, syncfp=syncfp, subpath=subpath)
  FS._vt_runfuse(mnt)

def trace_method(method):
  ## do nothing
  return method
  ##fname = '.'.join( (method.__module__, funccite(method)) )
  fname = '.'.join( (method.__module__, funcname(method)) )
  def traced_method(self, *a, **kw):
    citation = fname
    if a:
      citation += " " + pformat(a, depth=1)
    if kw:
      citation += " " + pformat(kw, depth=2)
    X("trace_method: %s ...", citation)
    with Pfx(citation):
      try:
        ctx = fuse_get_context()
      except NameError:
        ctx = (None, None, None)
      time0 = time.time()
      ##self.log.info("CALL %s", citation)
      try:
        result = method(self, *a, **kw)
      except FuseOSError as e:
        elapsed = time.time() - time0
        self.logQ.put( (self.log.info, citation, elapsed, ctx, "FuseOSError %s", e) )
        raise
      except Exception as e:
        elapsed = time.time() - time0
        dolog(self.log.exception, citation, elapsed, ctx, "%s %s", type(e), e)
        raise
      else:
        elapsed = time.time() - time0
        self.logQ.put( (self.log.info, citation, elapsed, ctx, "=> %r", result) )
        return result
  traced_method.__name__ = 'trace(%s)' % (fname,)
  return traced_method

def with_S(method):
  ''' FUSE request methods may run in any worker thread; push _vt_core.S around each call.
  '''
  def inner(self, *a, **kw):
    with Pfx(method.__name__):
      with self._vt_core.S:
        return method(self, *a, **kw)
  return inner

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
    return "<FileHandle %s>" % (self.E,)

  def write(self, data, offset):
    X("FH.write(data=%r, offset=%d)", data, offset)
    fp = self.Eopen._open_file
    with fp:
      with self._lock:
        fp.seek(offset)
        written = fp.write(data)
    self.E.touch()
    return written

  def read(self, offset, size):
    if size < 1:
      raise ValueError("FileHandle.read: size(%d) < 1" % (size,))
    fp = self.Eopen._open_file
    with fp:
      with self._lock:
        fp.flush()
        fp.seek(offset)
        data = fp.read(size)
    return data

  @trace_method
  def truncate(self, length):
    self.Eopen._open_file.truncate(length)
    self.E.touch()

  @trace_method
  def flush(self):
    self.Eopen.flush()
    ## no touch, already done by any writes
    ## self.E.touch()

  @trace_method
  def close(self):
    self.Eopen.close()
    ## no touch, already done by any writes
    ## self.E.touch()

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
    X("Inodes.__init__: _hardlinks=%s", self._hardlinked)
    self._lock = RLock()

  def _load_inode_data(self, idatatext, allocated):
    ''' Decode the permanent inode numbers and the Dirent containing their Dirents.
    '''
    XP("decode idatatext: %r", idatatext)
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
      X("Inodes.new: span0=%s", span0)
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
      X("_add_Dirent: add inum %d for Dirent %s", inum, E)
      I = Inode(inum, E)
      self._allocated.add(inum)
      X("after adding inum %d, .allocated=%s", inum, self._allocated)
      self._inode_map[inum] = I
      return I
    raise ValueError("inum %d already in _inode_map (but not in _allocated?)", inum)

  def _get_hardlink_Dirent(self, inum):
    ''' Retrieve the Dirent associated with `inum` from the hard link directory.
        Raises KeyError if the lookup fails.
    '''
    D = self._hardlinks_dir
    X("_get_hardlink_Dirent=%r", self._hardlinks_dir)
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
    return Edst

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
  def dirent2(self, inum):
    ''' Locate the Dirent for inode `inum`, return it and its parent.
        Raises ValueError if the `inum` is unknown.
    '''
    with Pfx("dirent2(%d)", inum):
      I = self[inum]
      return I.E, I.parentE

  @locked
  def dirent(self, inum):
    ''' Locate the Dirent for inode `inum`, return it.
        Raises ValueError if the `inum` is unknown.
    '''
    with Pfx("dirent(%d)", inum):
      return self[inum].E

class _StoreFS_core(object):
  ''' The core functionality supporting FUSE operations.
      The StoreFS_LLFUSE and StoreFS_FUSE3 classes subclass the
      appropriate FUSE module and present shims that call the logic
      here.
      TODO: medium term: see if this can be made into a VFS layer
      to support non-FUSE operation, for example a VT FTP client
      or the like.
  '''

  def __init__(self, E, S, syncfp=None, subpath=None):
    ''' Initialise a new FUSE mountpoint.
        `E`: the root directory reference
        `S`: the backing Store
        `syncfp`: if not None, a file to which to write sync lines
        `subpath`: relative path to mount Dir
    '''
    X("StoreFS.__init__(...)...")
    O.__init__(self)
    if not E.isdir:
      raise ValueError("not dir Dir: %s" % (E,))
    self.S = S
    self.E = E
    self.subpath = subpath
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
    self._inodes._add_Dirent(1, self.mntE)
    X("StoreFS.__init__: _inodes[1]=%s", self._inodes[1])
    X("StoreFS.__init__ COMPLETE")

  def __str__(self):
    if self.subpath:
      return "<%s S=%s /=%s %r=%s>" % (self.__class__.__name__, self.S, self.E, self.subpath, self.mntE)
    else:
      return "<%s S=%s /=%s>" % (self.__class__.__name__, self.S, self.E)

  def __del__(self):
    self.logQ.close()

  @trace_method
  def _sync(self):
    X("pid %d: _sync ...", os.getpid())
    if defaults.S is None:
      raise RuntimeError("RUNTIME: defaults.S is None!")
    if self.syncfp is not None:
      with self._lock:
        # update the inode table state
        self.E.meta['fs_inode_data'] = texthexify(self._inodes.encode())
        text = strfor_Dirent(self.E)
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

  def i2EP(self, inum):
    ''' Return the Dirent and parent Dirent associated with the supplied `inum`.
    '''
    return self._inodes.dirent2(inum)

  def i2E(self, inum):
    ''' Return the Dirent associated with the supplied `inum`.
    '''
    return self.i2EP(inum)[0]

  def _resolve(self, path):
    ''' Call cs.venti.paths.resolve and return its result.
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
      warning("E2i: allocated new Inode with inum %d", inum)
    return inum

  def i2E(self, inum):
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
      X("create %r", name)
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
    FH = FileHandle(self, E, for_read, for_write, for_append, lock=self._lock)
    inum = self.E2i(E)
    X("open: inum=%s", inum)
    I = self._inodes[inum]
    X("open: inode=%s", I)
    I += 1
    if flags & O_TRUNC:
      FH.truncate(0)
    fhndx = self._new_file_handle_index(FH)
    XP("OPEN: allocated new _file_handles[%s] => %s", fhndx, FH)
    return fhndx

  def make_hardlink(self, E):
    return self._inodes.make_hardlink(E)

  def kref_inc(self, inum, delta=1):
    I = self._inodes[inum]
    I += delta

  def kref_dec(self, inum, delta=1):
    I = self._inodes[inum]
    I -= delta

  @locked
  def _fh(self, fhndx):
    try:
      fh = self._file_handles[fhndx]
    except IndexError:
      error("cannot look up FileHandle index %r", fhndx)
      raise
    return fh

  def _fh_remove(self, fhndx):
    X("DEL _file_handles[%s] (pre: %r)", fhndx, self._file_handles)
    self._file_handles[fhndx] = None

  def _fh_close(self, fhndx):
    X("CLOSE _file_handles[%s] (pre: %r)", fhndx, self._file_handles)
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
    for fhndx in range(len(fhs)):
      if fhs[fhndx] is None:
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

if FUSE_CLASS == 'llfuse':

  class StoreFS_LLFUSE(llfuse.Operations):
    ''' Class providing filesystem operations, suitable for passing
        to a FUSE() constructor.
    '''

    def __init__(self, E, S, syncfp=None, subpath=None, options=None):
      ''' Initialise a new FUSE mountpoint.
          `E`: the root directory reference
          `S`: the backing Store
          `syncfp`: if not None, a file to which to write sync lines
          `subpath`: relative path to mount Dir
      '''
      self._vt_core = _StoreFS_core(E, S, syncfp=syncfp, subpath=subpath)
      self.log = self._vt_core.log
      self.logQ = self._vt_core.logQ
      llf_opts = set(llfuse.default_options)
      X("initial llf_opts from llfuse.default_options = %s", llf_opts)
      # Not available on OSX. TODO: detect 'darwin' and make conditional
      X("drop 'nonempty' option, not available on OSX")
      llf_opts.discard('nonempty')
      if options is not None:
        for opt in options:
          if opt.startswith('-'):
            llf_opts.discard(opt[1:])
          else:
            llf_opts.add(opt)
      X("final llf_opts = %s", llf_opts)
      self._vt_llf_opts = llf_opts

    # debugging aid
    def __getattr__(self, attr):
      warning("UNKNOWN ATTR: StoreFS.__getattr__: attr=%r", attr)
      def attrfunc(*a, **kw):
        warning("CALL UNKNOWN ATTR: %s(a=%r,kw=%r)", attr, a, kw)
        raise RuntimeError(attr)
      return attrfunc

    def __str__(self):
      return "<%s %s>" % (self.__class__.__name__, self._vt_core)

    def _vt_runfuse(self, mnt):
      ''' Run the filesystem once.
      '''
      with self._vt_core.S:
        X("llfuse.init(mnt=%r, %r)", mnt, self._vt_llf_opts)
        llfuse.init(self, mnt, self._vt_llf_opts)
        X("llfuse.main...")
        llfuse.main()
        X("llfuse.close...")
        llfuse.close()
        X("llfuse.close DONE, leaving _VT_RUNFUSE")

    def _vt_i2E(self, inode):
      try:
        E = self._vt_core.i2E(inode)
      except ValueError as e:
        warning("access(inode=%d): %s", inode, e)
        raise FUSEOSError(errno.EINVAL)
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
      EA.st_uid = st.st_uid if st.st_uid >= 0 else self._vt_core._fs_uid
      EA.st_gid = st.st_gid if st.st_gid >= 0 else self._vt_core._fs_gid
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

    @trace_method
    @with_S
    def access(self, inode, mode, ctx):
      E = self._vt_i2E(inode)
      return self._vt_core._Eaccess(E, mode, ctx)

    @trace_method
    @with_S
    def create(self, parent_inode, name_b, mode, flags, ctx):
      ''' Create a new file and open it. Return file handle index and EntryAttributes.
      '''
      X("create: name_b=%r ...", name_b)
      name = self._vt_str(name_b)
      P = self._vt_i2E(parent_inode)
      if name in P:
        warning("create(parent_inode=%d:%s,name=%r): already exists - surprised!",
                parent_inode, P, name)
      fhndx = self._vt_core.open2(P, name, flags|O_CREAT, ctx)
      E = self._vt_core._fh(fhndx).E
      E.meta.chmod(mode)
      P[name] = E
      return fhndx, self._vt_EntryAttributes(E)

    @trace_method
    @with_S
    def destroy(self):
      # TODO: call self.forget with all kreffed inums?
      self._vt_core._sync()

    @trace_method
    @with_S
    def flush(self, fh):
      FH = self._vt_core._fh(fh)
      FH.flush()
      inum = self._vt_core.E2i(FH.E)
      self._vt_core.kref_dec(inum)

    @with_S
    def forget(self, inode_list):
      for inode, nlookup in inode_list:
        self._vt_core.kref_dec(inode, nlookup)

    @trace_method
    @with_S
    def fsync(self, fh, datasync):
      self._fh(fh).flush()

    @trace_method
    @with_S
    def fsyncdir(self, fh, datasync):
      # TODO: commit dir? implies flushing the whole tree
      warning("fsyncdir does nothing at present")

    @trace_method
    @with_S
    def getattr(self, inode, ctx):
      E = self._vt_core.i2E(inode)
      return self._vt_EntryAttributes(E)

    @trace_method
    @with_S
    def getxattr(self, inode, xattr_name, ctx):
      # TODO: test for permission to access inode?
      E = self._vt_core.i2E(inode)
      meta = E.meta
      try:
        xattr = meta.xattrs[xattr_name]
      except KeyError:
        raise FuseOSError(errno.ENOATTR)
      return xattr

    @trace_method
    @with_S
    def link(self, inode, new_parent_inode, new_name_b, ctx):
      new_name = self._vt_str(new_name_b)
      # TODO: test for write access to new_parent_inode
      Esrc, Psrc = self._vt_core.i2EP(inode)
      if not Esrc.isfile and not Esrc.ishardlink:
        raise FuseOSError(errno.EPERM)
      Pdst = self._vt_core.i2E(new_parent_inode)
      if new_name in Pdst:
        raise FuseOSError(errno.EEXIST)
      # the final component must be a directory in order to create the new link
      if not Pdst.isdir:
        raise FuseOSError(errno.ENOTDIR)
      if Esrc.ishardlink:
        # point Esrc at the master Dirent in ._inodes
        inum = Esrc.inum
        Esrc = self._vt_core.i2E(inum)
      else:
        # new hardlink, update the source
        # keep Esrc as the master
        # obtain EsrcLink, the HardlinkDirent wrapper for Esrc
        # put EsrcLink into the enclosing Dir, replacing Esrc
        src_name = Esrc.name
        inum0 = self._vt_core.E2i(Esrc)
        EsrcLink = self._vt_core.make_hardlink(Esrc)
        Psrc[src_name] = EsrcLink
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
      return self._vt_EntryAttributes(E)

    @trace_method
    @with_S
    def listxattr(self, inode, ctx):
      # TODO: ctx allows to access inode?
      E = self._vt_core.i2E(inode)
      return list(E.meta.xattrs.keys())

    @trace_method
    @with_S
    def lookup(self, parent_inode, name_b, ctx):
      X("lookup %r...", name_b)
      name = self._vt_str(name_b)
      # TODO: test for permission to search parent_inode
      P, PP = self._vt_core.i2EP(parent_inode)
      if name == '.':
        E = P
      elif name == '..':
        E = PP
      else:
        try:
          E = P[name]
        except KeyError:
          raise FuseOSError(errno.ENOENT)
      return self._vt_EntryAttributes(E)

    @trace_method
    @with_S
    def mkdir(self, parent_inode, name_b, mode, ctx):
      name = self._vt_str(name_b)
      # TODO: test for permission to search and write parent_inode
      P = self._vt_core.i2E(parent_inode)
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

    @trace_method
    @with_S
    def mknod(self, parent_inode, name_b, mode, rdev, ctx):
      name = self._vt_str(name_b)
      P = self._vt_core.i2E(parent_inode)
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

    @trace_method
    @with_S
    def open(self, inode, flags, ctx):
      ''' Open an existing file, return file handle index.
      '''
      E = self._vt_i2E(inode)
      # TODO: mark parent, not root?
      X("MARK ROOT AS CHANGED - NEED TO FIND PARENT INSTEAD")
      self._vt_core.E.change()
      if flags & (O_CREAT|O_EXCL):
        warning("open(ionde=%d:%s,flags=0o%o): unexpected O_CREAT(0o%o) or O_EXCL(0o%o)",
                inode, E, flags, O_CREAT, O_EXCL)
        flags &= ~(O_CREAT|O_EXCL)
      fhndx = self._vt_core.open(E, flags, ctx)
      return fhndx

    @trace_method
    @with_S
    def opendir(self, inode, ctx):
      # TODO: check for permission to read
      X("opendir(inode=%s, ctx=%s)", inode, ctx)
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

    @trace_method
    @with_S
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

    @trace_method
    @with_S
    def readdir(self, fhndx, off):
      # TODO: if rootdir, generate '..' for parent of mount
      X("readdir(fhndx=%d, off=%d)", fhndx, off)
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

    @trace_method
    @with_S
    def readlink(self, inode, ctx):
      # TODO: check for permission to read the link?
      E = self._vt_core.i2E(inode)
      if not E.issym:
        raise FuseOSError(errno.EINVAL)
      return E.pathref

    @trace_method
    @with_S
    def release(self, fhndx):
      self._vt_core._fh_close(fhndx)

    @trace_method
    @with_S
    def releasedir(self, fhndx):
      self._vt_core._fh_remove(fhndx)

    @trace_method
    @with_S
    def removexattr(self, inode, xattr_name, ctx):
      raise FuseOSError(errno.ENOATTR)
      # TODO: test for inode ownership?
      E = self._vt_core.i2E(inode)
      meta = E.meta
      try:
        del meta.xattrs[xattr_name]
      except KeyError:
        raise FuseOSError(errno.ENOATTR)

    @trace_method
    @with_S
    def rename(self, parent_inode_old, name_old_b, parent_inode_new, name_new_b, ctx):
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

    @trace_method
    @with_S
    def rmdir(self, parent_inode, name_b, ctx):
      name = self._vt_str(name_b)
      P = self._vt_core.i2E(parent_inode)
      if not self._vt_core._Eaccess(Psrc, os.X_OK|os.W_OK, ctx):
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

    @trace_method
    @with_S
    def setattr(self, inode, attr, fields, fhndx, ctx):
      # TODO: test CTX for permission to chmod/chown/whatever
      # TODO: sanity check fields for other update_* flags?
      E = self._vt_core.i2E(inode)
      with Pfx(E):
        M = E.meta
        if fields.update_atime:
          info("ignoring update_atime st_atime_ns=%s", attr.st_atime_ns)
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

    @trace_method
    @with_S
    def setxattr(self, inode, xattr_name, value, ctx):
      # TODO: check perms (ownership?)
      E = self._vt_core.i2E(inode)
      E.meta.xattrs[xattr_name] = value

    @trace_method
    @with_S
    def statfs(self, ctx):
      # TODO: get free space from the current Store
      #       implies adding some kind of method to stores?
      st = os.statvfs(".")
      fst = llfuse.StatvfsData()
      for attr in 'f_bsize', 'f_frsize', 'f_blocks', 'f_bfree', 'f_bavail', 'f_files', 'f_ffree', 'f_favail':
        setattr(fst, attr, getattr(st, attr))
      return fst

    @trace_method
    @with_S
    def symlink(self, parent_inode, name_b, target, ctx):
      name = self._vt_str(name_b)
      # TODO: check search/write on P
      P = self._vt_core.i2E(parent_inode)
      if not P.isdir:
        raise FuseOSError(errno.ENOTDIR)
      if name in P:
        raise FuseOSError(errno.EEXIST)
      P[name] = SymlinkDirent(name, {'pathref': target})

    @trace_method
    @with_S
    def unlink(self, parent_inode, name_b, ctx):
      name = self._vt_str(name_b)
      # TODO: check search/write on P
      P = self._vt_core.i2E(parent_inode)
      if not P.isdir:
        raise FuseOSError(errno.ENOTDIR)
      try:
        del P[name]
      except KeyError:
        raise FuseOSError(errno.ENOENT)

    @with_S
    def write(self, fhndx, off, buf):
      FH = self._vt_core._fh(fhndx)
      written = FH.write(buf, off)
      if written != len(buf):
        warning("only %d bytes written, %d supplied", written, len(buf))
      return written

if FUSE_CLASS == 'llfuse':
  StoreFS = StoreFS_LLFUSE
elif FUSE_CLASS == 'fuse3':
  StoreFS = StoreFS_FUSE3

if __name__ == '__main__':
  from cs.venti.vtfuse_tests import selftest
  selftest(sys.argv)
