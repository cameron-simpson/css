#!/usr/bin/python
#
# Fuse interface to a Store.
# Uses llfuse: https://bitbucket.org/nikratio/python-llfuse/
# Formerly used fusepy: https://github.com/terencehonles/fusepy
# but that doesn't work with Python 3 and has some other problems.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' FUSE implementation wrapping a cs.vt.fs.FileSystem instance.
'''

from logging import getLogger, FileHandler as LogFileHandler, Formatter as LogFormatter
import errno
import os
from os import O_CREAT, O_WRONLY, O_RDWR, O_APPEND, O_EXCL
from os.path import abspath, dirname
import stat
import subprocess
import sys
from cs.context import stackattrs
from cs.excutils import logexc
from cs.logutils import warning, error, exception, DEFAULT_BASE_FORMAT, LogTime
from cs.pfx import Pfx, PfxThread, XP
from cs.x import X
from . import defaults
from .dir import Dir, FileDirent, SymlinkDirent, IndirectDirent
from .fs import FileHandle, FileSystem
from .store import MissingHashcodeError
import llfuse

FuseOSError = llfuse.FUSEError

LOGGER_NAME = __name__
LOGGER_FILENAME = 'vtfuse.log'

# OSX setxattr option values
XATTR_NOFOLLOW = 0x0001
XATTR_CREATE = 0x0002
XATTR_REPLACE = 0x0004

PREV_DIRENT_NAME = '...'
PREV_DIRENT_NAMEb = PREV_DIRENT_NAME.encode('utf-8')

# notional I/O blocksize for stat.st_blksize
FS_IO_BLOCKSIZE = 4096

def mount(
    mnt,
    E,
    *,
    S=None,
    archive=None,
    subpath=None,
    readonly=None,
    append_only=False,
    fsname=None
):
  ''' Run a FUSE filesystem, return the Thread running the filesystem.

      Parameters:
      * `mnt`: mount point
      * `E`: Dirent of root Store directory
      * `S`: optional backing Store, default from defaults.S
      * `archive`: if not None, an Archive or similar, with a
        `.update(Dirent[,when])` method
      * `subpath`: relative path from `E` to the directory to attach
        to the mountpoint
      * `readonly`: forbid data modification operations
      * `append_only`: files may not be truncated or overwritten
      * `fsname`: optional filesystem name for use by llfuse
  '''
  if readonly is None:
    readonly = S.readonly
  else:
    if not readonly and S.readonly:
      warning(
          "Store %s is readonly, using readonly option for mount (was %r)", S,
          readonly
      )
      readonly = True
  # forget the archive if readonly
  if readonly:
    if archive is not None:
      warning("readonly, forgetting archive %s", archive)
      archive = None
  log = getLogger(LOGGER_NAME)
  log.propagate = False
  log_handler = LogFileHandler(LOGGER_FILENAME)
  log_formatter = LogFormatter(DEFAULT_BASE_FORMAT)
  log_handler.setFormatter(log_formatter)
  log.addHandler(log_handler)
  X("mount: S=%s", S)
  X("mount: E=%s", E)
  ##dump_Dirent(E, recurse=True)
  FS = StoreFS(
      E,
      S=S,
      archive=archive,
      subpath=subpath,
      readonly=readonly,
      append_only=append_only,
      show_prev_dirent=True
  )
  return FS._vt_runfuse(mnt, fsname=fsname)

def umount(mnt):
  ''' Unmount the filesystem mounted at `mnt`, return umount(8) exit status.
  '''
  return subprocess.call(['umount', mnt])

def handler(method):
  ''' Decorator for FUSE handlers.

      Prefixes exceptions with the method name, associates with the
      Store, prevents anything other than a FuseOSError being raised.
  '''

  def handle(self, *a, **kw):
    ''' Wrapper for FUSE handler methods.
    '''
    syscall = method.__name__
    if syscall == 'write':
      fh, offset, bs = a
      arg_desc = [
          str(a[0]),
          str(a[1]),
          "%d bytes:%r..." % (len(bs), bytes(bs[:16]))
      ]
    else:
      arg_desc = [
          (
              ("<%s>" % (type(arg).__name__,))
              if isinstance(arg, llfuse.RequestContext) else repr(arg)
          ) for arg in a
      ]
    arg_desc.extend(
        "%s=%r" % (kw_name, kw_value) for kw_name, kw_value in kw.items()
    )
    arg_desc = ','.join(arg_desc)
    with Pfx("%s.%s(%s)", type(self).__name__, syscall, arg_desc):
      trace = syscall in (
          ##'getxattr',
          ##'setxattr',
          ##'statfs',
      )
      if trace:
        X("CALL %s(%s)", syscall, arg_desc)
      fs = self._vtfs
      try:
        with stackattrs(defaults, fs=fs):
          with fs.S:
            with LogTime("SLOW SYSCALL", threshold=5.0):
              result = method(self, *a, **kw)
            if trace:
              if isinstance(result, bytes):
                X(
                    "CALL %s result => %d bytes, %r...", syscall, len(result),
                    result[:16]
                )
              else:
                X("CALL %s result => %s", syscall, result)
            return result
      ##except FuseOSError as e:
      ##  warning("=> FuseOSError %s", e, exc_info=False)
      ##  raise
      except OSError as e:
        ##warning("=> OSError %s => FuseOSError", e, exc_info=False)
        raise FuseOSError(e.errno) from e
      except MissingHashcodeError as e:
        error("raising IOError from missing hashcode: %s", e)
        raise FuseOSError(errno.EIO) from e
      except Exception as e:
        exception("unexpected exception, raising EINVAL %s:%s", type(e), e)
        raise FuseOSError(errno.EINVAL) from e
      except BaseException as e:
        error("UNCAUGHT EXCEPTION: %s", e)
        raise RuntimeError("UNCAUGHT EXCEPTION") from e
      except:
        error("=> EXCEPTION %r", sys.exc_info())

  return handle

class DirHandle:
  ''' An "open" Dir: keeps a list of the names from open time
      and a reference to the Dir so that it can validate the names
      at readdir time.
  '''

  def __init__(self, fs, D):
    self.fs = fs
    self.D = D
    self.names = list(D.keys())

class StoreFS_LLFUSE(llfuse.Operations):
  ''' Class providing filesystem operations, suitable for passing
      to a FUSE() constructor.
  '''

  def __init__(
      self,
      E,
      *,
      S=None,
      archive=None,
      subpath=None,
      options=None,
      readonly=None,
      append_only=False,
      show_prev_dirent=False
  ):
    ''' Initialise a new FUSE mountpoint.

        Parameters:
        * `E`: the root directory reference
        * `S`: optional backing Store, default from defaults.S
        * `archive`: if not None, an Archive or similar, with a
          .update(Dirent[,when]) method
        * `subpath`: relative path to mount Dir
        * `readonly`: forbid data modification; if omitted or None,
          infer from S.readonly
        * `append_only`: forbid truncation or overwrite of file data
        * `show_prev_dirent`: show previous Dir revision as '...'
    '''
    if readonly is None:
      readonly = S.readonly
    fs = self._vtfs = FileSystem(
        E,
        S=S,
        archive=archive,
        subpath=subpath,
        readonly=readonly,
        append_only=append_only,
        show_prev_dirent=show_prev_dirent
    )
    # llfuse requires the mount point inode to be inode 1
    fs[1] = fs.mntE
    llf_opts = set(llfuse.default_options)
    if os.uname().sysname == 'Darwin' and 'nonempty' in llf_opts:
      # Not available on OSX.
      warning(
          "llf_opts=%r: drop 'nonempty' option, not available on Darwin",
          sorted(llf_opts)
      )
      llf_opts.discard('nonempty')
    if options is not None:
      for opt in options:
        if opt.startswith('-'):
          llf_opts.discard(opt[1:])
        else:
          llf_opts.add(opt)
    self._vt_llf_opts = llf_opts

  # debugging aid
  def __getattr__(self, attr):
    warning("UNKNOWN ATTR: StoreFS.__getattr__: attr=%r", attr)

    def attrfunc(*a, **kw):
      ''' Stub function to report on attributes which get called.
          Intended to report on unimplemented methods.
      '''
      warning("CALL UNKNOWN ATTR: %s(a=%r,kw=%r)", attr, a, kw)
      raise RuntimeError("CALL UNKNOWN ATTR %s(*%r,**%r)" % (attr, a, kw))

    return attrfunc

##def __getattribute__(self, attr):
##  X("LOOKUP %r ...", attr)
##  try:
##    return object.__getattribute__(self, attr)
##  except AttributeError:
##    return self.__getattr__(attr)

  def __str__(self):
    return "<%s %s>" % (self.__class__.__name__, self._vtfs)

  def _vt_runfuse(self, mnt, fsname=None):
    ''' Run the filesystem once.
        Return a Thread managing the mount.
    '''
    fs = self._vtfs
    S = fs.S
    if fsname is None:
      fsname = str(S)
    # llfuse reads additional mount options from the fsname :-(
    fsname = fsname.replace(',', ':')
    with S:
      opts = set(self._vt_llf_opts)
      opts.add("fsname=" + fsname)
      ##opts.add('noappledouble')
      llfuse.init(self, mnt, opts)
      # record the full path to the mount point
      # this is used to support '..' at the top of the tree
      fs.mnt_path = abspath(mnt)

      @logexc
      def mainloop():
        ''' Worker main loop to run the filesystem then tidy up.
        '''
        with stackattrs(defaults, fs=fs):
          with S:
            with defaults.common_S(S):
              llfuse.main(workers=32)
              llfuse.close()
        S.close()

      T = PfxThread(target=mainloop)
      S.open()
      T.start()
      return T

  def _vt_i2E(self, inode):
    try:
      E = self._vtfs.i2E(inode)
    except ValueError as e:
      warning("access(inode=%d): %s", inode, e)
      raise FuseOSError(errno.EINVAL)
    return E

  def _vt_EntryAttributes(self, E):
    ''' Compute an llfuse.EntryAttributes object from `E`.meta.
    '''
    fs = self._vtfs
    st = E.stat(fs=fs)
    EA = llfuse.EntryAttributes()
    EA.st_ino = st.st_ino
    ## EA.generation
    ## EA.entry_timeout
    ## EA.attr_timeout
    EA.st_mode = st.st_mode
    EA.st_nlink = st.st_nlink
    uid = st.st_uid
    if uid is None or uid < 0:
      uid = fs._fs_uid
    gid = st.st_gid
    if gid is None or gid < 0:
      gid = fs._fs_gid
    EA.st_uid = uid
    EA.st_gid = gid
    ## EA.st_rdev
    EA.st_size = st.st_size
    EA.st_blksize = FS_IO_BLOCKSIZE
    EA.st_blocks = (st.st_size + 511) // 512
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
      warning(
          "_vt_str: expected bytes, got %s %r, passing unchanged", type(bs), bs
      )
      s = bs
    return s

  @staticmethod
  def _vt_bytes(s):
    if isinstance(s, str):
      bs = s.encode('utf-8')
    else:
      warning(
          "_vt_bytes: expected str, got %s %r, passing unchanged", type(s), s
      )
      bs = s
    return bs

  ##############
  # FUSE support methods.

  @handler
  def access(self, inode, mode, ctx):
    ''' Check if the requesting process has `mode` rights on `inode`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.access
    '''
    E = self._vt_i2E(inode)
    return self._vtfs.access(E, mode, uid=ctx.uid, gid=ctx.gid)

  @handler
  def create(self, parent_inode, name_b, mode, flags, ctx):
    ''' Create a new file and open it. Return file handle index and EntryAttributes.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.create
    '''
    fs = self._vtfs
    if fs.readonly:
      raise FuseOSError(errno.EROFS)
    name = self._vt_str(name_b)
    P = self._vt_i2E(parent_inode)
    if name in P:
      warning(
          "create(parent_inode=%d:%s,name=%r): already exists - surprised!",
          parent_inode, P, name
      )
      del P[name]
    fhndx = fs.open2(P, name, flags | O_CREAT)
    E = fs._fh(fhndx).E
    E.meta.chmod(mode)
    P[name] = E
    return fhndx, self._vt_EntryAttributes(E)

  @handler
  def destroy(self):
    ''' Cleanup operations, called when llfuse.close has been called,
        just before the filesystem is unmounted.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.destroy
    '''
    # TODO: call self.forget with all kreffed inums?
    self._vtfs.close()

  @handler
  def flush(self, fh):
    ''' Handle close() system call.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.flush
    '''
    FH = self._vtfs._fh(fh)
    FH.flush()

  @handler
  def forget(self, ideltae):
    ''' Decrease lookup counts for indoes in `ideltae`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.forget

        We do not bother with this as Inodes persist in memory for
        the duration of the mount.
    '''
    pass

  @handler
  def fsync(self, fh, datasync):
    ''' Flush buffers for open file `fh`.

        `datasync`: if true, only flush the data contents, not the metadata.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.fsync
    '''
    self._vtfs._fh(fh).flush()

  @handler
  def fsyncdir(self, fh, datasync):
    ''' Flush the buffers for open directory `fh`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.fsyncdir
    '''
    # TODO: commit dir? implies flushing the whole tree
    warning("fsyncdir does nothing at present")

  @handler
  def getattr(self, inode, ctx):
    ''' Get EntryAttributes from `inode`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.getattr
    '''
    E = self._vtfs.i2E(inode)
    return self._vt_EntryAttributes(E)

  @handler
  def getxattr(self, inode, xattr_name, ctx):
    ''' Return extended attribute `xattr_name` from `inode`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.getxattr
    '''
    # TODO: test for permission to access inode?
    return self._vtfs.getxattr(inode, xattr_name)

  @handler
  def link(self, inode, new_parent_inode, new_name_b, ctx):
    ''' Link `inode` to new name `new_name_b` in `new_parent_inode`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.link

        Links in a VT filesystem are not implemented in a typical
        UNIX way; they are implemented using IndirectDirents,
        which contain a reference to the primary Dirent's UUID.
        Normal (non-indirect) Dirents are singletons for a given FileSystem
        instance.
        They are referred to in the FileSystem inodes table.

        A fresh mount must be able to dereference an IndirectDirent
        to obtain the primary Dirent.
        Because FileSystems may be of arbitrary size, the file tree
        is fetched/decoded on demand.
        Therefore, Dirents with indirect references are stored
        persistently in the FileSystem state and instantiated at
        mount.

        Because of this, a primary Dirent should exist _either_ in
        a Dir _or_ in the persistent set of Inodes, otherwise there
        can be multiple Dirents with the same UUID which would need
        reconciling when encountered.
        When a normal Dirent gets its first additional link it is
        _replaced_ by an IndirectDirent and the primary moved solely
        into the Inodes table.

        TODO: this is a real problem if we attach foreign trees
        that also have these UUIDs, so maybe reconciliation should
        be a standard action.
    '''
    # TODO: move almost all of this into cs.vt.fs
    # once inode and new_parent_inode are deferenced
    fs = self._vtfs
    if fs.readonly:
      raise FuseOSError(errno.EROFS)
    # TODO: test for write access to new_parent_inode
    new_name = self._vt_str(new_name_b)
    I = fs[inode]
    E = I.E
    if E.isindirect:
      raise RuntimeError("tried to link IndirectDirent!")
    # TODO: remove this check if we can avoid Dir loops
    if E.isdir:
      raise FuseOSError(errno.EPERM)
    Pnew = fs.i2E(new_parent_inode)
    # the final component must be a directory in order to create the new link
    if not Pnew.isdir:
      raise FuseOSError(errno.ENOTDIR)
    if new_name in Pnew:
      raise FuseOSError(errno.EEXIST)
    uu = E.get_uuid()
    EI = Pnew[new_name] = IndirectDirent(new_name, uu)
    I.refcount += 1
    # need to promote the old Dir entry to an IndirectDirent
    # TODO: standard operation in fs.py
    Pold = E.parent
    if Pold:
      if Pold.isindirect:
        if Pold.uuid != uu:
          warning(
              "E.parent's UUID (%r) does not make E.uuid (%r)", Pold.uuid, uu
          )
      else:
        old_name = E.name
        Eold = Pold[old_name]
        if not Eold.isindirect:
          if Eold is E:
            # replace with IndirectLink to E.uuid
            Pold[old_name] = IndirectDirent(old_name, uu)
          else:
            # not the same Dirent:
            # the expected scenario is that it has the same UUID
            # and a different history
            if Eold.uuid == uu:
              warning(
                  "original link has the same UUID but is not the same object, reconciling"
              )
              E.reconcile(Eold)
              Eold = E
            else:
              warning(
                  "old parent already has a different Dirent for %r", old_name
              )
    # utilise the latest parent and name for purposes
    E.parent = EI
    E.name = new_name
    return self._vt_EntryAttributes(E)

  @handler
  def listxattr(self, inode, ctx):
    ''' Return list of extended attributes of `inode`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.listxattr
    '''
    # TODO: ctx allows to access inode?
    return self._vtfs.i2E(inode).meta.listxattrs()

  @handler
  def lookup(self, parent_inode, name_b, ctx):
    ''' Look up `name_b` in `parent_inode`, return EntryAttributes.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.lookup
    '''
    name = self._vt_str(name_b)
    fs = self._vtfs
    I = fs[parent_inode]
    # TODO: test for permission to search parent_inode
    P = I.E
    EA = None
    if name == '.':
      E = P
    elif name == '..':
      if E is fs.mntE:
        # directly stat the directory above the mountpoint
        try:
          st = os.stat(dirname(fs.mnt_path))
        except OSError as e:
          raise FuseOSError(e.errno)
        EA = self._stat_EntryAttributes(st)
      else:
        # otherwise use the parent with the FS
        E = P.parent
    elif name == PREV_DIRENT_NAME and fs.show_prev_dirent:
      E = P.prev_dirent
    else:
      try:
        E = P[name]
      except KeyError:
        ##warning("lookup(parent_inode=%s, name=%r): ENOENT", parent_inode, name)
        ##raise FuseOSError(errno.ENOENT)
        EA = llfuse.EntryAttributes()
        EA.st_ino = 0
        EA.entry_timeout = 1.0
        return EA
    if EA is None:
      if E is None:
        raise FuseOSError(errno.ENOENT)
      try:
        EA = self._vt_EntryAttributes(E)
      except Exception as e:
        warning("%r: %s", name, e)
        raise FuseOSError(errno.ENOENT)
    return EA

  @handler
  def mkdir(self, parent_inode, name_b, mode, ctx):
    ''' Create new directory named `name_b` in `parent_inode`, return EntryAttributes.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.mkdir
    '''
    fs = self._vtfs
    if fs.readonly:
      raise FuseOSError(errno.EROFS)
    name = self._vt_str(name_b)
    # TODO: test for permission to search and write parent_inode
    P = fs.i2E(parent_inode)
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

  @handler
  def mknod(self, parent_inode, name_b, mode, rdev, ctx):
    ''' Create file named `named_b` in `parent_inode`, possibly special.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.mknod
    '''
    fs = self._vtfs
    if fs.readonly:
      raise FuseOSError(errno.EROFS)
    name = self._vt_str(name_b)
    P = fs.i2E(parent_inode)
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

  @handler
  def open(self, inode, flags, ctx):
    ''' Open an existing file, return file handle index.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.open
    '''
    E = self._vt_i2E(inode)
    if flags & (O_CREAT | O_EXCL):
      warning(
          "open(ionde=%d:%s,flags=0o%o): unexpected O_CREAT(0o%o) or O_EXCL(0o%o)",
          inode, E, flags, O_CREAT, O_EXCL
      )
      flags &= ~(O_CREAT | O_EXCL)
    for_write = (flags & O_WRONLY) == O_WRONLY or (flags & O_RDWR) == O_RDWR
    for_append = (flags & O_APPEND) == O_APPEND
    if (for_write or for_append) and self._vtfs.readonly:
      raise FuseOSError(errno.EROFS)
    fhndx = self._vtfs.open(E, flags)
    if for_write or for_append:
      E.changed = True
    return fhndx

  @handler
  def opendir(self, inode, ctx):
    ''' Open directory `inode`, return directory handle `fhndx`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.opendir
    '''
    # TODO: check for permission to read
    E = self._vtfs.i2E(inode)
    if not E.isdir:
      raise FuseOSError(errno.ENOTDIR)
    fs = self._vtfs
    OD = DirHandle(fs, E)
    return fs._new_file_handle_index(OD)

  @handler
  def read(self, fhndx, off, size):
    ''' Read `size` bytes from open file handle `fhndx` at offset `off`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.read
    '''
    FH = self._vtfs._fh(fhndx)
    chunks = []
    while size > 0:
      data = FH.read(size, off)
      if not data:
        break
      chunks.append(data)
      off += len(data)
      size -= len(data)
    return b''.join(chunks)

  @handler
  def readdir(self, fhndx, off):
    ''' Read entries in open directory file handle `fhndx` from offset `off`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.readdir
    '''
    # TODO: if rootdir, generate '..' for parent of mount
    FH = self._vtfs._fh(fhndx)

    def entries():
      ''' Generator to yield directory entries.
      '''
      o = off
      D = FH.D
      fs = FH.fs
      S = self._vtfs.S
      names = FH.names
      while True:
        try:
          E = None
          EA = None
          if o == 0:
            name = '.'
            with S:
              E = D[name]
          elif o == 1:
            name = '..'
            if D is self._vtfs.mntE:
              try:
                st = os.stat(dirname(self._vtfs.mnt_path))
              except OSError as e:
                warning("os.stat(%r): %s", dirname(self._vtfs.mnt_path), e)
              else:
                EA = self._stat_EntryAttributes(st)
            else:
              with S:
                E = D[name]
          else:
            o2 = o - 2
            if o2 == len(names) and fs.show_prev_dirent:
              name = PREV_DIRENT_NAME
              try:
                E = D.prev_dirent
              except MissingHashcodeError as e:
                warning("prev_dirent unavailable: %s", e)
            elif o2 >= len(names):
              break
            else:
              name = names[o2]
              if name == '.' or name == '..':
                # already special cased
                E = None
              elif name == PREV_DIRENT_NAME and fs.show_prev_dirent:
                warning(
                    "%s: readdir: suppressing entry %r because fs.show_prev_dirent is true",
                    D, PREV_DIRENT_NAME
                )
                E = None
              else:
                with S:
                  E = D.get(name)
          if EA is None:
            if E is not None:
              # yield name, attributes and next offset
              with stackattrs(defaults, fs=fs):
                with S:
                  try:
                    EA = self._vt_EntryAttributes(E)
                  except Exception as e:
                    warning("%r: %s", name, e)
                    EA = None
          if EA is not None:
            yield self._vt_bytes(name), EA, o + 1
          o += 1
        except Exception as e:
          exception("READDIR: %s", e)
          raise

    return entries()

  @staticmethod
  def _stat_EntryAttributes(st):
    ''' Convert a POSIX stat object into an llfuse.EntryAttributes.
    '''
    EA = llfuse.EntryAttributes()
    EA.st_ino = st.st_ino
    EA.st_mode = st.st_mode
    EA.st_nlink = st.st_nlink
    EA.st_uid = st.st_uid
    EA.st_gid = st.st_gid
    EA.st_size = st.st_size
    EA.st_blksize = st.st_blksize
    EA.st_blocks = st.st_blocks
    EA.st_atime_ns = int(st.st_atime * 1000000000)
    EA.st_ctime_ns = int(st.st_ctime * 1000000000)
    EA.st_mtime_ns = int(st.st_mtime * 1000000000)
    return EA

  @handler
  def readlink(self, inode, ctx):
    ''' Read the reference from symbolic link `inode`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.readlink
    '''
    # TODO: check for permission to read the link?
    E = self._vtfs.i2E(inode)
    if not E.issym:
      raise FuseOSError(errno.EINVAL)
    return self._vt_bytes(E.pathref)

  @handler
  def release(self, fhndx):
    ''' Release open file handle `fhndx`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.release
    '''
    with Pfx("_fh_close(fhndx=%d)", fhndx):
      self._vtfs._fh_close(fhndx)

  @handler
  def releasedir(self, fhndx):
    ''' Release open directory file handle `fhndx`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.releasedir
    '''
    self._vtfs._fh_remove(fhndx)

  @handler
  def removexattr(self, inode, xattr_name, ctx):
    ''' Remove extended attribute `xattr_name` from `inode`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.removexattr
    '''
    # TODO: test for inode ownership?
    return self._vtfs.removexattr(inode, xattr_name)

  @handler
  def rename(
      self, parent_inode_old, name_old_b, parent_inode_new, name_new_b, ctx
  ):
    ''' Rename an entry `name_old_b` from `parent_inode_old` to `name_new_b` in `parent_inode_new`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.rename
    '''
    if self._vtfs.readonly:
      raise FuseOSError(errno.EROFS)
    name_old = self._vt_str(name_old_b)
    name_new = self._vt_str(name_new_b)
    Psrc = self._vtfs.i2E(parent_inode_old)
    if name_old not in Psrc:
      raise FuseOSError(errno.ENOENT)
    if not self._vtfs.access(Psrc, os.X_OK | os.W_OK, ctx.uid, ctx.gid):
      raise FuseOSError(errno.EPERM)
    Pdst = self._vtfs.i2E(parent_inode_new)
    if not self._vtfs.access(Pdst, os.X_OK | os.W_OK, ctx.uid, ctx.gid):
      raise FuseOSError(errno.EPERM)
    E = Psrc[name_old]
    del Psrc[name_old]
    E.name = name_new
    Pdst[name_new] = E

  @handler
  def rmdir(self, parent_inode, name_b, ctx):
    ''' Remove the directory named `name_b` from `parent_inode`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.rmdir
    '''
    if self._vtfs.readonly:
      raise FuseOSError(errno.EROFS)
    name = self._vt_str(name_b)
    P = self._vtfs.i2E(parent_inode)
    if not self._vtfs.access(P, os.X_OK | os.W_OK, ctx.uid, ctx.gid):
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

  @handler
  def setattr(self, inode, attr, fields, fhndx, ctx):
    ''' Change attributes of `inode`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.setattr
    '''
    # TODO: test CTX for permission to chmod/chown/whatever
    # TODO: sanity check fields for other update_* flags?
    if self._vtfs.readonly:
      raise FuseOSError(errno.EROFS)
    E = self._vtfs.i2E(inode)
    with Pfx(E):
      M = E.meta
      if fields.update_atime:
        ##info("ignoring update_atime st_atime_ns=%s", attr.st_atime_ns)
        pass
      if fields.update_mtime:
        M.mtime = attr.st_mtime_ns / 1000000000.0
      if fields.update_mode:
        M.chmod(attr.st_mode & 0o7777)
      if fields.update_uid:
        M.uid = attr.st_uid
      if fields.update_gid:
        M.gid = attr.st_gid
      if fields.update_size:
        FH = FileHandle(self, E, False, True, False)
        FH.truncate(attr.st_size)
        FH.close()
      EA = self._vt_EntryAttributes(E)
    return EA

  @handler
  def setxattr(self, inode, xattr_name, value, ctx):
    ''' Set the extended attribute `xattr_name` to `value` on `inode`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.setxattr

        TODO: x-vt-* control/query psuedo attributes.
    '''
    # TODO: check perms (ownership?)
    return self._vtfs.setxattr(inode, xattr_name, value)

  @handler
  def statfs(self, ctx):
    ''' Implement statfs(2).

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.statfs

        Currently bodges by reporting on the filesystem containing
        the current working directory, should really report on the
        filesystem holding the Store. That requires a Store.statfs
        method of some kind (TODO).
    '''
    # TODO: get free space from the current Store
    #       implies adding some kind of method to stores?
    st = os.statvfs(".")
    fst = llfuse.StatvfsData()
    for attr in ('f_bsize', 'f_frsize', 'f_blocks', 'f_bfree', 'f_bavail',
                 'f_files', 'f_ffree', 'f_favail'):
      setattr(fst, attr, getattr(st, attr))
    return fst

  @handler
  def symlink(self, parent_inode, name_b, target_b, ctx):
    ''' Create symlink named `name_b` in `parent_inode`, referencing `target_b`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.symlink
    '''
    fs = self._vtfs
    if fs.readonly:
      raise FuseOSError(errno.EROFS)
    with Pfx("SYMLINK parent_iode=%r, name_b=%r, target_b=%r, ctx=%r",
             parent_inode, name_b, target_b, ctx):
      name = self._vt_str(name_b)
      target = self._vt_str(target_b)
      # TODO: check search/write on P
      P = fs.i2E(parent_inode)
      if not P.isdir:
        raise FuseOSError(errno.ENOTDIR)
      if name in P:
        raise FuseOSError(errno.EEXIST)
      E = SymlinkDirent(name, target=target)
      P[name] = E
      return self._vt_EntryAttributes(E)

  @handler
  def unlink(self, parent_inode, name_b, ctx):
    ''' Unlink the name `name_b` from `parent_inode`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.unlink
    '''
    # TODO: move most of this into cs.vt.fs
    fs = self._vtfs
    if fs.readonly:
      raise FuseOSError(errno.EROFS)
    name = self._vt_str(name_b)
    # TODO: check search/write on P
    P = fs[parent_inode].E
    if not P.isdir:
      raise FuseOSError(errno.ENOTDIR)
    try:
      E = P.pop(name)
    except KeyError:
      raise FuseOSError(errno.ENOENT)
    if E.isindirect:
      I = fs.E2inode(E)
      I.refcount -= 1

  @handler
  def write(self, fhndx, off, buf):
    ''' Write data `buf` to the file handle `FH` at offset `off`.

        http://www.rath.org/llfuse-docs/operations.html#llfuse.Operations.write
    '''
    fs = self._vtfs
    if fs.readonly:
      raise FuseOSError(errno.EROFS)
    FH = fs._fh(fhndx)
    written = FH.write(buf, off)
    if written != len(buf):
      warning("only %d bytes written, %d supplied", written, len(buf))
    return written

StoreFS = StoreFS_LLFUSE

if __name__ == '__main__':
  sys.exit(main(sys.argv))
