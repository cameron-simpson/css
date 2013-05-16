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
from cs.logutils import D
from cs.obj import O

def mount(mnt, D, S):
  ''' Run a FUSE filesystem with Dirent and backing Store.
  '''
  FS = StoreFS(D, E)
  FS.mount(mnt)

class StoreFS(LoggingMixIn, Operations, O):
  ''' Class providing filesystem operations, suitable for passing
      to a FUSE() constructor.
  '''

  def __init__(self, D, S):
    ''' Initilaise a new FUSE mountpoint.
        mnt: the mountpoint
        D: the root directory reference
        S: the Store to hold data
    '''
    self.S =S
    self.D = D

  def mount(self, root):
    ''' Attach this StoreFS to the specified path `root`.
        Return the controlling FUSE object.
    '''
    return FUSE(self, root, foreground=True, nothreads=True)

  def resolve(self, path):
    ''' Return (D, basename).
    '''
    return self.D.resolve(path)

  def resolve2(self, path):
    ''' Resolve path to Dirent.
    '''
    D, basename = self.resolve(path)
    return D[basename]

  ##############
  # FUSE support methods.

  def getattr(self, path):
    return self.resolve2(path).meta.stat()
  def readlink(self, path):
    self.__OUT("readlink", path)
    return os.readlink(self.__abs(path))
  def readdir(self, path):
    return ['.', '..'] + self.resolve2(path).keys()
  def unlink(self, path):
    D, basename = self.resolve(path)
    if D[basename].isdir:
      raise ValueError("%s: is a directory" % (path,))
    del D[basename]
  def rmdir(self, path):
    D, basename = self.resolve(path)
    E = D[basename]
    if not E.isdir:
      raise ValueError("%s: not a directory" % (path,))
    if len(E.entires()) > 0:
      raise ValueError("%s: not empty" % (path,))
    del D[basename]
  def symlink(self, path, path1):
    self.__OUT("symlink", path)
    os.symlink(path, self.__abs(path1))
  def rename(self, path, path1):
    self.__OUT("rename", path, path1)
    os.rename(self.__abs(path), self.__abs(path1))
  def link(self, path, path1):
    self.__OUT("link", path, path1)
    os.link(self.__abs(path), self.__abs(path1))
  def chmod(self, path, mode):
    self.__OUT("chmod 0%03o %s" % (mode, path))
    os.chmod(self.__abs(path), mode)
  def chown(self, path, user, group):
    self.__OUT("chown %d:%d %s" % (user, group, path))
    os.chown(self.__abs(path), user, group)
  def truncate(self, path, len):
    self.__OUT("truncate", path, len)
    return -ENOSYS
  def mknod(self, path, mode, dev):
    self.__OUT("mknod", path, mode, dev)
    os.mknod(self.__abs(path), mode, dev)
  def mkdir(self, path, mode):
    self.__OUT("mkdir 0%03o %s" % (mode, path))
    os.mkdir(self.__abs(path), mode)
  def utime(self, path, times):
    self.__OUT("utime", path)
    os.utime(self.__abs(path), times)
  def access(self, path, mode):
    self.__OUT("access", path, mode)
    if not os.access(self.__abs(path), mode):
      return -EACCES
  def statfs(self):
    self.__OUT("statfs")
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
