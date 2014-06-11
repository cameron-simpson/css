#!/usr/bin/python
#
# Fuse interface to a Store.
# Uses fusepy: https://github.com/terencehonles/fusepy
#       - Cameron Simpson <cs@zip.com.au>
#

from fuse3 import FUSE, Operations, LoggingMixIn
from errno import ENOSYS
import sys
import os
from cs.logutils import D as _D
from cs.obj import O

def mount(mnt, E, S):
  ''' Run a FUSE filesystem with Dirent and backing Store.
  '''
  FS = StoreFS(E, S)
  FS.mount(mnt)

class StoreFS(LoggingMixIn, Operations, O):
  ''' Class providing filesystem operations, suitable for passing
      to a FUSE() constructor.
  '''

  def __init__(self, E, S):
    ''' Initilaise a new FUSE mountpoint.
        mnt: the mountpoint
        dirent: the root directory reference
        S: the Store to hold data
    '''
    if not E.isdir:
      raise ValueError("not dir Dir: %s" % (E,))
    self.S =S
    self.E = E

  def mount(self, root):
    ''' Attach this StoreFS to the specified path `root`.
        Return the controlling FUSE object.
    '''
    return FUSE(self, root, foreground=True, nothreads=True)

  def resolve(self, path):
    ''' Return (dirent, basename).
    '''
    return self.E.resolve(path)

  def resolve2(self, path):
    ''' Resolve path to Dirent.
    '''
    E, basename = self.resolve(path)
    return E[basename]

  ##############
  # FUSE support methods.

  def getattr(self, path):
    return self.resolve2(path).meta.stat()
  def readlink(self, path):
    _D("readlink", path)
    return os.readlink(self.__abs(path))
  def readdir(self, path):
    return ['.', '..'] + self.resolve2(path).keys()
  def unlink(self, path):
    dirent, basename = self.resolve(path)
    if dirent[basename].isdir:
      raise ValueError("%s: is a directory" % (path,))
    del dirent[basename]
  def rmdir(self, path):
    dirent, basename = self.resolve(path)
    E = dirent[basename]
    if not E.isdir:
      raise ValueError("%s: not a directory" % (path,))
    if len(E.entires()) > 0:
      raise ValueError("%s: not empty" % (path,))
    del dirent[basename]
  def symlink(self, path, path1):
    _D("symlink", path)
    os.symlink(path, self.__abs(path1))
  def rename(self, path, path1):
    _D("rename", path, path1)
    os.rename(self.__abs(path), self.__abs(path1))
  def link(self, path, path1):
    _D("link", path, path1)
    os.link(self.__abs(path), self.__abs(path1))
  def chmod(self, path, mode):
    _D("chmod 0%03o %s" % (mode, path))
    os.chmod(self.__abs(path), mode)
  def chown(self, path, user, group):
    _D("chown %d:%d %s" % (user, group, path))
    os.chown(self.__abs(path), user, group)
  def truncate(self, path, len):
    _D("truncate", path, len)
    return -ENOSYS
  def mknod(self, path, mode, dev):
    _D("mknod", path, mode, dev)
    os.mknod(self.__abs(path), mode, dev)
  def mkdir(self, path, mode):
    _D("mkdir 0%03o %s" % (mode, path))
    os.mkdir(self.__abs(path), mode)
  def utime(self, path, times):
    _D("utime", path)
    os.utime(self.__abs(path), times)
  def access(self, path, mode):
    _D("access", path, mode)
    if not os.access(self.__abs(path), mode):
      return -EACCES
  def statfs(self):
    _D("statfs")
    return os.statvfs(self.__basefs)

  class __File(object):
    def __init__(self, path, flags, *mode):
      print("new __File: path =", path, "flags =", repr(flags), "mode =", repr(mode))
      self.file = os.fdopen(os.open("." + path, flags, *mode),
                            flag2mode(flags))
      self.fd = self.file.fileno()

    def read(self, length, offset):
      self.file.seek(offset)
      return self.file.read(length)

    def write(self, buf, offset):
      self.file.seek(offset)
      self.file.write(buf)
      return len(buf)

    def release(self, flags):
      self.file.close()

    def fsync(self, isfsyncfile):
      if isfsyncfile and hasattr(os, 'fdatasync'):
        os.fdatasync(self.fd)
      else:
        os.fsync(self.fd)

    def flush(self):
      self.file.flush()
      # cf. xmp_flush() in fusexmp_fh.c
      os.close(os.dup(self.fd))

    def fgetattr(self):
      return os.fstat(self.fd)

    def ftruncate(self, len):
      self.file.truncate(len)
