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
    basename,
    dirname,
    exists as existspath,
    isabs as isabspath,
    isfile as isfilepath,
    isdir as isdirpath,
    islink as islinkpath,
    join as joinpath,
    realpath,
    relpath,
)
import signal
from stat import S_ISDIR, S_ISREG, S_ISLNK
from tempfile import TemporaryDirectory
from threading import RLock
from types import SimpleNamespace
from uuid import UUID, uuid4
import hashlib
import os
import shutil
import sys
import termios
import time
from icontract import require
from typeguard import typechecked
from cs.buffer import CornuCopyBuffer
from cs.cloud import CloudArea, validate_subpath
from cs.cloud.crypt import (
    create_key_pair,
    download as crypt_download,
    upload as crypt_upload,
    upload_paths,
    recrypt_passtext,
)
from cs.cmdutils import BaseCommand
from cs.context import pushattrs, popattrs
from cs.deco import fmtdoc, strable
from cs.fileutils import UUIDNDJSONMapping, NamedTemporaryCopy
from cs.later import Later
from cs.lex import cutsuffix, hexify, is_identifier
from cs.logutils import warning, error, exception
from cs.mappings import (
    AttrableMappingMixin,
    AttrableMapping,
    UUIDedDict,
)
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_method, unpfx
from cs.progress import Progress, OverProgress, progressbar
from cs.resources import RunState, RunStateMixin
from cs.result import report, CancellationError
from cs.seq import splitoff
from cs.threads import locked
from cs.tty import modify_termios
from cs.units import BINARY_BYTES_SCALE, transcribe
from cs.upd import Upd, UpdProxy, print  # pylint: disable=redefined-builtin

DEFAULT_JOB_MAX = 16

class CloudBackupCommand(BaseCommand):
  ''' A main programme instance.
  '''

  GETOPT_SPEC = 'A:d:j:k:'
  USAGE_FORMAT = r'''Usage: {cmd} [options] subcommand [...]
    Encrypted cloud backup utility.
    Options:
      -A cloud_area A cloud storage area of the form prefix://bucket/subpath.
                    Default from the $CLOUDBACKUP_AREA environment variable.
      -d statedir   The directoy containing {cmd} state.
                    Default: $HOME/.cloudbackup
      -j jobs       Number of uploads or downloads to do in parallel.
      -k key_name   Specify the name of the public/private key to
                    use for operations. The default is from the
                    $CLOUDBACKUP_KEYNAME environment variable or from the
                    most recent existing key pair.
  '''

  # TODO: -K keysdir, or -K private_keysdir:public_keysdir, default from {state_dirpath}/keys
  # TODO: restore [-u backup_uuid] backup_name subpath
  # TODO: recover backup_name [backup_uuid] subpaths...
  # TODO: rekey -K oldkey backup_name [subpaths...]: add per-file keys for new key
  # TODO: openssl-like -passin option for passphrase

  SUBCOMMAND_ARGV_DEFAULT = ('ls',)

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
      return CloudArea.from_cloudpath(
          self.cloud_area_path, max_connections=self.job_max
      )

  def apply_defaults(self):
    options = self.options
    options.cloud_area_path = os.environ.get('CLOUDBACKUP_AREA')
    options.job_max = DEFAULT_JOB_MAX
    options.key_name = os.environ.get('CLOUDBACKUP_KEYNAME')
    options.state_dirpath = joinpath(os.environ['HOME'], '.cloudbackup')

  def apply_opts(self, opts):
    ''' Apply main command line options.
    '''
    options = self.options
    badopts = False
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-A':
          options.cloud_area_path = val
        elif opt == '-d':
          options.state_dirpath = val
        elif opt == '-j':
          # TODO: just save -j as job_max_s, validate below
          try:
            val = int(val)
          except ValueError as e:
            warning("%r: %s", val, e)
            badopts = True
          else:
            if val < 1:
              warning("value < 1: %d", val)
              badopts = True
            else:
              options.job_max = val
        elif opt == '-k':
          options.key_name = val
        else:
          raise RuntimeError("unimplemented option")
    try:
      options.cloud_area
    except ValueError as e:
      warning("-A: invalid cloud_area: %s: %s", options.cloud_area_path, e)
      badopts = True
    if badopts:
      raise GetoptError("bad options")
    options.cloud_backup = CloudBackup(options.state_dirpath)
    if not isdirpath(options.state_dirpath):
      print(f"{options.cmd}: mkdir {options.state_dirpath}")
      with Pfx("mkdir(%r)", options.state_dirpath):
        os.mkdir(options.state_dirpath, 0o777)

  def cmd_backup(self, argv):
    ''' Usage: {cmd} [-R] backup_name:/path/to/backup_root [subpaths...]
          For each subpath, back up /path/to/backup_root/subpath into the
          named backup area. If no subpaths are specified, back up all of
          /path/to/backup_root.
          -R  Resolve the real path of /path/to/backup_root and
              record that as the source path for the backup.
    '''
    options = self.options
    badopts = False
    use_realpath = False
    opts, argv = getopt(argv, 'R')
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-R':
          use_realpath = True
        else:
          raise RuntimeError("unhandled option")
    if not argv:
      warning("missing backup_root")
      badopts = True
    else:
      backup_root_spec = argv.pop(0)
      with Pfx("backup_name:backup_root %r", backup_root_spec):
        try:
          backup_name, backup_root_dirpath = backup_root_spec.split(':', 1)
        except ValueError:
          warning("missing backup_name")
          print("The following backup names exist:", file=sys.stderr)
          for backup_name in options.cloud_backup.keys():
            print(" ", backup_name, file=sys.stderr)
        else:
          with Pfx("backup_name %r", backup_name):
            if not is_identifier(backup_name):
              warning("not an identifier")
              badopts = True
          with Pfx("backup_root %r", backup_root_dirpath):
            if not isabspath(backup_root_dirpath):
              warning("backup_root not an absolute path")
              badopts = True
            else:
              if not isdirpath(backup_root_dirpath):
                warning("not a directory")
                badopts = True
              if use_realpath:
                backup_root_dirpath = realpath(backup_root_dirpath)
    subpaths = argv
    for subpath in subpaths:
      with Pfx("subpath %r", subpath):
        try:
          validate_subpath(subpath)
        except ValueError as e:
          warning(unpfx(str(e)))
          badopts = True
        else:
          subdirpath = joinpath(backup_root_dirpath, subpath)
          if islinkpath(subdirpath):
            linkpath = os.readlink(subdirpath)
            warning(
                "symbolic link -> %s, please use the real subpath", linkpath
            )
            badopts = True
          elif not isdirpath(subdirpath):
            warning("not a directory: %r", subdirpath)
            badopts = True
    if badopts:
      raise GetoptError("bad invocation")
    ##print(
    ##    "backup %s/%s => %s as %r" % (
    ##        backup_root_dirpath, (
    ##            ','.join(subpaths)
    ##            if len(subpaths) > 1 else subpaths[0] if subpaths else ''
    ##        ), options.cloud_backup.cloud_area.cloudpath, backup_name
    ##    )
    ##)
    # TODO: a facility to supply passphrases for use when recrypting
    # a per-file key under a new public key when the per-file key is
    # present under a different public key
    with UpdProxy() as proxy:
      proxy.prefix = f"{options.cmd} {backup_root_dirpath} => {backup_name}"
      options.cloud_backup.init()
      backup = options.cloud_backup.run_backup(
          options.cloud_area,
          backup_root_dirpath,
          subpaths or ('',),
          backup_name=backup_name,
          public_key_name=options.key_name,
          file_parallel=options.job_max,
      )
    for field, _, value_s in backup.report():
      print(field, ':', value_s)

  def cmd_dirstate(self, argv):
    ''' Usage: {cmd} {{ /dirstate/file/path.ndjson | backup_name subpath }} [subcommand ...]
          Do stuff with dirstate NDJSON files.
          Subcommands:
            rewrite Rewrite the state file with the latest lines
                    only, and with the backup listing cleaned up.
    '''
    options = self.options
    if not argv:
      raise GetoptError("missing pathname or backup_name")
    if isabspath(argv[0]):
      uu = None
      subpath = None
      dirstate_path = argv.pop(0)
    else:
      backup_name = argv.pop(0)
      if not argv:
        raise GetoptError("missing subpath")
      subpath = argv.pop(0)
      if subpath == '.':
        subpath = ''
      elif subpath:
        validate_subpath(subpath)
      backup = options.cloud_backup[backup_name]
      uu, dirstate_path = backup.dirstate_uuid_pathname(subpath)
      print('subpath', subpath, '=>', uu)
    print(dirstate_path)
    state = NamedBackup.dirstate_from_pathname(
        dirstate_path, uuid=uu, subpath=subpath
    )
    for record in state.by_uuid.values():
      record._clean_backups()
    ##print(state)
    by_name = {record.name: record for record in state.scan()}
    for name, record in sorted(by_name.items()):
      with Pfx(name):
        record._clean_backups()
    if argv:
      subcmd = argv.pop(0)
      with Pfx(subcmd):
        if subcmd == 'rewrite':
          if argv:
            raise GetoptError("extra arguments: %r" % (argv,))
          state.rewrite_backend()
        else:
          raise GetoptError("unrecognised subsubcommand")

  # pylint: disable=too-many-locals,too-many-branches
  def cmd_ls(self, argv):
    ''' Usage: {cmd} [-A] [-l] [backup_name [subpaths...]]
          Without a backup_name, list the named backups.
          With a backup_name, list the files in the backup.
          Options:
            -A  List all backup UUIDs.
            -l  Long mode - detailed listing.
    '''
    # TODO: list backup_uuids?
    # TODO: -U backup_uuid
    options = self.options
    badopts = False
    all_uuids = False
    long_mode = False
    opts, argv = getopt(argv, 'Al')
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-A':
          all_uuids = True
        elif opt == '-l':
          long_mode = True
        else:
          raise RuntimeError("unhandled option")
    cloud_backup = options.cloud_backup
    if not argv:
      for backup_name in cloud_backup.keys():
        print(backup_name)
        backup = cloud_backup[backup_name]
        backups_by_uuid = backup.backup_records.by_uuid
        if all_uuids:
          backup_uuids = map(
              lambda record: record.uuid, backup.sorted_backup_records()
          )
        else:
          latest_backup = backup.latest_backup_record()
          if not latest_backup:
            warning("%s: no backups", backup.name)
            return 1
          # pylint: disable=trailing-comma-tuple
          backup_uuids = latest_backup.uuid,
        for backup_uuid in backup_uuids:
          backup_record = backups_by_uuid[backup_uuid]
          # short form
          print(
              ' ',
              datetime.fromtimestamp(backup_record.timestamp_start
                                     ).isoformat(timespec='seconds'),
              backup_uuid,
              backup_record.root_path,
          )
          if long_mode:
            for field, value, value_s in backup_record.report(
                omit_fields=('uuid', 'root_path', 'timestamp_start')):
              print('   ', field, ':', value_s)
      return 0
    backup_name = argv.pop(0)
    if not is_identifier(backup_name):
      warning("backup_name %r: not an identifier", backup_name)
      badopts = True
    subpaths = argv or ('',)
    for subpath in subpaths:
      with Pfx("subpath %r", subpath):
        if subpath and subpath != '.':
          try:
            validate_subpath(subpath)
          except ValueError as e:
            warning("invalid subpath: %s", unpfx(str(e)))
            badopts = True
    if badopts:
      raise GetoptError("bad invocation")
    backup = cloud_backup[backup_name]
    backups_by_uuid = backup.backup_records.by_uuid
    if all_uuids:
      backup_uuids = list(
          map(lambda record: record.uuid, backup.sorted_backup_records())
      )
    else:
      latest_backup = backup.latest_backup_record()
      if not latest_backup:
        warning("%s: no backups", backup_name)
        return 1
      # pylint: disable=trailing-comma-tuple
      backup_uuids = latest_backup.uuid,
    subpaths = list(
        map(lambda subpath: '' if subpath == '.' else subpath, subpaths)
    )
    for subpath in subpaths:
      for subsubpath, details in backup.walk(
          subpath,
          backup_uuid=(backup_uuids[0] if len(backup_uuids) == 1 else None),
          all_backups=all_uuids,
      ):
        for name, name_details in sorted(details.items()):
          pathname = joinpath(subsubpath, name) if subsubpath else name
          if all_uuids:
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

  def cmd_new_key(self, argv):
    ''' Usage: {cmd}
          Generate a new key pair and print its name.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    options = self.options
    cloud_backup = options.cloud_backup
    passphrase = getpass("Passphrase for new key: ")
    cloud_backup.init()
    key_uuid = cloud_backup.new_key(passphrase)
    print(key_uuid)

  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
  def cmd_restore(self, argv):
    ''' Usage: {cmd} -o outputdir [-U backup_uuid] backup_name [subpaths...]
          Restore files from the named backup.
          Options:
            -o outputdir    Output directory to create to hold the
                            restored files.
            -U backup_uuid  The backup UUID from which to restore.
    '''
    # TODO: move the core logic into a CloudBackup method
    # TODO: list backup names if no backup_name
    # TODO: restore file to stdout?
    # TODO: restore files as tarball to stdout or filename
    # TODO: rsync-like include/exclude or files-from options?
    options = self.options
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
    subpaths = argv or ('',)
    for subpath in subpaths:
      with Pfx("subpath %r", subpath):
        if subpath and subpath != '.':
          try:
            validate_subpath(subpath)
          except ValueError as e:
            warning("invalid subpath: %s", unpfx(str(e)))
            badopts = True
    if badopts:
      raise GetoptError("bad invocation")
    subpaths = list(
        map(lambda subpath: '' if subpath == '.' else subpath, argv or ('',))
    )
    cloud_backup = options.cloud_backup
    backup = cloud_backup[backup_name]
    if backup_uuid is None:
      backup_record = backup.latest_backup_record()
      if backup_record is None:
        warning("%s: no backups", backup.name)
        return 1
      backup_uuid = backup_record.uuid
    else:
      with Pfx("backup_uuid %r", backup_uuid):
        backup_uuid = UUID(backup_uuid)
        backup_record = backup.backup_records.by_uuid[backup_uuid]
    content_area = CloudArea.from_cloudpath(backup_record.content_path)
    with Pfx("backup UUID %s", backup_uuid):
      public_key_name = backup_record.public_key_name
      with Pfx("key name %s", public_key_name):
        private_path = cloud_backup.private_key_path(public_key_name)
        if not isfilepath(private_path):
          error("private key file not found: %r", private_path)
          return 1
    passphrase = getpass(
        "Passphrase for backup %s (key %s): " % (backup_uuid, public_key_name)
    )
    # TODO: test passphrase against private key
    made_dirs = set()
    xit = 0
    print("mkdir", restore_dirpath)
    with Pfx("mkdir(%r)", restore_dirpath):
      os.mkdir(restore_dirpath, 0o777)
    made_dirs.add(restore_dirpath)
    with UpdProxy() as proxy:
      proxy.prefix = f"{options.cmd} {backup} "
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
                cloudpath = joinpath(content_area.basepath, hashpath)
                print(cloudpath, '=>', fspath)
                length = name_details.st_size
                download_progress = Progress(name=cloudpath, total=length)
                with UpdProxy() as dl_proxy:
                  with download_progress.bar(proxy=dl_proxy):
                    P = crypt_download(
                        content_area.cloud,
                        content_area.bucket_name,
                        cloudpath,
                        private_path=private_path,
                        passphrase=passphrase,
                        public_key_name=public_key_name,
                        download_progress=download_progress,
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
                retcode = P.wait()
                if retcode != 0:
                  error(
                      "openssl %r returns exit code %s" % (
                          P.args,
                          retcode,
                      )
                  )
                  xit = 1
                else:
                  with Pfx(
                      "utime(%r,%f:%s)",
                      fspath,
                      name_details.st_mtime,
                      datetime.fromtimestamp(name_details.st_mtime
                                             ).isoformat(),
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
                retrieved_hashcode = type(hashcode)(digester.digest())
                if hashcode != retrieved_hashcode:
                  error(
                      "integrity error: retrieved data hashcode %s"
                      " != expected hashcode %s", retrieved_hashcode, hashcode
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
class CloudBackup:
  ''' A named backup area.

      Local disc areas:
        state_dirpath/private_keys/uuid.private.pem
        state_dirpath/public_keys/uuid.public.pem
        state_dirpath/backups/name/
          backups.ndjson    uuid,timestamp,pubkeyname
          diruuids.ndjson   uuid,subpath
          dirstate          u/u/id.ndjson
  '''

  @strable(open_func=CloudArea.from_cloudpath)
  @typechecked
  def __init__(
      self,
      state_dirpath: str,
  ):
    ''' Initialise the `CloudBackup`.

        Parameters:
        * `state_dirpath`: a directory holding global state
    '''
    self.state_dirpath = state_dirpath
    self.backups_dirpath = joinpath(state_dirpath, 'backups')
    self.private_key_dirpath = joinpath(state_dirpath, 'private_keys')
    self.public_key_dirpath = joinpath(state_dirpath, 'public_keys')
    self.per_name_backup_records = {}
    self._lock = RLock()

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, self.state_dirpath)

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

  def keys(self):
    ''' The existing backup names.
    '''
    return filter(
        is_identifier,
        sorted(os.listdir(joinpath(self.state_dirpath, 'backups')))
    )

  def __getitem__(self, index):
    ''' Indexing by an identifier returns the associated `NamedBackup`.
    '''
    # TODO: index by UUID or str(UUID) returns a (NamedBackup,BackupRecord).
    if is_identifier(index):
      backup_name = index
      backup = NamedBackup(
          cloud_backup=self,
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
  @typechecked
  def run_backup(
      self,
      cloud_area: CloudArea,
      backup_root_dirpath: str,
      subpaths,
      *,
      backup_name,
      public_key_name=None,
      file_parallel=None,
  ) -> "BackupRecord":
    ''' Run a new backup of data from `backup_root_dirpath`,
        backing up everything from each `backup_root_dirpath/subpath` downward.
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
    if not isdirpath(backup_root_dirpath):
      raise ValueError(
          "backup_root_dirpath is not a directory: %r" %
          (backup_root_dirpath,)
      )
    for subpath in subpaths:
      if subpath:
        validate_subpath(subpath)
    named_backup = self[backup_name]
    assert isinstance(named_backup, NamedBackup)
    named_backup.init()
    with BackupRun(
        named_backup,
        backup_root_dirpath,
        cloud_area,
        public_key_name=public_key_name,
        file_parallel=file_parallel,
    ) as backup_run:
      for subpath in subpaths:
        backup_run.backup_subpath(subpath)
      backup_record = backup_run.backup_record
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
      backup_name=None,
      public_key_name: str,
      root_path=None,
      content_path: str,
      count_files_checked=0,
      count_files_changed=0,
      count_uploaded_bytes=0,
      count_uploaded_files=0,
      **kw
  ):
    super().__init__(**kw)
    self['backup_name'] = backup_name
    self['public_key_name'] = public_key_name
    self['root_path'] = root_path
    self['content_path'] = content_path
    self['count_files_checked'] = count_files_checked
    self['count_files_changed'] = count_files_changed
    self['count_uploaded_bytes'] = count_uploaded_bytes
    self['count_uploaded_files'] = count_uploaded_files
    self.content_area = CloudArea.from_cloudpath(content_path)

  def start(self, when=None):
    ''' Set `self.timestamp_start` to `when`, default `time.time()`.
    '''
    if when is None:
      when = time.time()
    self['timestamp_start'] = when

  def __enter__(self):
    self.start()
    return self

  def end(self, when=None):
    ''' Set `self.timestamp_end` to `when`, default `time.time()`.
    '''
    if when is None:
      when = time.time()
    self['timestamp_end'] = when

  def __exit__(self, exc_type, exc_value, exc_traceback):
    self.end()

  def report(self, first_fields=None, omit_fields=None):
    ''' Yield `(field,value,value_s)` for the fields of the backup record,
        being the field name, its value
        and its default human friendly representation as a `str`.
    '''
    if first_fields is None:
      first_fields = (
          'uuid',
          'backup_name',
          'public_key_name',
          'root_path',
          'content_path',
          'timestamp_start',
          'timestamp_end',
          'count_files_checked',
          'count_files_changed',
          'count_uploaded_files',
          'count_uploaded_bytes',
      )
    fields = set(self.keys())
    report_fields = []
    # start with the important fields in preferred order
    for field in first_fields:
      if field not in fields:
        continue
      if omit_fields and field in omit_fields:
        continue
      report_fields.append(field)
      fields.remove(field)
    for field in sorted(fields):
      if omit_fields and field in omit_fields:
        continue
      report_fields.append(field)
    for field in report_fields:
      try:
        value = self[field]
      except KeyError:
        continue
      try:
        if field.endswith('_bytes'):
          value_s = transcribe(value, BINARY_BYTES_SCALE, max_parts=2, sep=' ')
        elif field.startswith('timestamp_'):
          dt = datetime.fromtimestamp(value)
          value_s = "%s : %f" % (dt.isoformat(timespec='seconds'), value)
        else:
          value_s = str(value)
      except ValueError as e:
        warning("cannot present field %r=%r: %s", field, value, e)
        value_s = repr(value)
      yield field, value, value_s

class BackupRun(RunStateMixin):
  ''' State management and display for a multithreaded backup run.
  '''

  @fmtdoc
  @typechecked
  @require(
      lambda folder_parallel: folder_parallel is None or folder_parallel > 0
  )
  @require(lambda file_parallel: file_parallel is None or file_parallel > 0)
  def __init__(
      self,
      named_backup: "NamedBackup",
      root_dirpath: str,
      cloud_area: CloudArea,
      *,
      public_key_name: str,
      folder_parallel=None,
      file_parallel=None,
  ):
    ''' Initialise a `BackupRun`.

        Parameters:
        * `named_backup`: a `NamedBackup` to track the backup state
        * `cloud_area`: a `CloudArea` to contain the content uploads
        * `public_key_name`: the name of the public key
          to use with this backup run
        * `folder_parallel`: the number of filesystem directories to process
          in parallel, normally a small number; default `4`
        * `file_parallel`: the number of parallel file uploads to support,
          normally a not so small number, enough to get good throughput
          allowing for the latency of the cloud upload process;
          default from `DEFAULT_JOB_MAX`: `{DEFAULT_JOB_MAX}`
    '''
    if folder_parallel is None:
      folder_parallel = 4
    if file_parallel is None:
      file_parallel = DEFAULT_JOB_MAX
    if not isdirpath(root_dirpath):
      raise ValueError("root_dirpath nto a directory: %r" % (root_dirpath,))
    self.named_backup = named_backup
    self.root_dirpath = root_dirpath
    self.cloud_area = cloud_area
    self.public_key_name = public_key_name
    self.folder_parallel = folder_parallel
    self.file_parallel = file_parallel
    self.runstate = None
    self.content_path = cloud_area.subarea('content').cloudpath
    # mention resources here for lint
    self.backup_record = None
    self.status_proxy = None
    self.folder_later = None
    self.folder_proxies = set()
    self.file_later = None
    self.file_proxies = set()
    self.previous_interrupt = None
    self._stacked = []
    self._lock = RLock()

  def __enter__(self):
    ''' Commence a run, return `self`.

        This allocates display status lines for progress reporting,
        prepares a new `BackupRecord`, catches `SIGINT`,
        and commences a backup run.
    '''
    Upd().cursor_invisible()
    status_proxy = UpdProxy()

    upload_progress = OverProgress(name="uploads")
    upload_proxy = UpdProxy()
    update_uploads = lambda P, _: upload_proxy(
        P.status(None, upload_proxy.width)
    )
    upload_progress.notify_update.add(update_uploads)

    file_proxies = set(UpdProxy() for _ in range(self.file_parallel))
    folder_proxies = set(UpdProxy() for _ in range(self.folder_parallel))

    backup_record = BackupRecord(
        backup_name=self.named_backup.backup_name,
        public_key_name=self.public_key_name,
        root_path=self.root_dirpath,
        content_path=self.content_path,
    )
    status_proxy.prefix = "backup %s: " % (backup_record.uuid)

    runstate = RunState(
        "%s.runstate(%s,%s)" %
        (type(self).__name__, self.cloud_area, self.public_key_name)
    )

    def cancel_runstate(signum, frame):
      ''' Receive signal, cancel the `RunState`.
      '''
      warning("received signal %s", signum)
      runstate.cancel()
      ##if previous_interrupt not in (signal.SIG_IGN, signal.SIG_DFL, None):
      ##  previous_interrupt(signum, frame)

    # TODO: keep all the previous handlers and restore them in __exit__
    previous_interrupt = signal.signal(signal.SIGINT, cancel_runstate)
    signal.signal(signal.SIGTERM, cancel_runstate)
    signal.signal(signal.SIGALRM, cancel_runstate)
    previous_termios = modify_termios(0, clear_modes={'lflag': termios.ECHO})
    self._stacked.append(
        pushattrs(
            self,
            runstate=runstate,
            backup_record=backup_record,
            backup_uuid=backup_record.uuid,
            status_proxy=status_proxy,
            folder_later=Later(self.folder_parallel, inboundCapacity=16),
            folder_proxies=folder_proxies,
            file_later=Later(self.file_parallel, inboundCapacity=256),
            file_proxies=file_proxies,
            previous_interrupt=previous_interrupt,
            previous_termios=previous_termios,
            upload_progress=upload_progress,
        )
    )
    backup_record.start()
    runstate.start()
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    ''' Clean up after a backup run.
    '''
    self.named_backup.add_backup_record(self.backup_record)
    signal.signal(signal.SIGINT, self.previous_interrupt)
    if self.previous_termios:
      termios.tcsetattr(0, termios.TCSANOW, self.previous_termios)
    self.runstate.stop()
    self.backup_record.end()
    self.named_backup.add_backup_record(self.backup_record)
    for proxy in self.file_proxies:
      proxy.delete()
    for proxy in self.folder_proxies:
      proxy.delete()
    self.status_proxy.delete()
    Upd().cursor_visible()
    old_attrs = self._stacked.pop()
    popattrs(self, old_attrs.keys(), old_attrs)

  def backup_subpath(self, subpath):
    ''' Use this `BackupRun` to backup `subpath` from `self.root_dirpath`.
    '''
    if subpath:
      validate_subpath(subpath)
    return self.named_backup.backup_tree(self, self.root_dirpath, subpath)

  @contextmanager
  def folder_proxy(self):
    ''' Allocate and return a folder `UpdProxy` for use by a folder scan.
    '''
    with self._lock:
      proxy = self.folder_proxies.pop()
    try:
      yield proxy
    finally:
      proxy.reset()
      with self._lock:
        self.folder_proxies.add(proxy)

  @contextmanager
  def file_proxy(self):
    ''' Allocate and return a file `UpdProxy` for use by a file scan.
    '''
    with self._lock:
      proxy = self.file_proxies.pop()
    try:
      yield proxy
    finally:
      proxy.reset()
      with self._lock:
        self.file_proxies.add(proxy)

# pylint: disable=too-many-instance-attributes
class NamedBackup(SingletonMixin):
  ''' A record encapsulating a named set of backups.
  '''

  # pylint: disable=unused-argument
  @classmethod
  def _singleton_key(
      cls, *, cloud_backup: CloudBackup, backup_name: str, state_dirpath: str
  ):
    return cloud_backup, backup_name

  def __init__(
      self,
      *,
      cloud_backup: CloudBackup,
      backup_name: str,
      state_dirpath: str,
  ):
    ''' Initialise a `NamedBackup`.

        Parameters:
        * `uuid`: optional UUID for this backup run;
          one will be created if omitted
        * `cloud_backup`: the `CloudBackup` making this run
        * `backup_name`: the name of this backup, an identifier
        * `public_key_name`: the name of the public key used to encrypt uploads
    '''
    if hasattr(self, 'cloud_backup'):
      return
    if not is_identifier(backup_name):
      raise ValueError("backup_name is not an identifier: %r" % (backup_name,))
    self._lock = RLock()
    self.cloud_backup = cloud_backup
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
    self.backup_records = UUIDNDJSONMapping(
        joinpath(self.state_dirpath, 'backups.ndjson'), dictclass=BackupRecord
    )
    # TODO: not using _saved_hashcodes yet
    self._saved_hashcodes = set()

  def __str__(self):
    return "%s[%s]" % (self.cloud_backup, self.backup_name)

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

  def sorted_backup_records(self, key=None, reverse=False):
    ''' Return the `BackupRecord`s for this `NamedBackup`
        sorted by `key` and `reverse` as for the `sorted` builtin function.
        If `key` is a `str` the key function
        will access that field of the record.
        The default sort key is `'timestamp_start'`.
    '''
    if key is None:
      key = 'timestamp_start'
    if isinstance(key, str):
      field = key
      key = lambda backup_record: backup_record[field]
    return sorted(
        self.backup_records.by_uuid.values(), key=key, reverse=reverse
    )

  def latest_backup_record(self):
    ''' Return the latest `BackupRecord` by `.timestamp_start`
        or `None` if there are no records.
    '''
    records = self.sorted_backup_records()
    if not records:
      return None
    return records[-1]

  @typechecked
  def add_backup_record(self, backup_record: BackupRecord):
    ''' Record a new `BackupRecord`.
    '''
    self.backup_records.add(backup_record, exists_ok=True)

  ##############################################################
  # DirStates

  def _dirstate__uu_sp(self, subpath):
    ''' Return `UUIDedDict(uuid,subpath)` for `subpath`,
        creating it if necessary.
    '''
    if subpath:
      validate_subpath(subpath)
    with self._lock:
      uu_sp = self.diruuids.by_subpath.get(subpath)
      if uu_sp:
        uu = uu_sp.uuid
      else:
        uu = uuid4()
        uu_sp = UUIDedDict(uuid=uu, subpath=subpath)
        self.diruuids.add(uu_sp)
    return uu_sp

  def dirstate_uuid_pathname(self, subpath):
    ''' Return `(UUID,pathname)` for the `DirState` file for `subpath`.
    '''
    if subpath:
      validate_subpath(subpath)
    uu_sp = self._dirstate__uu_sp(subpath)
    uu = uu_sp.uuid
    uupath = uuidpath(uu, 2, 2, make_subdir_of=self.dirstates_dirpath)
    dirstate_path = joinpath(
        self.dirstates_dirpath, dirname(uupath), uu.hex
    ) + '.ndjson'
    return uu, dirstate_path

  @classmethod
  @typechecked
  def dirstate_from_pathname(
      cls,
      ndjson_path: str,
      *,
      uuid: [UUID, None],
      subpath: [str, None],
      create: bool = False,
  ):
    ''' Return a `UUIDNDJSONMapping` associated with the filename `ndjson_path`.
    '''
    if not ndjson_path.endswith('.ndjson'):
      warning(
          "%s.dirstate_from_pathname(%r): does not end in .ndjson",
          cls.__name__, ndjson_path
      )
    state = UUIDNDJSONMapping(
        ndjson_path, dictclass=FileBackupState, create=create
    )
    state.uuid = uuid
    state.subpath = subpath
    return state

  def dirstate(self, subpath: str):
    ''' Return the `DirState` for `subpath`.
    '''
    if subpath:
      validate_subpath(subpath)
    state = self._dirstates.get(subpath)
    if state is not None:
      return state
    with self._lock:
      state = self._dirstates.get(subpath)
      if state is not None:
        return state
      uu, dirstate_path = self.dirstate_uuid_pathname(subpath)
      state = self.dirstate_from_pathname(
          dirstate_path, uuid=uu, subpath=subpath, create=True
      )
      self._dirstates[subpath] = state
    return state

  # pylint: disable=too-many-branches
  def walk(self, subpath: str, *, backup_uuid=None, all_backups=False):
    ''' Walk the backups of `subpath`, yield `(subsubpath,details)`.
        Note that only subsubpaths with nondirectory children are yiedled.

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
        pathname = joinpath(subpath, name)
        if all_backups:
          details[name] = file_backups
          for each_backup in map(UUIDedDict, file_backups.backups):
            if S_ISDIR(each_backup.st_mode):
              q.append(pathname)
              break
          continue
        # locate the records for backup_uuid
        file_backup = None
        for each_backup in map(UUIDedDict, file_backups.backups):
          if each_backup.uuid == backup_uuid:
            file_backup = each_backup
            break
        if file_backup is None:
          ##print(pathname, "MISSING", repr(file_backups.backups))
          continue
        if S_ISDIR(file_backup.st_mode):
          q.append(pathname)
          continue
        details[name] = file_backup
      if details:
        yield subpath, details

  @typechecked
  def attach_subpath(
      self, backup_root_dirpath: str, subpath: str, *, backup_uuid: UUID
  ):
    ''' Attach the directory at `subpath` to the tree root.

        This is required because it is possible to go directly to
        a dirstate for a subpath, for example by backing up specific
        subpaths of a larger area.
    '''
    validate_subpath(subpath)
    while subpath:
      with Pfx(subpath):
        base = basename(subpath)
        updirpath = dirname(subpath)
        assert updirpath != '.'
        dirstate = self.dirstate(updirpath)
        base_backups = dirstate.by_name.get(base)
        if base_backups is None:
          base_backups = FileBackupState(name=base, backups=[])
        record = None
        for backup in base_backups.backups:
          if backup.uuid == backup_uuid:
            record = backup
            break
        if record is None:
          # not already mentioned under this backup_uuid
          # stat it, add it, update the dirstate
          fs_dirpath = joinpath(backup_root_dirpath, subpath)
          with Pfx("lstat(%r)", fs_dirpath):
            S = os.lstat(fs_dirpath)
            if not S_ISDIR(S.st_mode):
              raise ValueError("not a directory")
          base_backups.add_dir(backup_uuid=backup_uuid, stat=S)
          dirstate.add(base_backups, exists_ok=True)
        subpath = updirpath

  ##############################################################
  # Backup processes.

  @typechecked
  def backup_tree(
      self,
      backup_run: BackupRun,
      backup_root_dirpath: str,
      topsubpath: str,
  ):
    ''' Back up everything in `backup_root_dirpath/topsubpath`
        recording the results against `backup_record`.
    '''
    if not topsubpath:
      topdirpath = backup_root_dirpath
    else:
      validate_subpath(topsubpath)
      topdirpath = joinpath(backup_root_dirpath, topsubpath)
    status_proxy = backup_run.status_proxy
    Rs = []
    L = backup_run.folder_later
    runstate = backup_run.runstate
    status_proxy("attach %r", topsubpath)
    if topsubpath:
      self.attach_subpath(
          backup_root_dirpath, topsubpath, backup_uuid=backup_run.backup_uuid
      )
    for dirpath, dirnames, _ in os.walk(topdirpath):
      if runstate.cancelled:
        break
      subpath = relpath(dirpath, backup_root_dirpath)
      status_proxy("os.walk %s/", subpath)
      if subpath == '.':
        subpath = ''
      Rs.append(
          L.defer(
              self.backup_single_directory, backup_run, backup_root_dirpath,
              subpath
          )
      )
      # walk the children lexically ordered
      dirnames[:] = sorted(dirnames)
    if Rs:
      for R in progressbar(report(Rs), total=len(Rs),
                           label="%s: wait for subdirectories" % (topsubpath,),
                           proxy=status_proxy):
        try:
          R()
        except Exception as e:  # pylint: disable=broad-except
          exception("file backup fails: %s", e)
    status_proxy('')

  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
  @typechecked
  def backup_single_directory(
      self,
      backup_run: BackupRun,
      backup_root_dirpath,
      subpath,
  ):
    ''' Backup the immediate contents of a particular subdirectory.
        Return `True` if everything was successfully backed up,
        `False` otherwise.
    '''
    if subpath:
      validate_subpath(subpath)
    runstate = backup_run.runstate
    if runstate.cancelled:
      return False
    backup_record = backup_run.backup_record
    backup_uuid = backup_record.uuid
    dirpath = joinpath(backup_root_dirpath, subpath)
    with Pfx("backup_single_directory(%r)", dirpath):
      with backup_run.folder_proxy() as proxy:
        proxy.prefix = subpath + ': '
        with Pfx("scandir"):
          proxy("scandir")
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
        backup_records_by_uuid = self.backup_records.by_uuid
        names = set()
        L = backup_run.file_later
        Rs = []
        entries = sorted(dir_entries.items())
        if len(entries) >= 64:
          entries = progressbar(
              entries,
              label="scan",
              proxy=proxy,
              update_frequency=16,
          )
        else:
          proxy("scan %d entries", len(entries))
        for name, dir_entry in entries:
          if runstate.cancelled:
            break
          with Pfx(name):
            pathname = joinpath(dirpath, name)
            if name in names:
              warning("repeated")
              continue
            names.add(name)
            backup_record['count_files_checked'] += 1
            name_backups = dirstate.by_name.get(name)
            if name_backups is None:
              name_backups = FileBackupState(name=name, backups=[])
              dirstate.add(name_backups)
            try:
              stat = dir_entry.stat(follow_symlinks=False)
            except FileNotFoundError as e:
              warning("skipped: %s", e)
              continue
            except OSError as e:
              warning("skipped: %s", e)
              ok = False
              continue
            if dir_entry.is_symlink():
              try:
                link = readlink(pathname)
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
                ##print("CHANGED (no prevstate)", pathname)
                changed = True
              elif not S_ISREG(prevstate.st_mode):
                ##print("CHANGED (previous not a file)", pathname)
                changed = True
              else:
                prev_mode = prevstate.st_mode
                prev_mtime = prevstate.st_mtime
                prev_size = prevstate.st_size
                if not S_ISREG(prev_mode):
                  ##print("CHANGED (not regular file)", pathname)
                  changed = True
                else:
                  if (stat.st_mtime != prev_mtime
                      or stat.st_size != prev_size):
                    ##print("CHANGED (changed size/mtime)", pathname)
                    changed = True
                  else:
                    prev_backup_uuid = prevstate.uuid
                    prev_backup_record = backup_records_by_uuid.get(
                        prev_backup_uuid
                    )
                    changed = (
                        prev_backup_record is None
                        or backup_record.content_path !=
                        prev_backup_record.content_path
                    )
                    if changed:
                      ##print(
                      ##    "CHANGED (no previous or previous has different content_path)",
                      ##    pathname
                      ##)
                      pass
              if changed:
                backup_record['count_files_changed'] += 1
                rfilepath = joinpath(subpath, name)
                R = L.defer(
                    self.backup_filename,
                    backup_run,
                    backup_root_dirpath,
                    rfilepath,
                    prevstate=prevstate,
                )
                R.extra.update(name=name, pathname=pathname)
                Rs.append(R)
              else:
                # record the current stat and the previous hashcode
                name_backups.add_regular_file(
                    backup_uuid=backup_uuid,
                    stat=stat,
                    hashcode=prevstate.hashcode
                )
            else:
              warning("unsupported type st_mode=%o", stat.st_mode)
              ok = False
              continue
            dirstate.add(name_backups, exists_ok=True)

        if Rs:
          if runstate.cancelled:
            for R in Rs:
              R.cancel()
          for R in progressbar(report(Rs), total=len(Rs),
                               label="wait for uploads", proxy=proxy):
            try:
              # we get a fresh stat and hashcode from backup_filename
              # because the file might change while we're mucking about
              backedup_hashcode, backedup_stat = R()
            except CancellationError:
              ok = False
            except Exception as e:  # pylint: disable=broad-except
              exception("file backup fails: %s", e)
              ok = False
            else:
              if backedup_hashcode is None:
                ##warning("backup cancelled: %s", R.extra.pathname)
                pass
              else:
                name = R.extra.name
                name_backups = dirstate.by_name[name]
                name_backups.add_regular_file(
                    backup_uuid=backup_uuid,
                    stat=backedup_stat,
                    hashcode=backedup_hashcode
                )
                dirstate.add(name_backups, exists_ok=True)

        if dirstate.scan_errors or (
            dirstate.scan_length >= 64
            and dirstate.scan_length >= 3 * len(dirstate)):
          with Pfx(
              "rewrite %s: %d scan_errors, len=%d and scan_length=%d",
              dirstate,
              len(dirstate.scan_errors),
              len(dirstate),
              dirstate.scan_length,
          ):
            # serialise these because we can run out of file descriptors
            # in a multithread environment
            with self._lock:
              dirstate.rewrite_backend()

        proxy('')
      return ok

  # pylint: disable=too-many-locals,too-many-return-statements
  @typechecked
  def backup_filename(
      self,
      backup_run: BackupRun,
      backup_root_dirpath: str,
      subpath: str,
      *,
      prevstate,
  ):
    ''' Back up a single file *backup_root_dirpath*`/`*subpath*,
        return its stat and hashcode.
        Return `(None,None)` if cancelled.

        Checksum the file; if the same as `prevstate.hashcode`
        return the hashcode immediately.

        Otherwise upload the file contents against the hashcode
        and return the stat and hashcode.
    '''
    validate_subpath(subpath)
    assert prevstate is None or isinstance(prevstate, AttrableMappingMixin)
    runstate = backup_run.runstate
    if runstate.cancelled:
      return None, None
    filename = joinpath(backup_root_dirpath, subpath)
    with Pfx("backup_filename(%r)", filename):
      with backup_run.file_proxy() as proxy:
        proxy.prefix = subpath + ': '
        proxy("check against previous backup")
        backup_record = backup_run.backup_record
        cloud_backup = self.cloud_backup
        cloud = backup_record.content_area.cloud
        bucket_name = backup_record.content_area.bucket_name
        public_key_name = backup_record.public_key_name
        if runstate.cancelled:
          return None, None
        # checksum the file contents
        try:
          with open(filename, 'rb') as f:
            fd = f.fileno()
            ##fd = os.open(filename, O_RDONLY)
            fstat = os.fstat(fd)
            if not S_ISREG(fstat.st_mode):
              raise ValueError("not a regular file")
            hasher = DEFAULT_HASHCLASS.digester()
            if fstat.st_size == 0:
              # TODO: why upload empty files at all? back to the "inline small files" issue
              # can't mmap empty files, and in any case they're easy
              hashcode = DEFAULT_HASHCLASS(
                  DEFAULT_HASHCLASS.digester().digest()
              )
              if runstate.cancelled:
                return None, None
              self.upload_hashcode_content(
                  backup_record, fd, hashcode, length=fstat.st_size
              )
              return hashcode, fstat
            # compute hashcode from file contents
            hashcode = DEFAULT_HASHCLASS.digester()
            mm = mmap(fd, fstat.st_size, prot=PROT_READ)
            if runstate.cancelled:
              return None, None
            hasher.update(mm)
            hashcode = DEFAULT_HASHCLASS(hasher.digest())
        except OSError as e:
          warning("checksum: %s", e)
          return None, None
        # compute some crypt-side upload paths
        basepath = self.hashcode_path(hashcode)
        # TODO: a check_uploaded flag?
        if (prevstate and S_ISREG(prevstate.st_mode)
            and hashcode == prevstate.hashcode):
          # assume content already uploaded in the previous backup
          # TODO: check that? cloud.stat?
          if public_key_name == prevstate.public_key_name:
            # upload already has a perfile key encrypted
            # with the current public key
            return hashcode, fstat
          # previous upload used a different key
          # check if the upload is keyed against the current key
          if runstate.cancelled:
            return None, None
          _, key_subpath = upload_paths(
              basepath, public_key_name=public_key_name
          )
          if cloud.stat(bucket_name=bucket_name, path=key_subpath):
            # content already uploaded and keyed against the current key
            return hashcode, fstat
          # TODO: not against the current key, can we decrypt a different key?
          # the fall through here will be if no decryptable key is present
          # need to enumerate the upstream keys for which we have
          # local private keys by iterating over the local private key
          # names
          # look for a private key for which we already have a passphrase to hand
          for private_key_name in cloud_backup.private_key_names():
            passphrase = cloud_backup._passphrases.get(private_key_name)
            if passphrase is not None:
              other_private_path = cloud_backup.private_key_path(
                  private_key_name
              )
              print(
                  f"{filename}: recrypt passtext"
                  " from {private_key_name} to {public_key_name}..."
              )
              if runstate.cancelled:
                return None, None
              recrypt_passtext(
                  cloud,
                  bucket_name,
                  basepath,
                  old_key_name=private_key_name,
                  old_private_path=other_private_path,
                  old_passphrase=passphrase,
                  new_key_name=public_key_name,
                  new_public_path=self.cloud_backup
                  .public_key_path(public_key_name),
              )
              return hashcode, fstat
          # no private keys with known passphrases
          # TODO: if interactive, offer available keys, request passphrase
        # need to reupload
        # copy the file so that what we upload is stable
        # this includes a second hashcode pass, alas
        if runstate.cancelled:
          return None, None
        proxy("prepare upload")
        with NamedTemporaryCopy(
            filename,
            progress=65536,
            progress_label="snapshot " + filename,
            prefix='backup_filename__' + subpath.replace(os.sep, '_') + '__',
        ) as T:
          if runstate.cancelled:
            return None, None
          with open(T.name, 'rb') as f2:
            fd2 = f2.fileno()
            mm = mmap(fd2, 0, prot=PROT_READ)
            hasher = DEFAULT_HASHCLASS.digester()
            if runstate.cancelled:
              return None, None
            hasher.update(mm)
            hashcode = DEFAULT_HASHCLASS(hasher.digest())
            # upload the content if not already uploaded
            # TODO: shared by hashcode set of locks
            if runstate.cancelled:
              return None, None
            P = Progress(name="crypt upload " + subpath, total=len(mm))
            backup_run.upload_progress.add(P)
            with P.bar(proxy=proxy, label=''):
              self.upload_hashcode_content(
                  backup_record,
                  mm,
                  hashcode,
                  upload_progress=P,
                  length=len(mm)
              )
            backup_run.upload_progress.remove(P, accrue=True)
        return hashcode, fstat

  def upload_hashcode_content(
      self,
      backup_record: BackupRecord,
      f,
      hashcode,
      *,
      upload_progress=None,
      length,
  ):
    ''' Upload the contents of `f` under the supplied `hashcode`
        into the content area specified the `contentdir_cloudpath`.
    '''
    content_area = backup_record.content_area
    basepath = joinpath(content_area.basepath, self.hashcode_path(hashcode))
    file_info, *cloudpaths = crypt_upload(
        f,
        content_area.cloud,
        content_area.bucket_name,
        basepath,
        public_path=self.cloud_backup.public_key_path(
            backup_record.public_key_name
        ),
        public_key_name=backup_record.public_key_name,
        upload_progress=upload_progress,
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

  def __init__(self, **kw):
    super().__init__(**kw)
    try:
      backups = self['backups']
    except KeyError:
      pass
    else:
      backups[:] = map(
          lambda record:
          (record if isinstance(record, UUIDedDict) else UUIDedDict(**record)),
          backups
      )

  @pfx_method
  def _clean_backups(self):
    ''' Remove repeated backup entries (cruft from old bugs I hope).
    '''
    cleaned = []
    seen_uuids = set()
    for backup in self.backups:
      backup_uuid = backup.uuid
      if not isinstance(backup_uuid, UUID):
        warning("not a UUID: backup_uuid %r", backup_uuid)
      if backup_uuid in seen_uuids:
        warning("discard repeated entry for backup_uuid %r", backup.uuid)
        continue
      cleaned.append(backup)
      seen_uuids.add(backup_uuid)
    self.backups[:] = cleaned

  @pfx_method
  @typechecked
  def _new_backup(self, backup_uuid: UUID, stat):
    ''' Prepare a shiny new file state.
    '''
    backup_state = UUIDedDict(uuid=backup_uuid, st_mode=stat.st_mode)
    if S_ISREG(stat.st_mode):
      backup_state.update(st_mtime=stat.st_mtime, st_size=stat.st_size)
    uuids = set(map(lambda record: record.uuid, self.backups))
    assert all(map(lambda uuid: isinstance(uuid, UUID), uuids))
    if backup_uuid in uuids:
      raise ValueError(
          "backup_uuid %r already present: %r" % (backup_uuid, uuids)
      )
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
  sys.exit(CloudBackupCommand.run_argv(sys.argv))
