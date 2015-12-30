#!/usr/bin/python
#
# Fuse interface to a Store.
# Uses fusepy: https://github.com/terencehonles/fusepy
#       - Cameron Simpson <cs@zip.com.au>
#

from fuse import FUSE, FuseOSError, Operations, LoggingMixIn, fuse_get_context
from functools import partial
from collections import namedtuple
from logging import getLogger, FileHandler, Formatter
import errno
import os
from os import O_CREAT, O_RDONLY, O_WRONLY, O_RDWR, O_APPEND, O_TRUNC
from os.path import basename
from pprint import pformat
import sys
from threading import Thread
import time
from cs.debug import DummyMap, TracingObject
from cs.logutils import X, XP, debug, info, warning, error, Pfx, DEFAULT_BASE_FORMAT
from cs.obj import O, obj_as_dict
from cs.py.func import funccite, funcname
from cs.queues import IterableQueue
from cs.seq import Seq
from cs.threads import locked
from .archive import strfor_Dirent, write_Dirent_str
from .block import Block
from .debug import dump_Dirent
from .dir import Dir, FileDirent, SymlinkDirent
from .file import File
from .meta import NOUSERID, NOGROUPID
from .paths import resolve

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
  FS._mount(mnt)

def trace_method(method):
  ##fname = '.'.join( (method.__module__, funccite(method)) )
  fname = '.'.join( (method.__module__, funcname(method)) )
  def traced_method(self, *a, **kw):
    citation = fname
    if a:
      citation += " " + pformat(a, depth=1)
    if kw:
      citation += " " + pformat(kw, depth=2)
    with Pfx(citation):
      ctx = fuse_get_context()
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
        self.logQ.put( (self.log.exception, citation, elapsed, ctx, "%s %s", type(e), e) )
        raise
      else:
        elapsed = time.time() - time0
        self.logQ.put( (self.log.info, citation, elapsed, ctx, "=> %r", result) )
        return result
  traced_method.__name__ = 'trace(%s)' % (fname,)
  return traced_method

def log_traces_queued(Q):
  for logcall, citation, elapsed, ctx, msg, *a in Q:
    logcall("%fs uid=%s/gid=%s/pid=%s %s " + msg,
            elapsed, ctx[0], ctx[1], ctx[2], citation ,*a)

class StoreFS(Operations):
  ''' Class providing filesystem operations, suitable for passing
      to a FUSE() constructor.
  '''

  def __init__(self, E, S, syncfp=None, subpath=None):
    ''' Initialise a new FUSE mountpoint.
        `E`: the root directory reference
        `S`: the backing Store
        `syncfp`: if not None, a file to which to write sync lines
        `subpath`: relative path to mount Dir
    '''
    O.__init__(self)
    if not E.isdir:
      raise ValueError("not dir Dir: %s" % (E,))
    self.S = S
    self.E = E
    self.subpath = subpath
    if subpath:
      # locate subdirectory to display at mountpoint
      mntE, P, tail_path = resolve(E, subpath)
      if tail_path:
        raise ValueError("subpath %r does not resolve", subpath)
      if not mntE.isdir:
        raise ValueError("subpath %r is not a directory", subpath)
      self.mntE = mntE
    else:
      self.mntE = E
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
    self._inode_seq = Seq(start=1)
    self._inode_map = {}
    self._path_files = {}
    self._file_handles = []

  def __str__(self):
    if self.subpath:
      return "<StoreFS S=%s /=%s %r=%s>" % (self.S, self.E, self.subpath, self.mntE)
    else:
      return "<StoreFS S=%s /=%s>" % (self.S, self.E)

  def __del__(self):
    self.logQ.close()

  def __getattr__(self, attr):
    # debug aid
    warning("UNKNOWN ATTR: StoreFS.__getattr__: attr=%r", attr)
    def attrfunc(*a, **kw):
      warning("CALL UNKNOWN ATTR: %s(a=%r,kw=%r)", attr, a, kw)
      raise RuntimeError(attr)
    return attrfunc

  @trace_method
  def _sync(self):
    if self.syncfp is not None:
      with self._lock:
        text = strfor_Dirent(self.E)
        last_text = self._syncfp_last_dirent_text
        if last_text is not None and text == last_text:
          text = None
      if text is not None:
        write_Dirent_str(self.syncfp, text, etc=self.E.name)
        self._syncfp_last_dirent_text = text
        dump_Dirent(self.E, recurse=True) # debugging

  def _mount(self, root):
    ''' Attach this StoreFS to the specified path `root`.
        Return the controlling FUSE object.
    '''
    return FUSE(self, root, foreground=True, nothreads=True, debug=False)
    ##return TracingObject(FUSE(self, root, foreground=True, nothreads=True, debug=False))

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

  def _Estat(self, E):
    ''' Stat a Dirent, return a dict with useful st_* fields.
    '''
    d = obj_as_dict(E.meta.stat(), 'st_')
    d['st_dev'] = 16777218
    d['st_dev'] = 1701
    d['st_atime'] = float(d['st_atime'])
    d['st_ctime'] = float(d['st_ctime'])
    d['st_mtime'] = float(d['st_mtime'])
    d['st_nlink'] = 10
    if d['st_uid'] == NOUSERID:
      d['st_uid'] = self._fs_uid
    if d['st_gid'] == NOGROUPID:
      d['st_gid'] = self._fs_gid
    return d

  @locked
  def _ino(self, path):
    ''' Return an inode number for a path, allocating one of necessary.
    '''
    path = '/'.join( [ word for word in path.split('/') if len(word) ] )
    if path not in self._inode_map:
      self._inode_map[path] = self._inode_seq.next()
    return self._inode_map[path]

  @locked
  def _fh(self, fhndx):
    try:
      fh = self._file_handles[fhndx]
    except IndexError:
      fh = None
    if fh is None:
      error("cannot look up FileHandle index %r", fhndx)
    return fh

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

  ##############
  # FUSE support methods.

  def _Eaccess(self, E, amode):
    with Pfx("_Eaccess(E=%r, amode=%s)", E, amode):
      ctx_uid, ctx_gid, ctx_pid = ctx = fuse_get_context()
      # test the access against the caller's uid/gid
      # pass same in as default file ownership in case there are no metadata
      return E.meta.access(amode, ctx_uid, ctx_gid,
                           default_uid=ctx_uid, default_gid=ctx_gid)

  @trace_method
  def access(self, path, amode):
    E = self._namei(path)
    if not self._Eaccess(E, amode):
      raise FuseOSError(errno.EACCES)
    return 0

  @trace_method
  def chmod(self, path, mode):
    E, P = self._namei2(path)
    E.meta.chmod(mode)
    if P:
      P.change()

  @trace_method
  def chown(self, path, uid, gid):
    E, P = self._namei2(path)
    if P:
      P.change()
    M = E.meta
    if uid >= 0 and uid != self._fs_uid:
      M.uid = uid
    if gid >= 0 and gid != self._fs_gid:
      M.gid = gid

  @trace_method
  def create(self, path, mode, fi=None):
    if fi is not None:
      raise RuntimeError("WHAT TO DO IF fi IS NOT NONE? fi=%r" % (fi,))
    fhndx = self.open(path, O_CREAT|O_TRUNC|O_WRONLY)
    warning("TODO: create: apply mode (0o%o) to self._fh[%d]", mode, fhndx)
    return fhndx

  @trace_method
  def destroy(self, path):
    # TODO: catch path == '/', indicates umount?
    self._sync()

  @trace_method
  def fgetattr(self, *a, **kw):
    E = self._namei(path)
    if fh is not None:
      ##X("fh=%s", fh)
      pass
    return self._Estat(E)

  @trace_method
  def flush(self, path, datasync, fhndx):
    self._fh(fhndx).flush()

  @trace_method
  def flush(self, path, fh):
    ##info("FLUSH: NOOP?")
    pass

  @trace_method
  def fsync(self, path, datasync, fh):
    if self.do_fsync:
      self._fh(fhndx).flush()

  @trace_method
  def fsyncdir(self, path, datasync, fh):
    return 0

  @trace_method
  def ftruncate(self, path, length, fhndx):
    fh = self._fh(fhndx)
    fh.truncate(length)

  @trace_method
  def getattr(self, path, fh=None):
    E = self._namei(path)
    st = self._Estat(E)
    st['st_ino'] = self._ino(path)
    return st

  @trace_method
  def getxattr(self, path, name, position=0):
    E = self._namei(path)
    meta = E.meta
    try:
      xattr = meta.xattrs[name]
    except KeyError:
      raise FuseOSError(errno.ENOATTR)
    return xattr

  @trace_method
  def listxattr(self, path):
    E = self._namei(path)
    return list(E.meta.xattrs.keys())

  @trace_method
  def lock(self, path, fh, op, struct_lock):
    try:
      ct = struct_lock.contents
    except AttributeError:
      # using vanilla fuse.py without "struct lock" patch
      pass
    else:
      XP("dir(struct_lock) = %r", dir(struct_lock))
      XP("  contents = %r", struct_lock.contents)
      XP("  dircontents) = %r", dir(struct_lock.contents))
      ct = struct_lock.contents
      X("AAAAAAAA")
      X("  type=%s", ct.type)
      X("  start=%s", ct.start)
      X("  end=%s", ct.end)
      X("  owner=%s", ct.owner)
      X("  pid=%s", ct.pid)
      X("BBBBBBBB")
      # TODO: only seem to see LOCK_UN, never other operations
      ##bs = []
      ##while thing > 0:
      ##  bs.append(thing % 256)
      ##  thing //= 256
      ##XP("thing bytes reversed: %r", ' '.join([ "0x%02x" % b for b in bs ]))
    raise FuseOSError(errno.ENOTSUP)

  @trace_method
  def mkdir(self, path, mode):
    E, P, tail_path = self._resolve(path)
    if not tail_path:
      error("file exists")
      raise FuseOSError(errno.EEXIST)
    if len(tail_path) != 1:
      error("expected exactly 1 missing path component, got: %r", tail_path)
      raise FuseOSError(errno.ENOENT)
    if not E.isdir:
      error("parent (%r) not a directory, raising ENOTDIR", E.name)
      raise FuseOSError(errno.ENOTDIR)
    base = tail_path[0]
    newE = Dir(base, parent=E)
    E[base] = newE
    E = newE
    E.meta.chmod(mode & 0o7777)

  @trace_method
  def mknod(self, path, mode, dev):
    raise FuseOSError(errno.ENOTSUP)

  @trace_method
  @locked
  def open(self, path, flags):
    ''' Obtain a FileHandle open on `path`, return its index.
    '''
    do_create = flags & O_CREAT
    do_trunc = flags & O_TRUNC
    for_read = (flags & O_RDONLY) == O_RDONLY or (flags & O_RDWR) == O_RDWR
    for_write = (flags & O_WRONLY) == O_WRONLY or (flags & O_RDWR) == O_RDWR
    for_append = (flags & O_APPEND) == O_APPEND
    debug("do_create=%s for_read=%s, for_write=%s, for_append=%s",
          do_create, for_read, for_write, for_append)
    E, P, tail_path = self._resolve(path)
    if len(tail_path) > 0 and not do_create:
      error("no do_create, raising ENOENT")
      raise FuseOSError(errno.ENOENT)
    if len(tail_path) > 1:
      error("multiple missing path components: %r", tail_path)
      raise FuseOSError(errno.ENOENT)
    if len(tail_path) == 1:
      debug("open: new file, basename %r", tail_path)
      if not E.isdir:
        error("parent (%r) not a directory, raising ENOTDIR", E.name)
        raise FuseOSError(errno.ENOTDIR)
      base = tail_path[0]
      newE = FileDirent(base)
      E[base] = newE
      E = newE
    else:
      debug("file exists already")
      pass
    fh = FileHandle(self, path, E, for_read, for_write, for_append)
    if do_trunc:
      fh.truncate(0)
    fhndx = self._new_file_handle_index(fh)
    if P:
      P.change()
    return fhndx

  @trace_method
  def opendir(self, path):
    E = self._namei(path)
    if not E.isdir:
      raise FuseOSError(errno.ENOTDIR)
    fhndx = self._new_file_handle_index(E)
    return fhndx

  @trace_method
  def read(self, path, size, offset, fhndx):
    chunks = []
    while size > 0:
      data = self._fh(fhndx).read(offset, size)
      if len(data) == 0:
        break
      chunks.append(data)
      offset += len(data)
      size -= len(data)
    return b''.join(chunks)

  @trace_method
  def readdir(self, path, fhndx):
    if self._fh(fhndx) is None:
      warning("unknown fdndx %r", fhndx)
    E = self._namei(path)
    if not E.isdir:
      raise FuseOSError(errno.ENOTDIR)
    try:
      entries = list(E.keys())
    except IOError as e:
      error("IOError: %s", e)
      raise FuseOSError(errno.EIO)
    return ['.', '..'] + entries

  @trace_method
  def readlink(self, path):
    E = self._namei(path)
    if not E.issym:
      raise FuseOSError(errno.EINVAL)
    return E.pathref

  @trace_method
  def release(self, path, fhndx):
    fh = self._fh(fhndx)
    if fh is None:
      error("no matching FileHandle!")
      raise FuseOSError(errno.EINVAL)
    fh.close()
    return 0

  @trace_method
  def releasedir(self, path, fhndx):
    fh = self._fh(fhndx)
    if fh is None:
      error("handle is None!")
    return 0

  @trace_method
  def removexattr(self, path, name):
    raise FuseOSError(errno.ENOATTR)

  @trace_method
  def rename(self, oldpath, newpath):
    E1base = basename(oldpath)
    E1, P1, tail_path = self._resolve(oldpath)
    if tail_path:
      raise FuseOSError(errno.ENOENT)
    if not self._Eaccess(P1, os.X_OK|os.W_OK):
      raise FuseOSError(errno.EPERM)
    E2base = basename(newpath)
    E2, P2, tail_path = self._resolve(newpath)
    if len(tail_path) > 1:
      raise FuseOSError(errno.ENOENT)
    if len(tail_path) == 1:
      P2 = E2
      E2 = None
    if not self._Eaccess(P2, os.X_OK|os.W_OK):
      raise FuseOSError(errno.EPERM)
    del P1[E1base]
    E1.name = E2base
    P2[E2base] = E1

  @trace_method
  def rmdir(self, path):
    Ebase = basename(path)
    E, P, tail_path = self._resolve(path)
    if tail_path:
      raise FuseOSError(errno.ENOENT)
    if not E.isdir:
      raise FuseOSError(errno.EDOTDIR)
    if not self._Eaccess(P, os.W_OK|os.X_OK):
      raise FuseOSError(errno.EPERM)
    if E.entries:
      raise FuseOSError(errno.ENOTEMPTY)
    del P[Ebase]

  @trace_method
  def setxattr(self, path, name, value, options, position=0):
    sane = True
    if position != 0:
      warning("position != 0: %r", position)
      sane = False
    if options & ~(XATTR_CREATE|XATTR_REPLACE):
      warning("unsupported options (beyond XATTR_CREATE(0x%02d) and XATTR_REPLACE(0x%02d) in 0x%02x"
              % (XATTR_CREATE, XATTR_REPLACE, options))
    if not sane:
      raise FuseOSError(errno.EINVAL)
    E = self._namei(path)
    xattrs = E.meta.xattrs
    if options & XATTR_CREATE and name in xattrs:
      raise FuseOSError(errno.EEXIST)
    if options & XATTR_REPLACE and name not in xattrs:
      raise FuseOSError(errno.ENOATTR)
    xattrs[name] = value

  @trace_method
  def statfs(self, path):
    st = os.statvfs(".")
    d = {}
    for f in dir(st):
      if f.startswith('f_'):
        d[f] = getattr(st, f)
    return d

  @trace_method
  def symlink(self, target, source):
    E, P, tail_path = self._resolve(target)
    # target must not exist, therefore there should be unresolved path elements
    if not tail_path:
      # we expect the path to not fully resole, otherwise the object already exists
      raise FuseOSError(errno.EEXIST)
    # if there are more than 1 unresolved components then some
    # ancestor of target is missing
    if len(tail_path) > 1:
      XP("tail_path = %r", tail_path)
      raise FuseOSError(errno.ENOENT)
    # the final component must be a directory in order to create the new symlink
    if not E.isdir:
      raise FuseOSError(errno.ENOTDIR)
    name, = tail_path
    E[name] = SymlinkDirent(name, {'pathref': source})

  @trace_method
  def sync(self, *a, **kw):
    pass

  @trace_method
  def truncate(self, path, length, fh=None):
    E, P = self._namei2(path)
    if not self._Eaccess(E, os.W_OK):
      raise FuseOSError(errno.EPERM)
    E.truncate(length)
    P.change()

  @trace_method
  def unlink(self, path):
    Ebase = basename(path)
    E, P, tail_path = self._resolve(path)
    if tail_path:
      raise FuseOSError(errno.ENOENT)
    if E.isdir:
      raise FuseOSError(errno.EISDIR)
    if not self._Eaccess(P, os.W_OK|os.X_OK):
      raise FuseOSError(errno.EPERM)
    del P[Ebase]

  @trace_method
  def utimens(self, path, times):
    atime, mtime = times
    E, P = self._namei2(path)
    M = E.meta
    ## we do not do atime ## M.atime = atime
    M.mtime = mtime
    if P:
      P.change()

  def write(self, path, data, offset, fhndx):
    return self._fh(fhndx).write(data, offset)

class FileHandle(O):
  ''' Filesystem state for open files.
  '''

  def __init__(self, fs, path, E, for_read, for_write, for_append):
    O.__init__(self)
    self.fs = fs
    self.log = fs.log
    self.logQ = fs.logQ
    self.path = path
    self.E = E
    self.Eopen = E.open()
    self.for_read = for_read
    self.for_write = for_write
    self.for_append = for_append

  def __str__(self):
    return "<FileHandle %r %s>" % (self.path, self.E)

  def write(self, data, offset):
    fp = self.Eopen._open_file
    with fp:
      fp.seek(offset)
      written = fp.write(data)
    self.E.touch()
    return written

  def read(self, offset, size):
    if size < 1:
      raise ValueError("FileHandle.read: size(%d) < 1" % (size,))
    fp = self.Eopen._open_file
    with fp:
      fp.seek(offset)
      data = fp.read(size)
    return data

  @trace_method
  def truncate(self, length):
    self.E.touch()
    self.Eopen._open_file.truncate(length)

  @trace_method
  def flush(self):
    self.E.touch()
    self.Eopen.flush()

  @trace_method
  def close(self):
    self.E.touch()
    self.Eopen.close()

if __name__ == '__main__':
  from cs.venti.vtfuse_tests import selftest
  selftest(sys.argv)
