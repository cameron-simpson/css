#!/usr/bin/env python3
#
# Filesystem layer for Dirs.
# This is used by systems like FUSE.
# - Cameron Simpson <cs@cskk.id.au>

''' Filesystem semantics for a Dir.
'''

import errno
from inspect import getmodule
import os
from os import O_CREAT, O_RDONLY, O_WRONLY, O_RDWR, O_APPEND, O_TRUNC, O_EXCL, O_NOFOLLOW
import shlex
from types import SimpleNamespace as NS
from uuid import UUID
from cs.context import stackattrs
from cs.excutils import logexc
from cs.later import Later
from cs.logutils import exception, error, warning, info, debug
from cs.pfx import Pfx
from cs.range import Range
from cs.threads import locked
from cs.x import X
from . import defaults, Lock, RLock
from .block import isBlock
from .cache import BlockCache
from .dir import _Dirent, Dir, FileDirent
from .debug import dump_Dirent
from .meta import Meta
from .parsers import scanner_from_filename, scanner_from_mime_type
from .paths import resolve
from .transcribe import Transcriber, mapping_transcriber, parse

XATTR_VT_PREFIX = 'x-vt-'

DEFAULT_FS_THREAD_MAX = 16

def oserror(errno_, msg, *a):
  ''' Function to issue a warning and then raise an OSError.
  '''
  assert isinstance(errno_, int)
  assert isinstance(msg, str)
  if a:
    msg = msg % a
  raise OSError(errno_, msg)

# Generate OS_E* functions to raise custom OSErrors.
# This generates a suite of functions like this:
#  OS_EEXIST = lambda msg, *a: oserror(errno.EEXIST, msg, *a)
# for the known names in the errno module.
def mkOSfunc(M, Ename):
  ''' Create a wrapper function for `oserror` from errno value name `Ename`
      and save in in module `M` as `'OS_'+Ename`.

      This requires `Ename` to be a valid errno symbol from the `errno` module.
  '''
  Evalue = getattr(errno, Ename)
  setattr(M, 'OS_' + Ename, lambda msg, *a: oserror(Evalue, msg, *a))

# Generate dummy functions for missing symbols which we use.
def mkOSfuncEINVAL(M, Ename):
  ''' Create a wrapper function for `oserror` from the name `Ename`
      and save in in module `M` as `'OS_'+Ename`.

      This requires `Ename` to *not* be a valid errno symbol
      from the `errno` module, and is to support calls of "foreign"
      errno symbols;
      they are translated to `EINVAL` with an indication in the warning message.
  '''
  os_funcname = 'OS_' + Ename
  setattr(
      M, os_funcname, lambda msg, *a:
      oserror(errno.EINVAL, '(no %s, using EINVAL) ' + msg, Ename, *a)
  )

def _prep_osfuncs():
  ''' Generate the required wrappers for the various `E*` errno symbols.
  '''
  M = getmodule(oserror)
  for Ename in dir(errno):
    if Ename.startswith('E'):
      mkOSfunc(M, Ename)
  for Ename in 'ENOATTR', :
    if not hasattr(errno, Ename):
      mkOSfuncEINVAL(M, Ename)

_prep_osfuncs()

class FileHandle:
  ''' Filesystem state for an open file.
  '''

  def __init__(self, fs, E, for_read, for_write, for_append, lock=None):
    ''' Initialise the FileHandle with filesystem, dirent and modes.
    '''
    if lock is None:
      lock = Lock()
    self.fs = fs
    self.E = E
    self.for_read = for_read
    self.for_write = for_write
    self.for_append = for_append
    self._block_mapping = None
    self._lock = lock
    E.open()

  def __str__(self):
    fhndx = getattr(self, 'fhndx', None)
    return "<FileHandle:fhndx=%s:%s>" % (
        fhndx,
        self.E,
    )

  def bg(self, func, *a, **kw):
    ''' Function dispatcher.
    '''
    return self.fs.bg(func, *a, **kw)

  def close(self):
    ''' Close the file, mark its parent directory as changed.
    '''
    S = defaults.S
    R = self.E.flush()
    self.E.parent.changed = True
    S.open()
    # NB: additional S.open/close around self.E.close
    @logexc
    def withR(R):
      with stackattrs(defaults, S=S):
        self.E.close()
      S.close()

    R.notify(withR)

  def write(self, data, offset):
    ''' Write data to the file.
    '''
    f = self.E.open_file
    with f:
      with self._lock:
        if self.for_append and offset != len(f):
          OS_EFAULT(
              "%s: file open for append but offset(%s) != length(%s)", f,
              offset, len(f)
          )
        f.seek(offset)
        written = f.write(data)
    self.E.touch()
    return written

  def read(self, size, offset):
    ''' Read data from the file.
    '''
    if size < 1:
      raise ValueError("FileHandle.read: size(%d) < 1" % (size,))
    return self.E.open_file.read(size, offset=offset, longread=True)

  def truncate(self, length):
    ''' Truncate the file, mark it as modified.
    '''
    self.E.open_file.truncate(length)
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
    self.E.flush(scanner, dispatch=self.bg)
    ## no touch, already done by any writes
    X("FileHandle.Flush DONE")

@mapping_transcriber(
    prefix="Ino",
    transcription_mapping=lambda self: {
        'refcount': self.refcount,
        'E': self.E,
    },
    required=('refcount', 'E'),
    optional=(),
)
class Inode(Transcriber, NS):
  ''' An Inode associates an inode number and a Dirent.

      Attributes:
      * `inum`: the inode number
      * `E`: the primary Dirent
      * `refcount`: the number of Dir references to this Dirent
  '''

  def __init__(self, inum, E, refcount=1):
    NS.__init__(self)
    self.inum = inum
    self.E = E
    self.refcount = refcount

  def __repr__(self):
    return (
        "%s(inum=%d,refcount=%d,E=%s(%r))" % (
            type(self).__name__, self.inum, self.refcount,
            type(self.E).__name__, self.E.name
        )
    )

  def transcribe_inner(self, T, fp):
    return T.transcribe_mapping(
        {
            'refcount': self.refcount,
            'E': self.E,
        }, fp, T=T
    )

  @classmethod
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    if prefix != cls.transcribe_prefix:
      raise ValueError(
          "expected prefix=%r, got: %r" % (
              cls.transcribe_prefix,
              prefix,
          )
      )
    m, offset = T.parse_mapping(s, offset, stopchar=stopchar, T=T)
    return cls(None, m['E'], m['refcount']), offset

class Inodes:
  ''' Inode information for a filesystem.

      This consists of:
      - a Range denoting allocated inode numbers
      - a mapping of inode numbers to Inodes
      - a mapping of UUIDs to Inodes
      - a mapping of Dirents to Inodes

      Once an Inode is allocated it will have a reference by inum
      and Dirent. Since a Dirent need not have a UUID, it may not
      be mapped by UUID. The UUID map will be updated if `.add` is
      called later when the Dirent has a UUID, and clients should
      call `.add` to ensure that mapping if they rely on a Dirent's
      UUID, such as when making an IndirectDirent.
  '''

  def __init__(self, fs):
    self.fs = fs  # main filesystem
    self._allocated = Range()  # range of allocated inode numbers
    self._by_inum = {}
    self._by_uuid = {}
    self._by_dirent = {}
    self._lock = RLock()

  def load_fs_inode_dirents(self, D):
    ''' Load entries from an `fs_inode_dirents` Dir into the Inode table.
    '''
    X("LOAD FS INODE DIRENTS:")
    dump_Dirent(D)
    for name, E in D.entries.items():
      X("  name=%r, E=%r", name, E)
      with Pfx(name):
        # get the refcount from the :uuid:refcount" name
        _, refcount_s = name.split(':')[:2]
        I = self.add(E)
        I.refcount = int(refcount_s)
        X("  I=%s", I)

  def get_fs_inode_dirents(self):
    ''' Create an `fs_inode_dirents` Dir containing Inodes which
        should be preserved across mounts.
    '''
    D = Dir('fs_inode_dirents')
    for uuid, I in sorted(self._by_uuid.items()):
      if I.refcount > 0:
        D["%s:%d" % (uuid, I.refcount)] = I.E
      else:
        warning("refcount=%s, SKIP %s", I.refcount, I.E)
    X("GET FS INODE DIRENTS:")
    dump_Dirent(D)
    return D

  def _new_inum(self):
    ''' Allocate a new Inode number.
    '''
    allocated = self._allocated
    if allocated:
      span0 = allocated.span0
      inum = span0.end
    else:
      inum = 1
    allocated.add(inum)
    return inum

  def add(self, E, inum=None):
    ''' Add the Dirent `E` to the Inodes, return the new Inode.
        It is not an error to add the same Dirent more than once.
    '''
    with Pfx("Inodes.add(E=%s)", E):
      if E.isindirect:
        raise ValueError("indirect Dirents may not become Inodes")
      if inum is not None and inum < 1:
        raise ValueError("inum must be >= 1, got: %d" % (inum,))
      uu = E.uuid
      I = self._by_dirent.get(E)
      if I:
        assert I.E is E
        if inum is not None and I.inum != inum:
          raise ValueError(
              "inum=%d: Dirent already has an Inode with a different inum: %s"
              % (inum, I)
          )
        if uu:
          # opportunisticly update UUID mapping
          # in case the Dirent has acquired a UUID
          I2 = self._by_uuid.get(uu)
          if I2:
            assert I2.E is E
          else:
            self._by_uuid[uu] = I
        return I
      # unknown Dirent, create new Inode
      if inum is None:
        inum = self._new_inum()
      else:
        I = self._by_inum.get(inum)
        if I:
          raise ValueError("inum %d already allocated: %s" % (inum, I))
        self._allocated.add(inum)
      I = Inode(inum, E)
      self._by_dirent[E] = I
      self._by_inum[inum] = I
      if uu:
        self._by_uuid[uu] = I
      return I

  def __getitem__(self, ndx):
    if isinstance(ndx, int):
      try:
        I = self._by_inum[ndx]
      except KeyError as e:
        raise IndexError("unknown inode number %d: %s" % (ndx, e))
      return I
    if isinstance(ndx, UUID):
      return self._by_uuid[ndx]
    if isinstance(ndx, _Dirent):
      return self._by_dirent[ndx]
    raise TypeError("cannot deference indices of type %r" % (type(ndx),))

  def __contains__(self, ndx):
    try:
      _ = self[ndx]
    except (KeyError, IndexError):
      return False
    return True

class FileSystem:
  ''' The core filesystem functionality supporting FUSE operations
      and in principle other filesystem-like access.

      See the `cs.vt.fuse` module for the `StoreFS_LLFUSE` class (aliased
      as `StoreFS`) and associated mount function which presents a
      `FileSystem` as a FUSE mount.

      TODO: medium term: see if this can be made into a VFS layer
      to support non-FUSE operation, for example a VT FTP client
      or the like.
  '''

  def __init__(
      self,
      E,
      *,
      S=None,
      archive=None,
      subpath=None,
      readonly=None,
      append_only=False,
      show_prev_dirent=False,
      thread_max=None,
  ):
    ''' Initialise a new mountpoint.

        Parameters:
        * `E`: the root directory reference
        * `S`: the backing Store
        * `archive`: if not None, an Archive or similar, with a
          `.update(Dirent[,when])` method
        * `subpath`: relative path to mount Dir
        * `readonly`: forbid data modification
        * `append_only`: append only mode: files may only grow,
          filenames may not be changed or deleted
        * `show_prev_dirent`: show previous Dir revision as the '...' entry
    '''
    if not E.isdir:
      raise ValueError("not dir Dir: %s" % (E,))
    if S is None:
      S = defaults.S
    self._old_S_block_cache = S.block_cache
    self.block_cache = S.block_cache or defaults.block_cache or BlockCache()
    S.block_cache = self.block_cache
    S.open()
    if readonly is None:
      readonly = S.readonly
    if thread_max is None:
      thread_max = DEFAULT_FS_THREAD_MAX
    self.E = E
    self.S = S
    self.archive = archive
    if archive is None:
      self._last_sync_state = None
    else:
      self._last_sync_state = bytes(E)
    self.subpath = subpath
    self.readonly = readonly
    self.append_only = append_only
    self.show_prev_dirent = show_prev_dirent
    if subpath:
      # locate subdirectory to display at mountpoint
      mntE, _, tail_path = resolve(E, subpath)
      if tail_path:
        raise ValueError("subpath %r does not resolve" % (subpath,))
      if not mntE.isdir:
        raise ValueError("subpath %r is not a directory" % (subpath,))
      self.mntE = mntE
    else:
      mntE = E
    self.mntE = mntE
    self.is_darwin = os.uname().sysname == 'Darwin'
    self.device_id = -1
    self._fs_uid = os.geteuid()
    self._fs_gid = os.getegid()
    self._lock = RLock()
    self._later = Later(DEFAULT_FS_THREAD_MAX)
    self._later.open()
    self._path_files = {}
    self._file_handles = []
    inodes = self._inodes = Inodes(self)
    self[1] = mntE
    try:
      with Pfx("fs_inode_dirents"):
        fs_inode_dirents = E.meta.get("fs_inode_dirents")
        X("FS INIT: fs_inode_dirents=%s", fs_inode_dirents)
        if fs_inode_dirents:
          inode_dir, offset = _Dirent.from_str(fs_inode_dirents)
          if offset < len(fs_inode_dirents):
            warning(
                "unparsed text after Dirent: %r", fs_inode_dirents[offset:]
            )
          X("IMPORT INODES:")
          dump_Dirent(inode_dir)
          inodes.load_fs_inode_dirents(inode_dir)
        else:
          X("NO INODE IMPORT")
        X("FileSystem mntE:")
      with self.S:
        with stackattrs(defaults, fs=self):
          dump_Dirent(mntE)
    except Exception as e:
      exception("exception during initial report: %s", e)

  def bg(self, func, *a, **kw):
    ''' Dispatch a function via the FileSystem's Later instance.
    '''
    return self._later.defer(func, *a, **kw)

  def close(self):
    ''' Close the FileSystem.
    '''
    self._sync()
    self.S.close()
    self.S.block_cache = self._old_S_block_cache

  def __str__(self):
    if self.subpath:
      return "<%s S=%s /=%s %r=%s>" % (
          self.__class__.__name__, self.S, self.E, self.subpath, self.mntE
      )
    return "%s(S=%s,/=%r)" % (type(self).__name__, self.S, self.E)

  def __getitem__(self, inum):
    ''' Lookup inode numbers or UUIDs.
    '''
    return self._inodes[inum]

  def __setitem__(self, inum, E):
    ''' Associate a specific inode number with a Dirent.
    '''
    self._inodes.add(E, inum)

  @logexc
  def _sync(self):
    with Pfx("_sync"):
      if defaults.S is None:
        raise RuntimeError("RUNTIME: defaults.S is None!")
      archive = self.archive
      if not self.readonly and archive is not None:
        with self._lock:
          E = self.E
          updated = False
          X("snapshot %r  ...", E)
          E.snapshot()
          X("snapshot: afterwards E=%r", E)
          fs_inode_dirents = self._inodes.get_fs_inode_dirents()
          X("_SYNC: FS_INODE_DIRENTS:")
          dump_Dirent(fs_inode_dirents)
          X("set meta.fs_inode_dirents")
          if fs_inode_dirents.size > 0:
            E.meta['fs_inode_dirents'] = str(fs_inode_dirents)
          else:
            E.meta['fs_inode_dirents'] = ''
          new_state = bytes(E)
          if new_state != self._last_sync_state:
            archive.update(E)
            self._last_sync_state = new_state
            updated = True
        # debugging
        if updated:
          dump_Dirent(E, recurse=False)

  def _resolve(self, path):
    ''' Call paths.resolve and return its result.
    '''
    return resolve(self.mntE, path)

  def _namei2(self, path):
    ''' Look up path. Raise OSError(ENOENT) if missing. Return Dirent, parent.
    '''
    E, P, tail_path = self._resolve(path)
    if tail_path:
      OS_ENOENT("cannot resolve path %r", path)
    return E, P

  def _namei(self, path):
    ''' Look up path. Raise OSError(ENOENT) if missing. Return Dirent.
    '''
    E, _ = self._namei2(path)
    return E

  @locked
  def E2inode(self, E):
    ''' Return the Inode for the supplied Dirent `E`.
    '''
    if E.isindirect:
      E = E.ref
    return self._inodes.add(E)

  def i2E(self, inum):
    ''' Return the Dirent associated with the supplied `inum`.
    '''
    I = self._inodes[inum]
    return I.E

  def open2(self, P, name, flags):
    ''' Open a regular file given parent Dir `P` and `name`,
        allocate FileHandle, return FileHandle index.

        Increments the kernel reference count.
        Wraps self.open.
    '''
    if not P.isdir:
      OS_ENOTDIR("parent (name=%r) not a directory", P.name)
    if name in P:
      if flags & O_EXCL:
        OS_EEXIST("entry %r already exists", name)
      E = P[name]
    elif not flags & O_CREAT:
      OS_ENOENT("no entry named %r", name)
    else:
      E = FileDirent(name)
      P[name] = E
    return self.open(E, flags)

  def open(self, E, flags):
    ''' Open a regular file `E`, allocate FileHandle, return FileHandle index.
        Increments the kernel reference count.
    '''
    for_read = (flags & O_RDONLY) == O_RDONLY or (flags & O_RDWR) == O_RDWR
    for_write = (flags & O_WRONLY) == O_WRONLY or (flags & O_RDWR) == O_RDWR
    for_append = (flags & O_APPEND) == O_APPEND
    for_trunc = (flags & O_TRUNC) == O_TRUNC
    debug(
        "for_read=%s, for_write=%s, for_append=%s", for_read, for_write,
        for_append
    )
    if for_trunc and not for_write:
      OS_EINVAL("O_TRUNC requires O_WRONLY or O_RDWR")
    if for_append and not for_write:
      OS_EINVAL("O_APPEND requires O_WRONLY or O_RDWR")
    if (for_write and not for_append) and self.append_only:
      OS_EINVAL("fs is append_only but no O_APPEND")
    if for_trunc and self.append_only:
      OS_EINVAL("fs is append_only but O_TRUNC")
    if (for_write or for_append) and self.readonly:
      error("fs is readonly")
      OS_EROFS("fs is readonly")
    if E.issym:
      if flags & O_NOFOLLOW:
        OS_ELOOP("open symlink with O_NOFOLLOW")
      OS_EINVAL("open(%s)" % (E,))
    elif not E.isfile:
      OS_EINVAL("open of nonfile: %s" % (E,))
    FH = FileHandle(self, E, for_read, for_write, for_append, lock=self._lock)
    if flags & O_TRUNC:
      FH.truncate(0)
    return self._new_file_handle_index(FH)

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

  @staticmethod
  def access(E, amode, uid=None, gid=None):
    ''' Check access mode `amode` against Dirent `E`.
    '''
    with Pfx("access(E=%r,amode=%s,uid=%r,gid=%d)", E, amode, uid, gid):
      # test the access against the caller's uid/gid
      # pass same in as default file ownership in case there are no metadata
      return E.meta.access(amode, uid, gid, default_uid=uid, default_gid=gid)

  def getxattr(self, inum, xattr_name):
    ''' Get the extended attribute `xattr_name` from `inum`.
    '''
    E = self.i2E(inum)
    xattr_name = Meta.xattrify(xattr_name)
    if xattr_name.startswith(XATTR_VT_PREFIX):
      # process special attribute names
      suffix = xattr_name[len(XATTR_VT_PREFIX):]
      if suffix == 'block':
        return str(E.block).encode()
      OS_EINVAL(
          "getxattr(inum=%s,xattr_name=%r): invalid %r prefixed name", inum,
          xattr_name, XATTR_VT_PREFIX
      )
    xattr = E.meta.getxattr(xattr_name, None)
    if xattr is None:
      ##if xattr_name == 'com.apple.FinderInfo':
      ##  OS_ENOTSUP("inum %d: no xattr %r, pretend not supported", inum, xattr_name)
      if self.is_darwin:
        OS_ENOATTR("inum %d: no xattr %r", inum, xattr_name)
      else:
        OS_ENODATA("inum %d: no xattr %r", inum, xattr_name)
    return xattr

  def removexattr(self, inum, xattr_name):
    ''' Remove the extended attribute named `xattr_name` from `inum`.
    '''
    if self.readonly:
      OS_EROFS("fs is read only")
    E = self.i2E(inum)
    xattr_name = Meta.xattrify(xattr_name)
    if xattr_name.startswith(XATTR_VT_PREFIX):
      OS_EINVAL(
          "removexattr(inum=%s,xattr_name=%r): invalid %r prefixed name", inum,
          xattr_name, XATTR_VT_PREFIX
      )
    meta = E.meta
    try:
      meta.delxattr(xattr_name)
    except KeyError:
      OS_ENOATTR("no such extended attribute: %r", xattr_name)

  def setxattr(self, inum, xattr_name, xattr_value):
    ''' Set the extended attribute `xattr_name` to `xattr_value`
        on inode `inum`.
    '''
    if self.readonly:
      OS_EROFS("fs is read only")
    E = self.i2E(inum)
    xattr_name = Meta.xattrify(xattr_name)
    if not xattr_name.startswith(XATTR_VT_PREFIX):
      # ordinary attribute, set it and return
      E.meta.setxattr(xattr_name, xattr_value)
      return
    # process special attribute names
    with Pfx("%s.setxattr(%d,%r,%r)", self, inum, xattr_name, xattr_value):
      suffix = xattr_name[len(XATTR_VT_PREFIX):]
      with Pfx(suffix):
        if suffix == 'block':
          # update the Dirent's content directly
          if not E.isfile:
            OS_EINVAL("tried to update the data content of a nonfile: %s", E)
          block_s = Meta.xattrify(xattr_value)
          B, offset = parse(block_s)
          if offset < len(block_s):
            OS_EINVAL("unparsed text after trancription: %r", block_s[offset:])
          if not isBlock(B):
            OS_EINVAL("not a Block transcription")
          info("%s: update .block directly to %r", E, str(B))
          E.block = B
          return
        if suffix == 'control':
          argv = shlex.split(xattr_value.decode('utf-8'))
          if not argv:
            OS_EINVAL("no control command")
          op = argv.pop(0)
          with Pfx(op):
            if op == 'cache':
              if argv:
                OS_EINVAL("extra arguments: %r", argv)
              B = E.block
              if B.indirect:
                X("ADD BLOCK CACHE FOR %s", B)
                bm = self.block_cache.get_blockmap(B)
                X("==> BLOCKMAP: %s", bm)
              else:
                X("IGNORE BLOCK CACHE for %s: not indirect", B)
              return
            OS_EINVAL("unrecognised control command")
        OS_EINVAL("invalid %r prefixed name", XATTR_VT_PREFIX)
