#!/usr/bin/env python3

''' An ecnrypted cloud backup tool.
'''

# pylint: disable=too-many-lines

from binascii import unhexlify
from contextlib import contextmanager
from datetime import datetime
import errno
from getopt import getopt, GetoptError
from getpass import getpass
from mmap import mmap, PROT_READ
from os import readlink, stat_result
from os.path import (
    dirname,
    exists as existspath,
    isfile as isfilepath,
    isdir as isdirpath,
    join as joinpath,
    relpath,
)
from stat import S_ISDIR, S_ISREG, S_ISLNK
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import RLock
from types import SimpleNamespace
from uuid import UUID, uuid4
import hashlib
import os
import shutil
import sys
import time
from cs.buffer import CornuCopyBuffer
from cs.cloud import CloudArea, CloudPath, validate_subpath
from cs.cloud.crypt import (
    create_key_pair,
    download as crypt_download,
    upload as crypt_upload,
    upload_paths,
    recrypt_passtext,
)
from cs.cmdutils import BaseCommand
from cs.deco import strable
from cs.lex import cutsuffix, hexify, is_identifier
from cs.logutils import warning, error
from cs.mappings import (
    AttrableMappingMixin,
    AttrableMapping,
    UUIDedDict,
    UUIDNDJSONMapping,
)
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_method, unpfx
from cs.progress import Progress, progressbar
from cs.seq import splitoff
from cs.threads import locked
from cs.units import BINARY_BYTES_SCALE
from cs.upd import Upd, print  # pylint: disable=redefined-builtin
from typeguard import typechecked

def main(argv=None):
  ''' Create a `CloudBackupCommandCmd` instance and call its main method.
  '''
  return CloudBackupCommand().run(argv)

class CloudBackupCommand(BaseCommand):
  ''' A main programme instance.
  '''

  GETOPT_SPEC = 'A:d:k:'
  USAGE_FORMAT = r'''Usage: {cmd} [options] subcommand [...]
    Encrypted cloud backup utility.
    Options:
      -A cloud_area A cloud storage area of the form prefix://bucket/subpath.
                    Default from the $CLOUDBACKUP_AREA environment variable.
      -d statedir   The directoy containing {cmd} state.
                    Default: $HOME/.cloudbackup
      -k key_name   Specify the name of the public/private key to
                    use for operations. The default is from the
                    $CLOUDBACKUP_KEYNAME environment or from the most recent
                    existing key pair.
  '''

  # TODO: -K keysdir, or -K private_keysdir:public_keysdir, default from {state_dirpath}/keys
  # TODO: restore [-u backup_uuid] backup_name subpath
  # TODO: recover backup_name [backup_uuid] subpaths...
  # TODO: rekey -K oldkey backup_name [subpaths...]: add per-file keys for new key
  # TODO: openssl-like -passin option for passphrase

  # pylint: disable=too-few-public-methods
  class OPTIONS_CLASS(SimpleNamespace):
    ''' Options namespace with convenience methods.
    '''

    def __init__(self, **kw):
      super().__init__(**kw)
      self._lock = RLock()

    @property
    @locked
    def cloud_area(self):
      ''' The `CloudArea`, from the `-A` global option or `$CLOUDBACKUP_AREA`.
      '''
      if not self.cloud_area_path:
        raise ValueError(
            "no cloud area specified; requires -A option or $CLOUDBACKUP_AREA"
        )
      return CloudArea.from_cloudpath(self.cloud_area_path)

  @staticmethod
  def apply_defaults(options):
    options.cloud_area_path = os.environ.get('CLOUDBACKUP_AREA')
    options.key_name = os.environ.get('CLOUDBACKUP_KEYNAME')
    options.state_dirpath = joinpath(os.environ['HOME'], '.cloudbackup')

  @staticmethod
  def apply_opts(opts, options):
    ''' Apply main command line options.
    '''
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-A':
          options.cloud_area_path = val
        elif opt == '-d':
          options.state_dirpath = val
        elif opt == '-k':
          options.key_name = val
        else:
          raise RuntimeError("unimplemented option")
    options.backup_area = BackupArea(options.state_dirpath, options.cloud_area)

  @staticmethod
  def cmd_backup(argv, options):
    ''' Usage: {cmd} topdir backup_name [subpaths...]
          For each subpath, back up topdir/subpath into the backup
          named backup_name. If no subpaths are specified, back up
          all of topdir.
          backup_name
              The name of the backup collection rooted at topdir.
          subpath
              A path within topdir to back up.
    '''
    badopts = False
    if not argv:
      warning("missing topdir")
      badopts = True
    else:
      topdir = argv.pop(0)
      with Pfx("topdir %r", topdir):
        if not isdirpath(topdir):
          warning("not a directory")
          badopts = True
    if not argv:
      warning("missing backup_name")
      badopts = True
    else:
      backup_name = argv.pop(0)
      with Pfx("backup_name %r", backup_name):
        if not is_identifier(backup_name):
          warning("not an identifier")
          badopts = True
    subpaths = argv
    for subpath in subpaths:
      with Pfx("subpath %r"):
        try:
          validate_subpath(subpath)
        except ValueError as e:
          warning(str(e))
          badopts = True
        else:
          subdirpath = joinpath(topdir, subpath)
          if not isdirpath(subdirpath):
            warning("not a directory: %r", subdirpath)
            badopts = True
    if badopts:
      raise GetoptError("bad invocation")
    ##print(
    ##    "backup %s/%s => %s as %r" % (
    ##        topdir, (
    ##            ','.join(subpaths)
    ##            if len(subpaths) > 1 else subpaths[0] if subpaths else ''
    ##        ), options.backup_area.cloud_area.cloudpath, backup_name
    ##    )
    ##)
    # TODO: a facility to supply passphrases for use when recrypting
    # a per-file key under a new public key when the per-file key is
    # present under a different public key
    options.backup_area.init()
    backup = options.backup_area.run_backup(
        topdir,
        subpaths or ('',),
        backup_name=backup_name,
        public_key_name=options.key_name
    )
    print("backup run completed ==>", backup)

  # pylint: disable=too-many-locals,too-many-branches
  @staticmethod
  def cmd_ls(argv, options):
    ''' Usage: {cmd} backup_name [subpaths...]
          List the files in the backup named backup_name.
    '''
    # TODO: list backup names if no backup_name
    # TODO: list backup_uuids?
    # TODO: -A: allbackups=True
    # TODO: -U backup_uuid
    badopts = False
    all_backups = False
    backup_uuid = None
    if not argv:
      warning("missing backup_name")
      badopts = True
    else:
      backup_name = argv.pop(0)
      if not is_identifier(backup_name):
        warning("backup_name is not an identifier: %r", backup_name)
        badopts = True
    subpaths = argv
    for subpath in subpaths:
      with Pfx("subpath %r", subpath):
        if subpath and subpath != '.':
          try:
            validate_subpath(subpath)
          except ValueError as e:
            warning("invalid subpath: %r: %s", subpath, unpfx(str(e)))
            badopts = True
    if badopts:
      raise GetoptError("bad invocation")
    subpaths = list(
        map(lambda subpath: '' if subpath == '.' else subpath, argv or ('',))
    )
    backup_area = options.backup_area
    backup = backup_area[backup_name]
    with Upd().insert(0) as proxy:
      proxy.prefix = f"{backup}: "
      for subpath in subpaths:
        if subpath == ".":
          subpath = ''
        for subsubpath, details in backup.walk(subpath,
                                               backup_uuid=backup_uuid,
                                               all_backups=all_backups):
          proxy(subsubpath)
          for name, name_details in sorted(details.items()):
            pathname = joinpath(subsubpath, name)
            if all_backups:
              print(pathname + ":")
              for backup in name_details.backups:
                print(" ", repr(backup))
            else:
              st_mode = name_details.st_mode
              assert not S_ISDIR(st_mode)
              if S_ISREG(st_mode):
                print(
                    pathname,
                    "%d:%s" % (name_details.st_size, name_details.hashcode)
                )
              elif S_ISLNK(name_details.st_mode):
                print(pathname, '->', name_details.link)
              else:
                print(pathname, "???", repr(name_details))

  @staticmethod
  def cmd_new_key(argv, options):
    ''' Usage: {cmd}
          Generate a new key pair and print its name.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    backup_area = options.backup_area
    passphrase = getpass("Passphrase for new key: ")
    backup_area.init()
    key_uuid = backup_area.new_key(passphrase)
    print(key_uuid)

  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
  @staticmethod
  def cmd_restore(argv, options):
    ''' Usage: {cmd} -o outputdir [-U backup_uuid] backup_name [subpaths...]
          Restore files from the named backup.
          Options:
            -o outputdir    Output directory to create to hold the
                            restored files.
            -U backup_uuid  The backup UUID from which to restore.
    '''
    # TODO: move the core logic into a BackupArea method
    # TODO: list backup names if no backup_name
    # TODO: restore file to stdout?
    # TODO: restore files as tarball to stdout or filename
    # TODO: rsync-like include/exclude or files-from options?
    badopts = False
    backup_uuid = None
    restore_dirpath = None
    opts, argv = getopt(argv, 'o:U:')
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-o':
          restore_dirpath = val
        elif opt == '-U':
          backup_uuid = val
        else:
          raise RuntimeError("unhandled option")
    if restore_dirpath is None:
      warning("missing mandatory -o outputdir option")
      badopts = True
    else:
      with Pfx("outputdir %s", restore_dirpath):
        if existspath(restore_dirpath):
          warning("already exists")
          badopts = True
    if not argv:
      warning("missing backup_name")
      badopts = True
    else:
      backup_name = argv.pop(0)
      if not is_identifier(backup_name):
        warning("backup_name is not an identifier: %r", backup_name)
        badopts = True
    subpaths = argv
    for subpath in subpaths:
      with Pfx("subpath %r", subpath):
        if subpath and subpath != '.':
          try:
            validate_subpath(subpath)
          except ValueError as e:
            warning("invalid subpath: %r: %s", subpath, unpfx(str(e)))
            badopts = True
    if badopts:
      raise GetoptError("bad invocation")
    subpaths = list(
        map(lambda subpath: '' if subpath == '.' else subpath, argv or ('',))
    )
    backup_area = options.backup_area
    backup = backup_area[backup_name]
    if backup_uuid is None:
      backup_record = backup.latest_backup_record()
      if backup_record is None:
        warning("%s: no backups", backup.name)
        return 1
      backup_uuid = backup_record.uuid
    else:
      backup_uuid = UUID(backup_uuid)
      backup_record = backup.backup_records.by_uuid[backup_uuid]
    with Pfx("backup UUID %s", backup_uuid):
      public_key_name = backup_record.public_key_name
      with Pfx("key name %s", public_key_name):
        private_path = backup_area.private_key_path(public_key_name)
        if not isfilepath(private_path):
          error("private key file not found: %r", private_path)
          return 1
    passphrase = getpass(
        "Passphrase for backup %s (key %s): " % (backup_uuid, public_key_name)
    )
    # TODO: test passphrase against private key
    made_dirs = set()
    content_subpath = CloudPath.from_str(backup_record.content_path).subpath
    xit = 0
    print("mkdir", restore_dirpath)
    with Pfx("mkdir(%r)", restore_dirpath):
      os.mkdir(restore_dirpath, 0o777)
    made_dirs.add(restore_dirpath)
    with Upd().insert(0) as proxy:
      proxy.prefix = f"{backup}: "
      for subpath in subpaths:
        if subpath == ".":
          subpath = ''
        for subsubpath, details in backup.walk(subpath,
                                               backup_uuid=backup_uuid):
          proxy(subsubpath)
          for name, name_details in sorted(details.items()):
            pathname = joinpath(subsubpath, name)
            proxy(pathname)
            fspath = joinpath(restore_dirpath, pathname)
            with Pfx(fspath):
              st_mode = name_details.st_mode
              assert not S_ISDIR(st_mode)
              if S_ISREG(st_mode):
                hashcode_s = name_details.hashcode
                hashcode = HashCode.from_str(hashcode_s)
                hashpath = backup.hashcode_path(hashcode)
                cloudpath = joinpath(content_subpath, hashpath)
                print(cloudpath, '=>', fspath)
                length = name_details.st_size
                P = crypt_download(
                    backup_area.cloud,
                    backup_area.bucket_name,
                    cloudpath,
                    private_path=private_path,
                    passphrase=passphrase,
                    public_key_name=public_key_name
                )
                fsdirpath = dirname(fspath)
                if fsdirpath not in made_dirs:
                  print("mkdir", fsdirpath)
                  with Pfx("makedirs(%r)", fsdirpath):
                    os.makedirs(fsdirpath, 0o777)
                  made_dirs.add(fsdirpath)
                with open(fspath, 'wb') as f:
                  bfr = CornuCopyBuffer.from_file(P.stdout)
                  digester = hashcode.digester()
                  for bs in progressbar(
                      bfr,
                      label=pathname,
                      total=length,
                      itemlenfunc=len,
                      units_scale=BINARY_BYTES_SCALE,
                  ):
                    digester.update(bs)
                    f.write(bs)
                with Pfx(
                    "utime(%r,%f:%s)",
                    fspath,
                    name_details.st_mtime,
                    datetime.fromtimestamp(name_details.st_mtime).isoformat(),
                ):
                  os.utime(
                      fspath,
                      times=(
                          name_details.get('st_atime')
                          or name_details.st_mtime,
                          name_details.st_mtime,
                      ),
                      follow_symlinks=False
                  )
                returncode = P.wait()
                if returncode != 0:
                  error("exit code %d from decrypter", returncode)
                  xit = 1
                retrieved_hashcode = type(hashcode)(digester.digest())
                if hashcode != retrieved_hashcode:
                  error(
                      "integrity error: retrieved data hashcode %s != expected hashcode %s",
                      retrieved_hashcode, hashcode
                  )
                  xit = 1
              elif S_ISLNK(name_details.st_mode):
                print(pathname, '->', name_details.link)
                with Pfx("symlink(%r,%r)", name_details.link, fspath):
                  os.symlink(name_details.link, fspath)
              else:
                warning(
                    "unhandled file type: st_mode=0o%06o", name_details.st_mode
                )
                print(pathname, "???", repr(name_details))
    return xit

class HashCode(bytes):
  ''' The base class for various flavours of hashcodes.
  '''

  hashclasses = {}
  hashname = None
  hashfunc = lambda: None

  def __eq__(self, other):
    ''' Hashcodes are equal ifthey have the same type and data.
    '''
    # pylint: disable=unidiomatic-typecheck
    return type(self) == type(other) and bytes.__eq__(self, other)

  @staticmethod
  def add_hashclass(hashclass):
    ''' Add a hashclass to the mapping of hashname->hashclass.
    '''
    hashname = hashclass.hashname
    assert hashname not in HashCode.hashclasses
    HashCode.hashclasses[hashname] = hashclass

  @staticmethod
  def from_str(s):
    ''' Decode a string of the form *hashname*`:`*hexbytes*
        into a `HashCode` subclass instance.
    '''
    hashname, hexbytes = s.split(':')
    bs = unhexlify(hexbytes)
    hashclass = HashCode.hashclasses[hashname]
    return hashclass(bs)

  def __str__(self):
    return self.hashname + ':' + hexify(self)

  @classmethod
  def digester(cls):
    ''' Return a digester hash object, suitable for use via its `.update` and
        `.digest` and `.hexdigest` methods.
    '''
    return cls.hashfunc()

  @classmethod
  def hashbuffer(cls, bfr):
    ''' Checksum the `bytes` instances from `bfr`,
        return an instance of `cls`.
    '''
    m = cls.digester()
    for bs in bfr:
      m.update(bs)
    return cls(m.digest())

  def path(self, *sizes):
    ''' Split a hashcode into short prefixes and a tail,
        for constructing subdirectory trees based on the hashcode.
    '''
    return splitoff(hexify(self), *sizes)

class SHA1HashCode(HashCode):
  ''' A `HashCode` subclass using SHA1.
  '''

  hashname = 'sha1'
  hashfunc = hashlib.sha1

HashCode.hashclasses[SHA1HashCode.hashname] = SHA1HashCode

# the hash class to use for content hashing
DEFAULT_HASHCLASS = SHA1HashCode

# pylint: disable=too-many-instance-attributes
class BackupArea:
  ''' A named backup area.

      Local disc areas:
        state_dirpath/private_keys/uuid.private.pem
        state_dirpath/public_keys/uuid.public.pem
        state_dirpath/backups/name/
          backups.ndjson    uuid,timestamp,pubkeyname
          diruuids.ndjson   uuid,subpath
          dirstate          u/u/id.ndjson
      Cloud areas:
        cloud_area/content  file uploads by hashcode
  '''

  @strable(open_func=CloudArea.from_cloudpath)
  @typechecked
  def __init__(
      self,
      state_dirpath: str,
      cloud_area: CloudArea,
  ):
    ''' Initialise the `BackupArea`.

        Parameters:
        * `state_dirpath`: a directory holding global state
        * `cloud_area`: the cloud storage area
    '''
    self.cloud_area = cloud_area
    self.state_dirpath = state_dirpath
    self.backups_dirpath = joinpath(state_dirpath, 'backups')
    self.private_key_dirpath = joinpath(state_dirpath, 'private_keys')
    self.public_key_dirpath = joinpath(state_dirpath, 'public_keys')
    self.content_area = cloud_area.subarea('content')
    self.per_name_backup_records = {}
    self._lock = RLock()

  def __str__(self):
    return "%s(state_dirpath=%r,cloud_area=%s)" % (
        type(self).__name__, self.state_dirpath, self.cloud_area
    )

  @pfx_method(use_str=True)
  def init(self):
    ''' Create the required state directories.
    '''
    for dirpath in (
        self.state_dirpath,
        self.private_key_dirpath,
        self.public_key_dirpath,
        self.backups_dirpath,
    ):
      if not isdirpath(dirpath):
        ##print("create directory", repr(dirpath))
        with Pfx("makedirs(%r)", dirpath):
          os.makedirs(dirpath, 0o700)

  @property
  def cloud(self):
    ''' The `Cloud` instance.
    '''
    return self.cloud_area.cloud

  @property
  def bucket_name(self):
    ''' The cloud bucket name.
    '''
    return self.cloud_area.bucket_name

  def __getitem__(self, index):
    ''' Indexing by an identifier returns the associated `NamedBackup`.
    '''
    # TODO: index by UUID or str(UUID) returns a (NamedBackup,BackupRecord).
    if is_identifier(index):
      backup_name = index
      backup = NamedBackup(
          backup_area=self,
          backup_name=backup_name,
          state_dirpath=self.named_state_dirpath(backup_name),
      )
      return backup
    raise KeyError(index)

  ##############################################################
  # Private/public keys.

  def private_key_path(self, key_name):
    ''' Return the path to the private key named `key_name`.
    '''
    return f"{self.private_key_dirpath}/{key_name}.private.pem"

  def public_key_path(self, key_name):
    ''' Return the path to the public key named `key_name`.
    '''
    return f"{self.public_key_dirpath}/{key_name}.public.pem"

  def new_key(self, passphrase: str):
    ''' Generate a new private/public keypair using `passphrase`,
        return its UUID.
    '''
    with TemporaryDirectory() as dirpath:
      os.chmod(dirpath, 0o700)
      uuid, private_path, public_path = create_key_pair(dirpath, passphrase)
      key_name = str(uuid)
      os.chmod(private_path, 0o600)
      shutil.move(private_path, self.private_key_path(key_name))
      shutil.move(public_path, self.public_key_path(key_name))
    return uuid

  def private_key_names(self):
    ''' Generator yielding the names of existing private keys.
    '''
    for key_file_basename in os.listdir(self.private_key_dirpath):
      key_name = cutsuffix(key_file_basename, '.private.pem')
      if key_name is not key_file_basename:
        yield key_name

  def public_key_names(self):
    ''' Generator yielding the names of existing public keys.
    '''
    for key_file_basename in os.listdir(self.public_key_dirpath):
      key_name = cutsuffix(key_file_basename, '.public.pem')
      if key_name is not key_file_basename:
        yield key_name

  @pfx_method
  def latest_key_name(self):
    ''' Return the `key_name` of the latest key pair created.
        This is the fallback if no key is specified and there's no
        environment variable.

        Both the private and public key must be present to be considered.
    '''
    latest_mtime = None
    latest_key_name = None
    for key_name in self.private_key_names():
      with Pfx("private key %r", key_name):
        public_key_path = self.public_key_path(key_name)
        with Pfx(public_key_path):
          try:
            S = os.stat(public_key_path)
          except OSError as e:
            # warn about problems other than a missing key file
            if e.errno != errno.ENOENT:
              warning("stat: %s", e)
            continue
        if latest_mtime is None or S.st_mtime > latest_mtime:
          latest_mtime = S.st_mtime
          latest_key_name = key_name
    return latest_key_name

  ##############################################################
  # Backup processes.

  def named_state_dirpath(self, backup_name):
    ''' Return the path the the state directory for a named backup.
    '''
    if not is_identifier(backup_name):
      raise ValueError("backup_name not an identifier: %r" % (backup_name,))
    return joinpath(self.backups_dirpath, backup_name)

  @pfx_method
  def run_backup(self, topdir, subpaths, *, backup_name, public_key_name=None):
    ''' Run a new backup of data from `topdir`,
        backing up everything from each `topdir/subpath` downward.
        Return the `NamedBackup`.
    '''
    if not subpaths:
      raise ValueError("no subpaths")
    if not is_identifier(backup_name):
      raise ValueError("backup_name is not an identifier: %r" % (backup_name,))
    if public_key_name is None:
      public_key_name = self.latest_key_name()
      if public_key_name is None:
        raise ValueError("no public_key_name and no self.latest_key_name()")
    if not isdirpath(topdir):
      raise ValueError("topdir is not a directory: %r" % (topdir,))
    for subpath in subpaths:
      if subpath:
        validate_subpath(subpath)
    backup = self[backup_name]
    assert isinstance(backup, NamedBackup)
    backup.init()
    with backup.run(public_key_name=public_key_name) as backup_record:
      for subpath in subpaths:
        backup.backup_tree(backup_record, topdir, subpath)
    return backup_record

@typechecked
def uuidpath(uuid: UUID, *sizes, make_subdir_of=None):
  ''' Split a UUID into short prefixes and a tail,
      for constructing subdirectory trees based on the UUID.

      If the optional `makedirs` parameter if true (default `False`)
      then create the required intermediate directories.
  '''
  uuid_s = uuid.hex
  *dirparts, _ = splitoff(uuid_s, *sizes)
  if make_subdir_of:
    dirpath = joinpath(make_subdir_of, *dirparts)
    if not isdirpath(dirpath):
      with Pfx("makedirs(%r)", dirpath):
        os.makedirs(dirpath)
  return joinpath(*dirparts, uuid_s)

class BackupRecord(UUIDedDict):
  ''' A `BackupRecord` persists information about a `NamedBackup` backup run.
  '''

  def __init__(
      self,
      *,
      public_key_name,
      content_path,
      count_files_checked=0,
      count_files_changed=0,
      count_uploaded_bytes=0,
      count_uploaded_files=0,
      **kw
  ):
    super().__init__(**kw)
    self['public_key_name'] = public_key_name
    self['content_path'] = content_path
    self['count_files_checked'] = count_files_checked
    self['count_files_changed'] = count_files_changed
    self['count_uploaded_bytes'] = count_uploaded_bytes
    self['count_uploaded_files'] = count_uploaded_files

  def __enter__(self):
    self['timestamp_start'] = time.time()
    return self

  def __exit__(self, exc_type, exc_value, exc_traceback):
    self['timestamp_end'] = time.time()

# pylint: disable=too-many-instance-attributes
class NamedBackup(SingletonMixin):
  ''' A record encapsulating a named set of backups.
  '''

  # pylint: disable=unused-argument
  @classmethod
  def _singleton_key(
      cls, *, backup_area: BackupArea, backup_name: str, state_dirpath: str
  ):
    return backup_area, backup_name

  def __init__(
      self,
      *,
      backup_area: BackupArea,
      backup_name: str,
      state_dirpath: str,
  ):
    ''' Initialise a `NamedBackup`.

        Parameters:
        * `uuid`: optional UUID for this backup run;
          one will be created if omitted
        * `backup_area`: the `BackupArea` making this run
        * `backup_name`: the name of this backup, an identifier
        * `public_key_name`: the name of the public key used to encrypt uploads
    '''
    if hasattr(self, 'backup_area'):
      return
    if not is_identifier(backup_name):
      raise ValueError("backup_name is not an identifier: %r" % (backup_name,))
    self._lock = RLock()
    self.backup_area = backup_area
    self.backup_name = backup_name
    self.state_dirpath = state_dirpath
    # the association of UUIDs with directory subpaths
    self.diruuids = UUIDNDJSONMapping(
        joinpath(self.state_dirpath, 'diruuids.ndjson')
    )
    # the directory holding the DirState files
    self.dirstates_dirpath = joinpath(self.state_dirpath, 'dirstates')
    # supports .dirstate() to return the same DirState per subpath
    self._dirstates = {}
    # cloud storage stuff
    self.content_area = self.backup_area.content_area
    self.cloud = self.content_area.cloud
    self.bucket_name = self.content_area.bucket_name
    self.backup_records = UUIDNDJSONMapping(
        joinpath(self.state_dirpath, 'backups.ndjson'), dictclass=BackupRecord
    )
    # TODO: not using _saved_hashcodes yet
    self._saved_hashcodes = set()

  def __str__(self):
    return "%s[%s]" % (self.backup_area, self.backup_name)

  def init(self):
    ''' Create the required on disc structures.
    '''
    if not isdirpath(self.state_dirpath):
      print("create", self.state_dirpath)
      with Pfx("mkdir(%r)", self.state_dirpath):
        os.mkdir(self.state_dirpath)

  @staticmethod
  def hashcode_path(hashcode, *sizes):
    ''' Make a path based on a hashcode.
    '''
    assert isinstance(hashcode, HashCode)
    if not sizes:
      sizes = 3, 3
    *hashparts, _ = hashcode.path(*sizes)
    path = joinpath(
        *hashparts,
        hexify(hashcode) + '.' + hashcode.hashname,
    )
    return path

  def latest_backup_record(self):
    ''' Return the latest completed backup record.
    '''
    backup_records = list(self.backup_records.by_uuid.values())
    if not backup_records:
      return None
    return max(
        self.backup_records.by_uuid.values(),
        key=lambda backup_record: backup_record.timestamp_end or 0
    )

  ##############################################################
  # DirStates

  @locked
  def dirstate(self, subpath: str):
    ''' Return the `DirState` for `subpath`.
    '''
    if subpath:
      validate_subpath(subpath)
    dirstate = self._dirstates.get(subpath)
    if dirstate is None:
      uu_sp = self.diruuids.by_subpath.get(subpath)
      if uu_sp:
        uu = uu_sp.uuid
      else:
        uu = uuid4()
        self.diruuids.add_to_mapping(UUIDedDict(uuid=uu, subpath=subpath))
      uupath = uuidpath(uu, 2, 2, make_subdir_of=self.dirstates_dirpath)
      dirstate_path = joinpath(
          self.dirstates_dirpath, dirname(uupath), uu.hex
      ) + '.ndjson'
      dirstate = UUIDNDJSONMapping(dirstate_path, dictclass=FileBackupState)
      dirstate.uuid = uu
      dirstate.subpath = subpath
    return dirstate

  # pylint: disable=too-many-branches
  def walk(self, subpath: str, *, backup_uuid=None, all_backups=False):
    ''' Walk the backups of `subpath`, yield `(subsubpath,details)`.
        Only subsubpaths with nondirectory children are yiedled.

        If `all_backups` is true, the `details` are the complete
        name->backup_states mapping for that directory.

        If `all_backups` is false, the details are the name->backup_state
        associated with `backup_uuid`.
        The default `backup_uuid` is that of the latest recorded backup.
    '''
    if subpath:
      validate_subpath(subpath)
    if backup_uuid is None:
      if not all_backups:
        backup_record = self.latest_backup_record()
        if backup_record is None:
          raise ValueError("%s: no backup records" % (self,))
        backup_uuid = backup_record.uuid
    else:
      if not isinstance(backup_uuid, UUID):
        raise ValueError("backup_uuid is not a UUID: %r" % (backup_uuid,))
      if all_backups:
        raise ValueError(
            "a backup_uuid may not be specified if all_backups is true:"
            " backup_uuid=%r, all_backups=%r" % (backup_uuid, all_backups)
        )
    q = [subpath]
    while q:
      subpath = q.pop()
      dirstate = self.dirstate(subpath)
      details = {}
      for name, file_backups in sorted(dirstate.by_name.items()):
        if all_backups:
          details[name] = file_backups
          continue
        # locate the records for backup_uuid
        pathname = joinpath(subpath, name)
        file_backup = None
        for each_backup in map(UUIDedDict, file_backups.backups):
          if each_backup.uuid == backup_uuid:
            file_backup = each_backup
            break
        if file_backup is None:
          print(pathname, "MISSING", repr(file_backups.backups))
          continue
        if S_ISDIR(file_backup.st_mode):
          q.append(pathname)
          continue
        details[name] = file_backup
      if details:
        yield subpath, details

  ##############################################################
  # Backup processes.

  @contextmanager
  def run(self, *, public_key_name):
    ''' Context manager for running a backup.
    '''
    backup_record = BackupRecord(
        public_key_name=public_key_name,
        content_path=self.content_area.cloudpath
    )
    with backup_record:
      yield backup_record
    self.backup_records.add_to_mapping(backup_record)

  def backup_tree(
      self, backup_record: BackupRecord, topdir: str, topsubpath: str
  ):
    ''' Back up everything in `topdir/topsubpath`
        recording the results against `backup_record`.
    '''
    # TODO: spawn per-folder backups via a Later
    if topsubpath:
      validate_subpath(topsubpath)
      topdirpath = joinpath(topdir, topsubpath)
    else:
      topdirpath = topdir
    with Upd().insert(1) as walk_proxy:
      for dirpath, dirnames, _ in os.walk(topdirpath):
        walk_proxy("%s/", dirpath)
        subpath = relpath(dirpath, topdirpath)
        if subpath == '.':
          subpath = ''
        self.backup_single_directory(backup_record, topdir, subpath)
        # walk the children lexically ordered
        dirnames[:] = sorted(dirnames)

  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
  def backup_single_directory(
      self, backup_record: BackupRecord, topdir, subpath
  ):
    ''' Backup the immediate contents of a particular subdirectory.
        Return `True` if everything was successfully backed up,
        `False` otherwise.
    '''
    if subpath:
      validate_subpath(subpath)
    backup_uuid = backup_record.uuid
    dirpath = joinpath(topdir, subpath)
    with Pfx("backup_single_directory(%r)", dirpath):
      with Pfx("scandir"):
        try:
          dir_entries = {
              dir_entry.name: dir_entry
              for dir_entry in os.scandir(dirpath)
          }
        except OSError as e:
          warning(str(e))
          return False
      ok = True
      dirstate = self.dirstate(subpath)
      names = set()
      with Upd().insert(1) as file_proxy:
        for name, dir_entry in sorted(dir_entries.items()):
          file_proxy(joinpath(dirpath, name))
          with Pfx(name):
            if name in names:
              warning("repeated")
              continue
            backup_record['count_files_checked'] += 1
            names.add(name)
            name_backups = dirstate.by_name.get(name)
            if name_backups is None:
              name_backups = FileBackupState(name=name, backups=[])
            stat = dir_entry.stat(follow_symlinks=False)
            if dir_entry.is_symlink():
              try:
                link = readlink(joinpath(dirpath, name))
              except OSError as e:
                warning("readlink: %s", e)
                ok = False
                continue
              name_backups.add_symlink(
                  backup_uuid=backup_uuid, stat=stat, link=link
              )
            elif dir_entry.is_dir():
              name_backups.add_dir(backup_uuid=backup_uuid, stat=stat)
            elif dir_entry.is_file():
              # the fast check
              prevstate = name_backups.latest_backup()
              # pylint: disable=simplifiable-if-statement
              if prevstate is None:
                changed = True
              else:
                prev_mode = prevstate['st_mode']
                prev_mtime = prevstate['st_mtime']
                prev_size = prevstate['st_size']
                if not S_ISREG(prev_mode):
                  changed = True
                else:
                  if (stat.st_mtime != prev_mtime
                      or stat.st_size != prev_size):
                    changed = True
                  else:
                    changed = False
              if changed:
                backup_record['count_files_changed'] += 1
                rfilepath = joinpath(subpath, name)
                # we get a fresh stat and hashcode from backup_filename
                # because the file might change while we're mucking about
                backedup_hashcode, backedup_stat = self.backup_filename(
                    backup_record, topdir, rfilepath, prevstate=prevstate
                )
                name_backups.add_regular_file(
                    backup_uuid=backup_uuid,
                    stat=backedup_stat,
                    hashcode=backedup_hashcode
                )
                print(
                    "backed up %s/%s => %s" %
                    (dirpath, name, backedup_hashcode)
                )
              else:
                # record the current stat and the previous hashcode
                name_backups.add_regular_file(
                    backup_uuid=backup_uuid,
                    stat=stat,
                    hashcode=prevstate['hashcode']
                )
            else:
              warning("unsupported type st_mode=%o", stat.st_mode)
              ok = False
              continue
            dirstate.add_to_mapping(name_backups, exists_ok=True)
      return ok

  # pylint: disable=too-many-locals
  @typechecked
  def backup_filename(
      self, backup_record: BackupRecord, topdir: str, subpath: str, *,
      prevstate
  ):
    ''' Back up a single file *topdir*`/`*subpath*,
        return its stat and hashcode.

        Checksum the file; if the same as `prevstate.hashcode`
        return the hashcode immediately.

        Otherwise upload the file contents against the hashcode
        and return the hashcode.
    '''
    validate_subpath(subpath)
    assert prevstate is None or isinstance(prevstate, AttrableMappingMixin)
    backup_area = self.backup_area
    cloud = backup_area.cloud
    bucket_name = backup_area.bucket_name
    public_key_name = backup_record.public_key_name
    filename = joinpath(topdir, subpath)
    with Pfx("backup_filename(%r)", filename):
      with open(filename, 'rb') as f:
        fd = f.fileno()
        ##fd = os.open(filename, O_RDONLY)
        fstat = os.fstat(fd)
        if not S_ISREG(fstat.st_mode):
          raise ValueError("not a regular file")
        hasher = DEFAULT_HASHCLASS.digester()
        if fstat.st_size == 0:
          # can't mmap empty files, and in any case they're easy
          hashcode = DEFAULT_HASHCLASS(DEFAULT_HASHCLASS.digester().digest())
          self.upload_hashcode_content(backup_record, fd, hashcode, 0)
          return hashcode, fstat
        # compute hashcode from file contents
        hashcode = DEFAULT_HASHCLASS.digester()
        mm = mmap(fd, fstat.st_size, prot=PROT_READ)
        hasher.update(mm)
        hashcode = DEFAULT_HASHCLASS(hasher.digest())
        # compute some crypt-side upload paths
        basepath = self.hashcode_path(hashcode)
        data_subpath, key_subpath = upload_paths(
            basepath, public_key_name=public_key_name
        )
        # TODO: a check_uploaded flag?
        if prevstate and hashcode == prevstate.hashcode:
          # assume content already uploaded in the previous backup
          # TODO: check that? cloud.stat?
          if public_key_name == prevstate.public_key_name:
            return hashcode, fstat
          # previous upload used a different key
          # check if the upload is keyed against the current key
          if cloud.stat(bucket_name=bucket_name, path=key_subpath):
            # content already uploaded and keyed against the current key
            return hashcode, fstat
          # TODO: not against the current key, can we decrypt a different key?
          # the fall through here will be if no decryptable key is present
          # need to enumerate the upstream keys for which we have
          # local private keys by iterating over the local private key
          # names
          # look for a private key for which we already have a passphrase to hand
          for private_key_name in backup_area.private_key_names():
            passphrase = backup_area._passphrases.get(private_key_name)
            if passphrase is not None:
              other_private_path = backup_area.private_key_path(
                  private_key_name
              )
              print(
                  f"{filename}: recrypt passtext"
                  " from {private_key_name} to {public_key_name}..."
              )
              recrypt_passtext(
                  cloud,
                  bucket_name,
                  basepath,
                  old_key_name=private_key_name,
                  old_private_path=other_private_path,
                  old_passphrase=passphrase,
                  new_key_name=public_key_name,
                  new_public_path=self.backup_area
                  .public_key_path(public_key_name),
              )
              return hashcode, fstat
          # no private keys with known passphrases
          # TODO: if interactive, offer available keys, request passphrase
        # need to reupload
        # copy the file so that what we upload is stable
        # this includes a second hashcode pass, alas
        with NamedTemporaryFile() as T:
          shutil.copy(filename, T.name)
          mm = mmap(fd, fstat.st_size, prot=PROT_READ)
          hasher = DEFAULT_HASHCLASS.digester()
          hasher.update(mm)
          hashcode = DEFAULT_HASHCLASS(hasher.digest())
          # upload the content if not already uploaded
          # TODO: shared by hashcode set of locks
          P = Progress(name="upload " + filename, total=0)
          with P.bar(insert_pos=-1):
            self.upload_hashcode_content(
                backup_record, fd, hashcode, len(mm), progress=P
            )
        return hashcode, fstat

  def upload_hashcode_content(
      self,
      backup_record: BackupRecord,
      f,
      hashcode,
      length,
      *,
      progress=None
  ):
    ''' Upload the contents of `f` under the supplied `hashcode`
        into the content area specified the `contentdir_cloudpath`.
    '''
    content_area = self.backup_area.content_area
    basepath = joinpath(content_area.basepath, self.hashcode_path(hashcode))
    file_info, *cloudpaths = crypt_upload(
        f,
        self.cloud,
        self.bucket_name,
        basepath,
        public_path=self.backup_area.public_key_path(
            backup_record.public_key_name
        ),
        public_key_name=backup_record.public_key_name,
        progress=progress,
        length=length,
    )
    backup_record['count_uploaded_files'] += 1
    backup_record['count_uploaded_bytes'] += length

class FileBackupState(UUIDedDict):
  ''' A state record for a name within a directory.

      Fields:
      * `uuid`: a persistent UUID for this name within the directory;
        this may be reissued if the file type chenges (eg file/dir/symlink);
        this may track a renamed file if noticed
        (TODO: track dev/ino/mtime/size across a backup? needs lots of memory)
      * `name`: the name within the directory
      * `backups`: a list of state `dicts` in order from most recent
        backup to least recent

      Each backup state `dict` contains the following keys:
      * `uuid`: the `BackupRecord` UUID for this backup
      * `stat`: a `dict` with relevant fields from an `os.stat_result`;
        for a regular file this includes `st_mode`, `st_mtime`, `st_size`;
        for a symlink this includes an additional `link` field
      * `hashcode`: for a regular file, a `str` representation
        of the content hashcode
  '''

  @typechecked
  def _new_backup(self, backup_uuid: UUID, stat):
    ''' Prepare a shiny new file state.
    '''
    backup_uuid_s = str(backup_uuid)
    assert backup_uuid_s not in self.backups
    backup_state = UUIDedDict(uuid=backup_uuid_s, st_mode=stat.st_mode)
    if S_ISREG(stat.st_mode):
      backup_state.update(st_mtime=stat.st_mtime, st_size=stat.st_size)
    self.backups.insert(0, backup_state)
    return backup_state

  @typechecked
  def add_dir(self, *, backup_uuid: UUID, stat: stat_result):
    ''' Add a state for a directory., return the new state.
    '''
    assert S_ISDIR(stat.st_mode)
    backup_state = self._new_backup(backup_uuid, stat)
    return backup_state

  @typechecked
  def add_regular_file(
      self, *, backup_uuid: UUID, stat: stat_result, hashcode
  ):
    ''' Add a state for a regular file, return the new state.
    '''
    assert S_ISREG(stat.st_mode)
    backup_state = self._new_backup(backup_uuid, stat)
    backup_state.update(hashcode=str(hashcode))
    return backup_state

  @typechecked
  def add_symlink(self, *, backup_uuid: UUID, stat: stat_result, link: str):
    ''' Add a state for a symlink.
    '''
    assert S_ISLNK(stat.st_mode)
    backup_state = self._new_backup(backup_uuid, stat)
    backup_state.update(link=link)
    return backup_state

  def latest_backup(self):
    ''' Return the most recent backup record as an `AttrableMappingMixin`,
        or `None` if there is none.
    '''
    backups = self.backups
    if not backups:
      return None
    return AttrableMapping(backups[0])

if __name__ == '__main__':
  sys.exit(main(sys.argv))
