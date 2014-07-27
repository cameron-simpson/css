#!/usr/bin/python
#
# Fuse interface to a Store.
# Uses fusepy: https://github.com/terencehonles/fusepy
#       - Cameron Simpson <cs@zip.com.au>
#

from fuse import FUSE, FuseOSError, Operations, LoggingMixIn
from functools import partial
from collections import namedtuple
import errno
import os
from os import O_CREAT, O_RDONLY, O_WRONLY, O_RDWR, O_APPEND, O_TRUNC
import sys
from threading import RLock
from cs.debug import DummyMap, TracingObject
from cs.logutils import X
from cs.obj import O, obj_as_dict
from cs.seq import Seq
from cs.threads import locked
from .block import Block
from .dir import FileDirent, Dir
from .file import File
from .paths import resolve

# records associated with an open file
# TODO: no support for multiple links or path-=open renames
OpenFile = namedtuple('OpenFile', ('path', 'E', 'fp'))

def mount(mnt, E, S):
  ''' Run a FUSE filesystem on `mnt` with Dirent `E` and backing Store `S`.
  '''
  FS = StoreFS(E, S)
  FS._mount(mnt)

class StoreFS(Operations):
  ''' Class providing filesystem operations, suitable for passing
      to a FUSE() constructor.
  '''

  def __init__(self, E, S):
    ''' Initilaise a new FUSE mountpoint.
        mnt: the mountpoint
        dirent: the root directory reference
        S: the Store to hold data
    '''
    O.__init__(self)
    if not E.isdir:
      raise ValueError("not dir Dir: %s" % (E,))
    self.S =S
    self.E = E
    self.do_fsync = False
    self._lock = RLock()
    self._inode_seq = Seq(start=1)
    self._inode_map = {}
    self._path_files = {}
    self._file_handles = []

  def __str__(self):
    return "<StoreFS>"

  def __getattr__(self, attr):
    # debug aid
    X("UNKNOWN ATTR: StoreFS.__getattr__: attr=%r", attr)
    def attrfunc(*a, **kw):
      X("UNKNOWN ATTR: %s(a=%r,kw=%r)", attr, a, kw)
      raise RuntimeError(attr)
    X("%s ==> %s", attr, attrfunc)
    return attrfunc

  def _mount(self, root):
    ''' Attach this StoreFS to the specified path `root`.
        Return the controlling FUSE object.
    '''
    return TracingObject(FUSE(self, root, foreground=True, nothreads=True, debug=True))

  def _resolve(self, path):
    ''' Call cs.venti.paths.resolve and return its result.
    '''
    return resolve(self.E, path)

  def _namei2(self, path):
    ''' Look up path. Raise FuseOSError(ENOENT) if missing. Return Dirent, parent.
    '''
    E, P, tail_path = self._resolve(path)
    if tail_path:
      X("_namei2: NOT FOUND: %r; tail_path=%r", path, tail_path)
      raise FuseOSError(errno.ENOENT)
    return E, P

  def _namei(self, path):
    ''' Look up path. Raise FuseOSError(ENOENT) if missing. Return Dirent.
    '''
    E, P = self._namei2(path)
    return E

  @locked
  def _ino(self, path):
    ''' Return an inode number for a path, allocating one of necessary.
    '''
    path = '/'.join( [ word for word in path.split('/') if len(word) ] )
    if path not in self._inode_map:
      self._inode_map[path] = self._inode_seq.next()
    return self._inode_map[path]

  @locked
  def _fh(self, fd):
    return self._file_handles[fd]

  @locked
  def _new_file_descriptor(self, file_handle):
    ''' Allocate a new file descriptor for a `file_handle`.
        TODO: linear allocation cost, may need recode if things get
          busy; might just need a list of released fds for reuse.
    '''
    fhs = self._file_handles
    for i in range(len(fhs)):
      if fhs[i] is None:
        fhs[i] = file_handle
        return i
    fhs.append(file_handle)
    return len(fhs) - 1

  ##############
  # FUSE support methods.

  def access(self, path, amode):
    X("access(path=%s, mode=%s)", path, amode)
    E = self._namei(path)
    if not E.meta.access(amode):
      X("raise EACCES")
      raise FuseOSError(errno.EACCES)
    X("%s.access: return 0", self)
    return 0

  def chmod(self, path, mode):
    X("chmod(%r, %o)...", path, mode)
    E = self._namei(path)
    E.meta.chmod(mode)

  def create(self, path, mode, fi=None):
    X("CREATE: path=%r, mode=%o, fi=%r", path, mode, fi)
    if fi is not None:
      raise RuntimeError("WHAT TO DO IF FI NOT NONE: fi=%r" % (fi,))
    fd = self.open(path, O_CREAT|O_TRUNC|O_WRONLY)
    X("TODO: create: apply mode (0o%o) to self._fh[%d]", mode, fd)
    return fd

  def getattr(self, path, fh=None):
    X("getattr: %s ...", path)
    try:
      E = self._namei(path)
    except FuseOSError as e:
      X("getattr: FuseOSError: %s", e)
      raise
    X("getattr: %s => %s", path, E)
    if fh is not None:
      X("fh=%r", fh)
    d = obj_as_dict(E.meta.stat(), 'st_')
    d['st_dev'] = 16777218
    d['st_ino'] = self._ino(path)
    d['st_dev'] = 1701
    d['st_atime'] = float(d['st_atime'])
    d['st_ctime'] = float(d['st_ctime'])
    d['st_mtime'] = float(d['st_mtime'])
    d['st_nlink'] = 10
    X("getattr: d=%r", d)
    return d

  def mkdir(self, path, mode):
    X("mkdir(path=%s, mode=%o)", path, mode)
    E, P, tail_path = self._resolve(path)
    if not tail_path:
      X("mkdir: file exists already")
      raise FuseOSError(errno.EEXIST)
    if len(tail_path) > 1:
      X("mkdir(%r): multiple missing path components: %r", path, tail_path)
      raise FuseOSError(errno.ENOENT)
    assert len(tail_path) == 1
    X("mkdir: new dir, basename %r", tail_path)
    if not E.isdir:
      X("mkdir: parent (%r) not a directory, raising ENOTDIR", E.name)
      raise FuseOSError(errno.ENOTDIR)
    base = tail_path[0]
    newE = Dir(path, parent=E)
    E[base] = newE
    E = newE
    E.meta.chmod(mode&0o7777)

  @locked
  def open(self, path, flags):
    ''' Obtain a file descriptor open on `path`.
    '''
    X("open(path=%r, flags=%o)...", path, flags)
    do_create = flags & O_CREAT
    for_read = (flags & O_RDONLY) == O_RDONLY or (flags & O_RDWR) == O_RDWR
    for_write = (flags & O_WRONLY) == O_WRONLY or (flags & O_RDWR) == O_RDWR
    for_append = (flags & O_APPEND) == O_APPEND
    X("open(path=%r,..): do_create=%s for_read=%s, for_write=%s, for_append=%s",
      path, do_create, for_read, for_write, for_append)
    E, P, tail_path = self._resolve(path)
    if len(tail_path) > 0 and not do_create:
      X("open(%r): no do_create, raising ENOENT", path)
      raise FuseOSError(errno.ENOENT)
    if len(tail_path) > 1:
      X("open(%r): multiple missing path components: %r", path, tail_path)
      raise FuseOSError(errno.ENOENT)
    if len(tail_path) == 1:
      X("open: new file, basename %r", tail_path)
      if not E.isdir:
        X("open: parent (%r) not a directory, raising ENOTDIR", E.name)
        raise FuseOSError(errno.ENOTDIR)
      base = tail_path[0]
      newE = FileDirent(path)
      E[base] = newE
      E = newE
    else:
      X("open: file exists already")
    fh = FileHandle(self, path, E, for_read, for_write, for_append)
    X("open(%r): fh=%s", path, fh)
    fd = self._new_file_descriptor(fh)
    X("open(%r): fd=%s", path, fd)
    return fd

  def opendir(self, path):
    X("opendir(path=%r)...", path)
    E = self._namei(path)
    fd = self._new_file_descriptor(E)
    X("opendir: return %d", fd)
    return fd

  def read(self, path, size, offset, fd):
    X("READ: path=%r, size=%d, offset=%d, fd=%r", path, size, offset, fd)
    chunks = []
    while size > 0:
      data = self._fh(fd).read(offset, size)
      if len(data) == 0:
        break
      chunks.append(data)
      offset += len(data)
      size -= len(data)
    return b''.join(chunks)

  def readdir(self, path, *a, **kw):
    X("READDIR: path=%r, a=%r, kw=%r", path, a, kw)
    E = self._namei(path)
    if not E.isdir:
      raise FuseOSError(errno.ENOTDIR)
    return ['.', '..'] + list(E.keys())

  def readlink(self, path):
    E = self._namei(path)
    # no symlinks yet
    raise FuseOSError(errno.EINVAL)

  def release(self, path, fd):
    X("release open file path=%r fd=%r...", path, fd)
    fh = self._fh(fd)
    if fh is None:
      error("release open file fd=%r: handle is None!", fd)
    else:
      fh.close()
    return 0

  def releasedir(self, path, fd):
    X("releasedir path=%r fd=%r...", path, fd)
    fh = self._fh(fd)
    if fh is None:
      error("releasedir fd=%r: handle is None!", fd)
    else:
      X("releasedir fd=%r: OK %s", fd, fh)
    return 0

  def statfs(self, path):
    X("statsfs(%s)", path)
    st = os.statvfs(".")
    X("statsfs(%s) ==> %r", path, st)
    d = {}
    for f in dir(st):
      if f.startswith('f_'):
        X("statvfs: .%s = %r", f, getattr(st, f))
        d[f] = getattr(st, f)
      else:
        X("statvfs: skip %s", f)
    return d

  def write(self, path, data, offset, fd):
    X("WRITE: path=%r, data=%r, offset=%d, fd=%r", path, data, offset, fd)
    return self._fh(fd).write(data, offset)

  def flush(self, path, fh):
    X("FLUSH: path=%r, fh=%r", path, fh)

  def fsync(self, path, datasync, fh):
    X("FSYNC: path=%r, datasync=%d, fh=%r", path, datasync, fh)
    if self.do_fsync:
      self._fh(fd).sync()

class FileHandle(O):
  ''' Filesystem state for open files.
  '''

  def __init__(self, fs, path, E, for_read, for_write, for_append):
    O.__init__(self)
    self.fs = fs
    self.path = path
    self.Eopen = E.open()
    self.offset = 0
    self.for_read = for_read
    self.for_write = for_write
    self.for_append = for_append

  def write(self, data, offset):
    fp = self.Eopen._open_file
    X("FileHandle.write: fp=<%s>%r", fp.__class__, fp)
    with fp:
      fp.seek(offset)
      written = fp.write(data)
    return written

  def read(self, offset, size):
    X("FileHandle.read: offset=%r, size=%r", offset, size)
    if size < 1:
      raise ValueError("FileHandle.read: size(%d) < 1" % (size,))
    fp = self.Eopen._open_file
    X("FileHandle.read: fp=<%s>%r", fp.__class__, fp)
    with fp:
      fp.seek(offset)
      data = fp.read(size)
    return data

  def sync(self):
    self.Eopen.sync()

  def close(self):
    self.Eopen.close()

if __name__ == '__main__':
  from cs.venti.vtfuse_tests import selftest
  selftest(sys.argv)
