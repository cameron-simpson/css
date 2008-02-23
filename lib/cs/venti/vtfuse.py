#!/usr/bin/python
#
# Fuse interface to a Store.
#       - Cameron Simpson <cs@zip.com.au>
#

from fuse import Fuse, FuseArgs   ## Direntry
from errno import ENOSYS
import sys
import os

def fusemount(mnt,S,E):
  ''' Run a FUSE filesystem with the specified basefs backing store
      and Venti storage.
  '''

  FS=FuseStore(mnt,S,E)
  sys.stderr.write("calling FS.main...\n")
  FS.main()

# Horrible hack because the Fuse class doesn't seem to tell fuse file
# objects which class instantiation they belong to.
# I guess There Can Be Only One.
mainFuseStore=None

class FuseStore(Fuse):
  def __init__(self, mnt, store, E, *args, **kw):
    ''' Class to manage a FUSE mountpoint.
        mnt: the mountpoint
        store: the Store to hold data
        E: the root directory reference
    '''
    # HACK: record fuse class object for use by files :-(
    global mainFuseStore
    assert mainFuseStore is None, "multiple instantiations of FuseStore forbidden"
    mainFuseStore=self

    fargs=FuseArgs()
    fargs.mountpoint=mnt
    ##fargs=fargs.assemble()
    kw['prog']=sys.argv[0]
    kw['usage']="Usage Message";

    print "FuseStore:"
    print "  args =", `args`
    print "  kw =", `kw`
    print "  fargs =", `fargs`
    Fuse.__init__(self, fuse_args=fargs, **kw)
    self.flags=0
    self.multithreaded=0
    ''' Keep a mapping of blockref (raw) to nlinks.
        We will preserve the ones with nlinks > 1 or file permissions.
    '''
    self.__inodes={}
    self.__mountpoint=mnt
    self.__store=store
    self.__root=E
    self.file_class=self.__File
    self.__out=None

  def __OUT(self,*args):
    if self.__out is None:
      self.__out=open("/dev/pts/39","w")
      sys.stdout=self.__out
      sys.stderr=self.__out
      if len(args):
        sys.stderr.write(" ".join([str(x) for x in args])+"\n")

  def __abs(self, path):
    assert path[0] == '/'
    return os.path.join('/u/cameron/tmp', path[1:])

  def __namei(self,path):
    return self.__store.namei(path,self.__root.bref)

  def getattr(self,path):
    self.__OUT("getattr", path)
    E=self.__namei(path)
    if E is None:
      return None
    return os.lstat(self.__abs(path))
  def readlink(self, path):
    self.__OUT("readlink", path)
    return os.readlink(self.__abs(path))
  def readdir(self, path, offset):
    self.__OUT("readdir", path)
    yield Direntry('.')
    yield Direntry('..')
    for e in os.listdir(self.__abs(path)):
      self.__OUT("readdir yield %s" % `e`)
      yield Direntry(e)
  def unlink(self, path):
    self.__OUT("unlink", path)
    os.unlink(self.__abs(path))
  def rmdir(self, path):
    self.__OUT("rmdir", path)
    os.rmdir(self.__abs(path))
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
    self.__OUT("chmod 0%03o %s" % (mode,path))
    os.chmod(self.__abs(path), mode)
  def chown(self, path, user, group):
    self.__OUT("chown %d:%d %s" % (user,group,path))
    os.chown(self.__abs(path), user, group)
  def truncate(self, path, len):
    self.__OUT("truncate", path, len)
    return -ENOSYS
  def mknod(self, path, mode, dev):
    self.__OUT("mknod", path, mode, dev)
    os.mknod(self.__abs(path), mode, dev)
  def mkdir(self, path, mode):
    self.__OUT("mkdir 0%03o %s" % (mode,path))
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
      global mainFuseStore
      assert mainFuseStore is not None
      self.__Fuse=mainFuseStore
      print "new __File: path =", path, "flags =", `flags`, "mode =", `mode`
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
