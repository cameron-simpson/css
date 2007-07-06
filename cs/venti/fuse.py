#!/usr/bin/python
#
# Fuse interface to a Store.
#       - Cameron Simpson <cs@zip.com.au>
#

# Horrible hack because the Fuse class doesn't seem to tell fuse file
# objects which class instantiation they belong to.
# I guess There Can Be Only One.
mainFuseStore=None

def fuse(backfs,store):
  ''' Run a FUSE filesystem with the specified basefs backing store
      and Venti storage.
      This is a separate function to defer the imports.
  '''

  from fuse import Fuse, Direntry
  from errno import ENOSYS
  class FuseStore(Fuse):
    def __init__(self, *args, **kw):
      global mainFuseStore
      assert mainFuseStore is None, "multiple instantiations of FuseStore forbidden"

      print "FuseStore:"
      print "  args =", `args`
      print "  kw =", `kw`
      Fuse.__init__(self, *args, **kw)

      import os.path
      assert os.path.isdir(backfs)
      self.__backfs=backfs
      self.__store=store

      # HACK: record fuse class object for use by files :-(
      mainFuseStore=self
      self.file_class=self.__File

    def __abs(self, path):
      assert path[0] == '/'
      return os.path.join(self.__backfs, path[1:])

    def getattr(self,path):
      print "getattr", path
      return os.lstat(self.__abs(path))
    def readlink(self, path):
      print "readlink", path
      return os.readlink(self.__abs(path))
    def readdir(self, path, offset):
      print "readdir", path
      yield Direntry('.')
      yield Direntry('..')
      for e in os.listdir(self.__abs(path)):
        print "readdir yield"
        yield Direntry(e)
    def unlink(self, path):
      print "unlink", path
      os.unlink(self.__abs(path))
    def rmdir(self, path):
      print "rmdir", path
      os.rmdir(self.__abs(path))
    def symlink(self, path, path1):
      print "symlink", path
      os.symlink(path, self.__abs(path1))
    def rename(self, path, path1):
      print "rename", path, path1
      os.rename(self.__abs(path), self.__abs(path1))
    def link(self, path, path1):
      print "link", path, path1
      os.link(self.__abs(path), self.__abs(path1))
    def chmod(self, path, mode):
      print "chmod 0%03o %s" % (mode,path)
      os.chmod(self.__abs(path), mode)
    def chown(self, path, user, group):
      print "chown %d:%d %s" % (user,group,path)
      os.chown(self.__abs(path), user, group)
    def truncate(self, path, len):
      print "truncate", path, len
      return -ENOSYS
    def mknod(self, path, mode, dev):
      print "mknod", path, mode, dev
      os.mknod(self.__abs(path), mode, dev)
    def mkdir(self, path, mode):
      print "mkdir 0%03o %s" % (mode,path)
      os.mkdir(self.__abs(path), mode)
    def utime(self, path, times):
      print "utime", path
      os.utime(self.__abs(path), times)
    def access(self, path, mode):
      print "access", path, mode
      if not os.access(self.__abs(path), mode):
        return -EACCES
    def statfs(self):
      print "statfs"
      return os.statvfs(self.__basefs)

    class __File(object):
      def __init__(self, path, flags, *mode):
        print "new __File: path =", path, "flags =", `flags`, "mode =", `mode`
        global mainFuseStore
        assert mainFuseStore is not None
        self.__Fuse=mainFuseStore
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

  FS=FuseStore()
  FS.parser.add_option(mountopt="root", metavar="PATH",
                       help='file system adjunct to store')
  FS.parse(values=FS, errex=1)
  FS.main()
