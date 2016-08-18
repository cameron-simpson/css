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
from threading import Thread, RLock
import time
from cs.debug import DummyMap, TracingObject
from cs.lex import texthexify, untexthexify
from cs.logutils import X, XP, debug, info, warning, error, Pfx, DEFAULT_BASE_FORMAT
from cs.obj import O, obj_as_dict
from cs.py.func import funccite, funcname
from cs.queues import IterableQueue
from cs.range import Range
from cs.serialise import put_bs, get_bs, put_bsdata, get_bsdata
from cs.threads import locked
from .archive import strfor_Dirent, write_Dirent_str
from .block import Block
from .debug import dump_Dirent
from .dir import Dir, FileDirent, SymlinkDirent, HardlinkDirent, D_FILE_T, decode_Dirent
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
        dolog(self.log.exception, citation, elapsed, ctx, "%s %s", type(e), e)
        raise
      else:
        elapsed = time.time() - time0
        self.logQ.put( (self.log.info, citation, elapsed, ctx, "=> %r", result) )
        return result
  traced_method.__name__ = 'trace(%s)' % (fname,)
  return traced_method

def log_traces_queued(Q):
  for logcall, citation, elapsed, ctx, msg, *a in Q:
    dolog(logcall, citation, elapsed, ctx, msg, *a)

def dolog(logcall, citation, elapsed, ctx, msg, *a):
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
    self._path_files = {}
    self._file_handles = []
    self._inodes = Inodes(self, E.meta.get('fs_inode_data'))

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
        # update the inode table state
        self.E.meta['fs_inode_data'] = texthexify(self._inodes.encode())
        text = strfor_Dirent(self.E)
        last_text = self._syncfp_last_dirent_text
        if last_text is not None and text == last_text:
          text = None
      if text is not None:
        write_Dirent_str(self.syncfp, text, etc=self.E.name)
        self._syncfp_last_dirent_text = text
        # debugging
        dump_Dirent(self.E, recurse=False)
        dump_Dirent(self._inodes._hardlinks_dir, recurse=False)

  def _mount(self, root):
    ''' Attach this StoreFS to the specified path `root`.
        Return the controlling FUSE object.
    '''
    return FUSE(self, root, foreground=True, nothreads=True, debug=False, use_ino=True)
    ##return TracingObject(FUSE(self, root, foreground=True, nothreads=True, debug=False))

  def allocate_mortal_inum(self):
    return self._inodes.allocate_mortal_inum()

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

  def _inum(self, E):
    ''' Compute the inode number for a Dirent.
        HardlinkDirents have a persistent .inum mapping the the Meta['iref'] field.
        Others do not and keep a private ._inum, not preserved across umount.
    '''
    if E.ishardlink:
      inum = E.inum
    else:
      # allocate transient inum
      try:
        inum = E._inum
      except AttributeError:
        inum = E._inum = self.allocate_mortal_inum()
    return inum

  def _Estat(self, E):
    ''' Stat a Dirent, return a dict with useful st_* fields.
    '''
    inum = self._inum(E)
    if E.ishardlink:
      E2 = self._inodes.dirent(inum)
    else:
      E2 = E
    d = obj_as_dict(E2.meta.stat(), 'st_')
    # TODO: what to do about st_dev?
    # TODO: different nlink for Dir?
    d['st_dev'] = 1701
    d['st_ino'] = inum
    d['st_atime'] = float(d['st_atime'])
    d['st_ctime'] = float(d['st_ctime'])
    d['st_mtime'] = float(d['st_mtime'])
    if d['st_uid'] == NOUSERID:
      d['st_uid'] = self._fs_uid
    if d['st_gid'] == NOGROUPID:
      d['st_gid'] = self._fs_gid
    XP("_Estat: d=%r", d)
    return d

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
      if E.ishardlink:
        E2 = self._inodes.dirent(E.inum)
        warning("map hardlink %s => %s", E, E2)
      else:
        E2 = E
      ctx_uid, ctx_gid, ctx_pid = ctx = fuse_get_context()
      # test the access against the caller's uid/gid
      # pass same in as default file ownership in case there are no metadata
      return E2.meta.access(amode, ctx_uid, ctx_gid,
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

  @locked
  @trace_method
  def link(self, target, source):
    # TODO: if source is a symlink, follow to find the real thing
    Esrc, Psrc = self._namei2(source)
    if not Esrc.isfile and not Esrc.ishardlink:
      raise FuseOSError(errno.EPERM)
    # resolve the target - should terminate at the directory to
    # receive the new hardlink
    Pdst, P, tail_path = self._resolve(target)
    # target must not exist, therefore there should be unresolved path elements
    if not tail_path:
      # we expect the path to not fully resolve, otherwise the object already exists
      raise FuseOSError(errno.EEXIST)
    # if there are more than 1 unresolved components then some
    # ancestor of target is missing
    if len(tail_path) > 1:
      XP("tail_path = %r", tail_path)
      raise FuseOSError(errno.ENOENT)
    # the final component must be a directory in order to create the new link
    if not Pdst.isdir:
      raise FuseOSError(errno.ENOTDIR)
    if Esrc.ishardlink:
     # point Esrc at the master Dirent in ._inodes
     inum = Esrc.inum
     Esrc = self._inodes.dirent(inum)
    else:
      # new hardlink, update the source
      # keep Esrc as the master
      # obtain EsrcLink, the HardlinkDirent wrapper for Esrc
      # put EsrcLink into the enclosing Dir, replacing Esrc
      src_name = Esrc.name
      inum0 = self._inum(Esrc)
      EsrcLink = self._inodes.make_hardlink(Esrc)
      Psrc[src_name] = EsrcLink
      inum = EsrcLink.inum
      if inum != inum0:
        raise RuntimeError("new hardlink: original inum %d != linked inum %d"
                           % (inum0, inum))
    # install the destination hardlink
    # make a new hardlink object referencing the inode
    # and attach it to the target directory
    dst_name, = tail_path
    EdstLink = HardlinkDirent.to_inum(inum, dst_name)
    Pdst[dst_name] = EdstLink
    # increment link count on underlying Dirent
    Esrc.meta.nlink += 1

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
      if E.ishardlink:
        # point E at the shared Dirent
        inum = self._inum(E)
        E, P = self._inodes.dirent2(inum)
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
      # we expect the path to not fully resolve, otherwise the object already exists
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

  def __init__(self, fs, path, E, for_read, for_write, for_append, lock=None):
    O.__init__(self)
    if lock is None:
      lock = fs._lock
    self.fs = fs
    self.log = fs.log
    self.logQ = fs.logQ
    self.path = path
    self.E = E
    self.Eopen = E.open()
    self.for_read = for_read
    self.for_write = for_write
    self.for_append = for_append
    self._lock = lock

  def __str__(self):
    return "<FileHandle %r %s>" % (self.path, self.E)

  def write(self, data, offset):
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

class Inodes(object):
  ''' Inode information for a filesystem.
  '''

  def __init__(self, fs, inodes_datatext=None):
    self.fs = fs
    self.max_used = 0
    # cache mapping from inode number to Dirent
    self._dirents_by_inum = {}
    # freed inode numbers for reuse
    self._freed = Range()
    # alocated inode numbers which are not hard links, and discarded after umount
    self._mortal = Range()
    # mapping from inode numbers to Dirents
    if inodes_datatext is None:
      self._init_empty()
    else:
      self._hardlinked, self._hardlinks_dir = self._decode_inode_data(inodes_datatext)
    self._lock = RLock()

  def _init_empty(self):
    self._hardlinked = Range()
    self._hardlinks_dir = Dir('inodes')

  def _decode_inode_data(self, idatatext):
    ''' Decode the permanent inode numbers and the Dirent containing their Dirents.
    '''
    XP("decode idatatext: %r", idatatext)
    idata = untexthexify(idatatext)
    taken_data, offset1 = get_bsdata(idata)
    offset = 0
    _hardlinked = Range()
    while offset < len(taken_data):
      start, offset = get_bs(taken_data, offset)
      end, offset = get_bs(taken_data, offset)
      _hardlinked.add(start, end)
    _hardlinked_dir, offset1 = decode_Dirent(idata, offset1)
    if _hardlinked_dir is None:
      error("invalid Dirent for _hardlinked_dir, inodes LOST")
      self._init_empty()
    if offset1 < len(idata):
      warning("unparsed idatatext at offset %d: %r", offset1, idata[offset1:])
    return _hardlinked, _hardlinked_dir

  @locked
  def encode(self):
    ''' Transcribe the permanent inode numbers and the Dirent containing their Dirents.
    '''
    # record the spans of allocated inodes
    taken = b''.join( put_bs(S.start) + put_bs(S.end)
                      for S in self._hardlinked.spans() )
    # ... and append the Dirent.
    return put_bsdata(taken) + self._hardlinks_dir.encode()

  def ipathelems(self, inum):
    return [ str(b) for b in put_bs(inum) ]

  def ipath(self, inum):
    return '/'.join(self.ipathelems(inum))

  @locked
  def dirent2(self, inum):
    ''' Locate the Dirent for inode `inum`, return it and its parent.
        Raises ValueError if the `inum` is unknown.
    '''
    with Pfx("dirent2(%d)", inum):
      XP("inum=%r, _dirents_by_inum=%r", inum, self._dirents_by_inum)
      Einfo = self._dirents_by_inum.get(inum)
      if Einfo is None:
        ipath = self.ipath(inum)
        E, P, tail_path = resolve(self._hardlinks_dir, ipath)
        if tail_path:
          raise ValueError("not in self._dirents_by_inum and %r not in self._hardlinks_dir"
                           % (ipath,))
        self._dirents_by_inum[inum] = E, P
      else:
        E, P = Einfo
      return E, P

  @locked
  def dirent(self, inum):
    ''' Locate the Dirent for inode `inum`, return it.
        Raises ValueError if the `inum` is unknown.
    '''
    with Pfx("dirent(%d)", inum):
      E, P = self.dirent2(inum)
      return E

  @locked
  def _allocate_free_inum(self):
    ''' Allocate an unused inode number and return it.
        Use the lowest inum from ._freed, otherwise choose one above
        ._mortal and ._hardlinked.
    '''
    freed = self._freed
    if freed:
      inum = freed.start
      freed -= inum
    else:
      inum = max( (1, self._mortal.end, self._hardlinked.end) )
    if inum in self._dirents_by_inum:
      raise RuntimeError("allocated inode number %d, but already in inode cache"
                         % (inum,))
    if inum in self._mortal:
      raise RuntimeError("allocated inode number %d, but already in mortal inode list (%s)"
                         % (inum, self._mortal))
    return inum

  @locked
  def allocate_mortal_inum(self):
    ''' Allocate an ephemeral inode number to survive only until umount.
        Record the inum in ._mortal.
    '''
    inum = self._allocate_free_inum()
    self._mortal.add(inum)
    return inum

  @locked
  def make_hardlink(self, E):
    ''' Create a new HardlinkDirent wrapping `E` and return the new Dirent.
    '''
    if E.type != D_FILE_T:
      raise ValueError("may not hardlink Dirents of type %s", E.type)
    # use the inode number of the source Dirent
    inum = self.fs._inum(E)
    E.meta.nlink = 1
    Edst = HardlinkDirent.to_inum(inum, E.name)
    # note the inum in the _hardlinked Range
    self._hardlinked.add(inum)
    # file the Dirent away in the _hardlinks_dir
    D = self._hardlinks_dir
    pathelems = self.ipathelems(inum)
    for name in pathelems[:-1]:
      if name not in D:
        D = D.mkdir(name)
      else:
        D = D.chdir1(name)
    name = pathelems[-1]
    X("HARDLINKS FINAL NAME %r", name)
    if name in D:
      raise RuntimeError("inum %d already allocated: %s", inum, D[name])
    D[name] = E
    self._dirents_by_inum[inum] = E, D
    # return the new HardlinkDirent
    return Edst

if __name__ == '__main__':
  from cs.venti.vtfuse_tests import selftest
  selftest(sys.argv)
