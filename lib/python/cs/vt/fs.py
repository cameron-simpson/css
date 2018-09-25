#!/usr/bin/env python3
#
# Filesystem layer for Dirs.
# This is used by systems like FUSE.
# - Cameron Simpson <cs@cskk.id.au>

''' Filesystem semantics for a Dir.
'''

import errno
import os
from os import O_CREAT, O_RDONLY, O_WRONLY, O_RDWR, O_APPEND, O_TRUNC, O_EXCL
from threading import Lock, RLock
from types import SimpleNamespace as NS
from uuid import UUID
from cs.excutils import logexc
from cs.logutils import error, warning, debug
from cs.pfx import Pfx
from cs.range import Range
from cs.threads import locked
from cs.x import X
from . import defaults
from .dir import _Dirent, Dir, FileDirent
from .debug import dump_Dirent
from .parsers import scanner_from_filename, scanner_from_mime_type
from .paths import resolve
from .transcribe import Transcriber, mapping_transcriber

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
    self._lock = lock
    E.open()

  def __str__(self):
    fhndx = getattr(self, 'fhndx', None)
    return "<FileHandle:fhndx=%d:%s>" % (fhndx, self.E,)

  def write(self, data, offset):
    ''' Write data to the file.
    '''
    f = self.E.open_file
    with f:
      with self._lock:
        if self.for_append and offset != len(f):
          error("%s: file open for append but offset(%s) != length(%s)",
                f, offset, len(f))
          raise OSError(errno.EFAULT)
        f.seek(offset)
        written = f.write(data)
    self.E.touch()
    return written

  def read(self, size, offset):
    ''' Read data from the file.
    '''
    if size < 1:
      raise ValueError("FileHandle.read: size(%d) < 1" % (size,))
    ##f = self.E.open_file
    ##X("f = %s %s", type(f), f)
    ##X("f.read = %s %s", type(f.read), f.read)
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
    self.E.flush(scanner)
    ## no touch, already done by any writes
    X("FileHandle.Flush DONE")

  def close(self):
    ''' Close the file, mark its parent directory as changed.
    '''
    self.E.close()
    self.E.parent.changed = True

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
  '''

  transcribe_prefix = 'Ino'

  def __init__(self, inum, E, refcount=0):
    NS.__init__(self)
    self.inum = inum
    self.E = E
    self.refcount = refcount

  def transcribe_inner(self, T, fp):
    return transcribe_mapping({
        'refcount': self.refcount,
        'E': self.E,
    }, fp, T=T)

  @classmethod
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    if prefix != self.transcribe_prefix:
      raise ValueError(
          "expected prefix=%r, got: %r"
          % (self.transcribe_prefix, prefix,))
    m, offset = parse_mapping(s, offset, stopchar=stopchar, T=T)
    return cls(m), offset

class Inodes(object):
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
    self.fs = fs                # main filesystem
    self._allocated = Range()   # range of allocated inode numbers
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
      X("  name=%r, E=%s", name, E)
      with Pfx(name):
        uuid_s, refcount_s = name.split(':')[:2]
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
    X("ALLOCATED INUM %d, allocated=%s", inum, allocated)
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
              % (inum, I))
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

class FileSystem(object):
  ''' The core filesystem functionality supporting FUSE operations
      and in principle other filesystem-like access.

      See the cs.vtfuse module for the StoreFS_LLFUSE class (aliased
      as StoreFS) and associated mount function which presents a
      FileSystem as a FUSE mount.

      TODO: medium term: see if this can be made into a VFS layer
      to support non-FUSE operation, for example a VT FTP client
      or the like.
  '''

  def __init__(
      self, E,
      *,
      S=None,
      archive=None,
      subpath=None,
      readonly=None,
      append_only=False,
      show_prev_dirent=False
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
    S.open()
    if readonly is None:
      readonly = S.readonly
    self.E = E
    self.S = S
    self.archive = archive
    if archive is None:
      self._last_sync_state = None
    else:
      self._last_sync_state = archive.strfor_Dirent(E)
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
    self.device_id = -1
    self._fs_uid = os.geteuid()
    self._fs_gid = os.getegid()
    self._lock = S._lock
    self._path_files = {}
    self._file_handles = []
    inodes = self._inodes = Inodes(self)
    self[1] = mntE
    with Pfx("fs_inode_dirents"):
      fs_inode_dirents = E.meta.get("fs_inode_dirents")
      X("FS INIT: fs_inode_dirents=%s", fs_inode_dirents)
      if fs_inode_dirents:
        inode_dir, offset = _Dirent.from_str(fs_inode_dirents)
        if offset < len(fs_inode_dirents):
          warning("unparsed text after Dirent: %r", fs_inode_dirents[offset:])
        X("IMPORT INODES:")
        dump_Dirent(inode_dir)
        inodes.load_fs_inode_dirents(inode_dir)
      else:
        X("NO INODE IMPORT")
    with defaults.stack('fs', self):
      X("FileSystem mntE:")
      dump_Dirent(mntE)

  def close(self):
    ''' Close the FileSystem.
    '''
    self._sync()
    self.S.close()

  def __str__(self):
    if self.subpath:
      return "<%s S=%s /=%s %r=%s>" % (
          self.__class__.__name__,
          self.S, self.E, self.subpath, self.mntE
      )
    return "<%s S=%s /=%s>" % (self.__class__.__name__, self.S, self.E)

  def __getitem__(self, inum):
    ''' Lookup inode numbers or UUIDs.
    '''
    I = self._inodes[inum]
    X("__getitem__(%d)=>%s", inum, I)
    return I

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
          X("snapshot %s ...", E)
          E.snapshot()
          X("snapshot: afterwards E=%s", E)
          fs_inode_dirents = self._inodes.get_fs_inode_dirents()
          X("_SYNC: FS_INODE_DIRENTS:")
          dump_Dirent(fs_inode_dirents)
          X("set meta.fs_inode_dirents")
          if fs_inode_dirents.size > 0:
            E.meta['fs_inode_dirents'] = str(fs_inode_dirents)
          else:
            E.meta['fs_inode_dirents'] = ''
          new_state = archive.strfor_Dirent(E)
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
      raise OSError(errno.ENOENT)
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
    E0 = E
    if E.isindirect:
      E = E.ref
    return self._inodes.add(E)

  def i2E(self, inum):
    ''' Return the Dirent associated with the supplied `inum`.
    '''
    I = self._inodes[inum]
    X("inum %s => %r", inum, I)
    return I.E

  def open2(self, P, name, flags):
    ''' Open a regular file given parent Dir `P` and `name`,
        allocate FileHandle, return FileHandle index.

        Increments the kernel reference count.
        Wraps self.open.
    '''
    if not P.isdir:
      error("parent (name=%r) not a directory, raising ENOTDIR", P.name)
      raise OSError(errno.ENOTDIR)
    if name in P:
      if flags & O_EXCL:
        raise OSError(errno.EEXIST)
      E = P[name]
    elif not flags & O_CREAT:
      raise OSError(errno.ENOENT)
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
    debug("for_read=%s, for_write=%s, for_append=%s",
          for_read, for_write, for_append)
    if for_trunc and not for_write:
      error("O_TRUNC requires O_WRONLY or O_RDWR")
      raise OSError(errno.EINVAL)
    if for_append and not for_write:
      error("O_APPEND requires O_WRONLY or O_RDWR")
      raise OSError(errno.EINVAL)
    if (for_write and not for_append) and self.append_only:
      error("fs is append_only but no O_APPEND")
      raise OSError(errno.EINVAL)
    if for_trunc and self.append_only:
      error("fs is append_only but O_TRUNC")
      raise OSError(errno.EINVAL)
    if (for_write or for_append) and self.readonly:
      error("fs is readonly")
      raise OSError(errno.EROFS)
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

  def access(self, E, amode, uid=None, gid=None):
    ''' Check access mode `amode` against Dirent `E`.
    '''
    with Pfx("access(E=%r,amode=%s,uid=%r,gid=%d)", E, amode, uid, gid):
      # test the access against the caller's uid/gid
      # pass same in as default file ownership in case there are no metadata
      return E.meta.access(amode, uid, gid,
                            default_uid=uid, default_gid=gid)
