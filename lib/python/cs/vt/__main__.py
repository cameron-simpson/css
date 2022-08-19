#!/usr/bin/env python3
#
# Command script for venti-ish implementation.
#       - Cameron Simpson <cs@cskk.id.au> 01may2007
#

''' cs.vt command line utility.
'''

from collections import defaultdict
from contextlib import contextmanager, nullcontext
from datetime import datetime
import errno
from getopt import getopt, GetoptError
import logging
import os
from os.path import (
    basename,
    splitext,
    exists as existspath,
    expanduser,
    exists as pathexists,
    join as joinpath,
    isdir as isdirpath,
    isfile as isfilepath,
)
import shutil
from signal import SIGHUP, SIGINT, SIGQUIT, SIGTERM
from stat import S_ISREG
import sys
from typeguard import typechecked
from cs.buffer import CornuCopyBuffer
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.debug import ifdebug, dump_debug_threads, thread_dump
from cs.fileutils import file_data, shortpath
from cs.lex import hexify, get_identifier
import cs.logutils
from cs.logutils import (
    exception, error, warning, track, info, upd, debug, logTo
)
from cs.pfx import Pfx, pfx_method
from cs.progress import progressbar
from cs.tty import ttysize
from cs.units import BINARY_BYTES_SCALE
from cs.upd import print
import cs.x
from cs.x import X
from . import common, defaults, DEFAULT_CONFIG_PATH, DEFAULT_CONFIG_ENVVAR
from .archive import Archive, FileOutputArchive, CopyModes
from .blockify import (
    blocked_chunks_of,
    blocked_chunks_of2,
    top_block_for,
    blockify,
)
from .compose import get_store_spec
from .config import Config, Store
from .convert import expand_path
from .datafile import DataRecord, DataFilePushable
from .debug import dump_chunk, dump_Block
from .dir import Dir, FileDirent
from .hash import DEFAULT_HASHCLASS, HASHCLASS_BY_NAME
from .index import LMDBIndex
from .merge import merge
from .parsers import scanner_from_filename
from .paths import OSDir, OSFile, path_resolve
from .scan import (
    MIN_BLOCKSIZE,
    MAX_BLOCKSIZE,
    scanbuf,
    py_scanbuf,
    scanbuf2,
    py_scanbuf2,
    scan,
)
from .server import serve_tcp, serve_socket
from .store import ProxyStore, DataDirStore, ProgressStore
from .transcribe import parse

RANDOM_DEV = '/dev/urandom'

def main(argv=None):
  ''' Create a `VTCmd` instance and call its main method.
  '''
  return VTCmd(argv).run()

def mount_vtfs(argv=None):
  ''' Hook for "mount.vtfs": run the "mount" subcommand of the vt(1) command.
  '''
  if argv is None:
    argv = sys.argv
  argv = list(argv)
  argv0 = argv.pop(0)
  return main([argv0, "mount"] + argv)

class VTCmd(BaseCommand):
  ''' A main programme instance.
  '''

  VT_STORE_ENVVAR = 'VT_STORE'
  VT_CACHE_STORE_ENVVAR = 'VT_CACHE_STORE'
  DEFAULT_HASHCLASS_ENVVAR = 'VT_HASHCLASS'
  VT_LOGFILE_ENVVAR = 'VT_LOGFILE'
  DEFAULT_SIGNALS = SIGHUP, SIGINT, SIGQUIT, SIGTERM

  GETOPT_SPEC = 'C:S:f:h:Pqv'

  USAGE_KEYWORDS = {
      'VT_STORE_ENVVAR': VT_STORE_ENVVAR,
      'VT_CACHE_STORE_ENVVAR': VT_CACHE_STORE_ENVVAR,
      'DEFAULT_CONFIG_ENVVAR': DEFAULT_CONFIG_ENVVAR,
      'DEFAULT_CONFIG_PATH': DEFAULT_CONFIG_PATH,
      'DEFAULT_HASHCLASS_NAME': DEFAULT_HASHCLASS.HASHNAME,
      'DEFAULT_HASHCLASS_ENVVAR': DEFAULT_HASHCLASS_ENVVAR,
  }

  USAGE_FORMAT = '''Usage: {cmd} [option...] [profile] subcommand [arg...]
  Options:
    -C store  Specify the store to use as a cache.
              Specify "NONE" for no cache.
              Default: from ${VT_CACHE_STORE_ENVVAR} or "[cache]".
    -S store  Specify the store to use:
                [clause]        Specification from .vtrc.
                /path/to/dir    DataDirStore
                tcp:[host]:port TCPStore
                |sh-command     StreamStore via sh-command
              Default from ${VT_STORE_ENVVAR}, or "[default]", except for
              the "serve" subcommand which defaults to "[server]"
              and ignores ${VT_STORE_ENVVAR}.
    -f config Config file. Default from ${DEFAULT_CONFIG_ENVVAR},
              otherwise {DEFAULT_CONFIG_PATH}
    -h hashclass Hashclass for Stores. Default from ${DEFAULT_HASHCLASS_ENVVAR},
              otherwise {DEFAULT_HASHCLASS_NAME}
    -P        Progress: show a progress bar of top level Store activity.
    -q        Quiet; not verbose. Default if stderr is not a tty.
    -v        Verbose; not quiet. Default if stderr is a tty.
'''

  def apply_defaults(self):
    options = self.options
    # verbose if stderr is a tty
    try:
      options.verbose = sys.stderr.isatty()
    except AttributeError:
      options.verbose = False
    options.config_path = os.environ.get(
        'VT_CONFIG', expanduser(DEFAULT_CONFIG_PATH)
    )
    options.store_spec = None
    options.cache_store_spec = os.environ.get(
        self.VT_CACHE_STORE_ENVVAR, '[cache]'
    )
    options.dflt_log = os.environ.get(self.VT_LOGFILE_ENVVAR)
    options.hashname = os.environ.get(
        self.DEFAULT_HASHCLASS_ENVVAR, DEFAULT_HASHCLASS.HASHNAME
    )
    options.show_progress = False
    options.status_label = self.cmd

  def apply_opts(self, opts):
    ''' Apply the command line options mapping `opts` to `options`.
    '''
    options = self.options
    for opt, val in opts:
      if opt == '-C':
        if val == 'NONE':
          options.cache_store_spec = None
        else:
          options.cache_store_spec = val
      elif opt == '-S':
        # specify Store
        options.store_spec = val
      elif opt == '-f':
        options.config_path = val
      elif opt == '-h':
        options.hashname = val
      elif opt == '-P':
        options.show_progress = True
      elif opt == '-q':
        # quiet: not verbose
        options.verbose = False
      elif opt == '-v':
        # verbose: not quiet
        options.verbose = True
      else:
        raise RuntimeError("unhandled option: %s" % (opt,))
    options.hashclass = None
    if options.hashname is not None:
      try:
        options.hashclass = HASHCLASS_BY_NAME[options.hashname]
      except KeyError:
        raise GetoptError(
            "unrecognised hashname %r: I know %r" %
            (options.hashname, sorted(HASHCLASS_BY_NAME.keys()))
        )
    if options.verbose:
      self.loginfo.level = logging.INFO
    if options.dflt_log is not None:
      logTo(options.dflt_log, delay=True)
    options.config = Config(options.config_path)

  def handle_signal(self, sig, frame):
    ''' Override `BaseCommand.handle_signal`:
        - do a threaddump for `SIGQUIT`
        - run the default `handle_signal` method
        - exit the programme immediately if `SIGQUIT`
    '''
    if sig == SIGQUIT:
      thread_dump()
    # call the standard RunState signal handler
    self.options.runstate.handle_signal(sig, frame)
    if sig == SIGQUIT:
      sys.exit(1)

  @contextmanager
  def run_context(self):
    ''' Set up and tear down the surrounding context.
    '''
    with super().run_context():
      options = self.options
      cmd = self.cmd
      config = options.config
      runstate = options.runstate
      show_progress = options.show_progress
      with stackattrs(common, runstate=runstate, config=config):
        # redo these because defaults is already initialised
        with stackattrs(defaults, runstate=runstate,
                        show_progress=show_progress):
          if cmd in ("config", "dump", "init", "profile", "scan", "test"):
            yield
          else:
            # open the default Store
            if options.store_spec is None:
              if cmd == "serve":
                store_spec = '[server]'
              else:
                store_spec = os.environ.get(self.VT_STORE_ENVVAR, '[default]')
              options.store_spec = store_spec
            try:
              # set up the primary Store using the main programme RunState for control
              S = Store(options.store_spec, options.config)
            except (KeyError, ValueError) as e:
              raise GetoptError(
                  "unusable Store specification: %s: %s" %
                  (options.store_spec, e)
              )
            except Exception as e:
              exception(
                  "UNEXPECTED EXCEPTION: can't open store %r: %s",
                  options.store_spec, e
              )
              raise GetoptError(
                  "unusable Store specification: %s" % (options.store_spec,)
              )
            if options.cache_store_spec is None:
              cacheS = None
            else:
              try:
                cacheS = Store(options.cache_store_spec, options.config)
              except Exception as e:
                exception(
                    "can't open cache store %r: %s", options.cache_store_spec,
                    e
                )
                raise GetoptError(
                    "unusable Store specification: %s" %
                    (options.cache_store_spec,)
                )
              else:
                S = ProxyStore(
                    "%s:%s" % (cacheS.name, S.name),
                    read=(cacheS,),
                    read2=(S,),
                    copy2=(cacheS,),
                    save=(cacheS, S),
                    archives=((S, '*'),),
                )
                S.config = options.config
            if show_progress:
              S = ProgressStore(S)
              add_bar_cmgr = S.progress_add.bar("ADD")
              get_bar_cmgr = S.progress_get.bar("GET")
            else:
              add_bar_cmgr = nullcontext()
              get_bar_cmgr = nullcontext()
            with defaults.common_S(S):
              with S:
                with add_bar_cmgr:
                  with get_bar_cmgr:
                    yield
            if cacheS:
              cacheS.backend = None
      runstate.cancel()
      if ifdebug():
        dump_debug_threads()

  def cmd_benchmark(self, argv):
    ''' Usage: {cmd} mode [args...] [<data]
          Modes:
            blocked_chunks  Scan the data into edge aligned chunks without a parser.
            blocked_chunks2 Scan the data into edge aligned chunks without a parser.
            blockify        Scan the data into edge aligned Blocks without a parser.
            py_scanbuf      Run the old pure Python scanbuf against the data.
            py_scanbuf2     Run the new pure Python scanbuf against the data.
            read            Read from data.
            scan            Run the new scan function against the data.
            scanbuf         Run the old C scanbuf against the data.
            scanbuf2        Run the new C scanbuf2 against the data.
    '''
    runstate = self.options.runstate
    if not argv:
      raise GetoptError("missing mode")
    mode = argv.pop(0)
    if os.isatty(0):
      warning("reading data from %s", RANDOM_DEV)
      inbfr = CornuCopyBuffer.from_fd(
          os.open(RANDOM_DEV, os.O_RDONLY), readsize=1024 * 1024
      )
    else:
      inbfr = CornuCopyBuffer.from_fd(0, readsize=1024 * 1024)
    try:
      S = os.fstat(0)
    except OSError as e:
      warning("fstat(0): %s", e)
      length = None
    else:
      length = S.st_size or None
    with Pfx(mode):
      if mode == 'blocked_chunks':
        if argv:
          raise GetoptError("extra arguments: %r", argv)
        hash_value = 0
        for chunk in progressbar(
            blocked_chunks_of(inbfr),
            label=mode,
            ##update_min_size=65536,
            update_frequency=256,
            itemlenfunc=len,
            total=length,
            units_scale=BINARY_BYTES_SCALE,
            runstate=runstate,
            report_print=True,
        ):
          pass
      elif mode == 'blocked_chunks2':
        if argv:
          raise GetoptError("extra arguments: %r", argv)
        hash_value = 0
        for chunk in progressbar(
            blocked_chunks_of2(inbfr),
            label=mode,
            ##update_min_size=65536,
            update_frequency=256,
            itemlenfunc=len,
            total=length,
            units_scale=BINARY_BYTES_SCALE,
            runstate=runstate,
            report_print=True,
        ):
          pass
      elif mode == 'blockify':
        if argv:
          raise GetoptError("extra arguments: %r", argv)
        last_offset = 0
        for offset in progressbar(
            blockify(inbfr),
            label=mode,
            update_frequency=256,
            ##update_min_size=65536,
            itemlenfunc=len,
            total=length,
            units_scale=BINARY_BYTES_SCALE,
            runstate=runstate,
            report_print=True,
        ):
          last_offset = offset
      elif mode == 'py_scanbuf':
        if argv:
          raise GetoptError("extra arguments: %r", argv)
        hash_value = 0
        for chunk in progressbar(
            inbfr,
            label=mode,
            update_min_size=65536,
            itemlenfunc=len,
            total=length,
            units_scale=BINARY_BYTES_SCALE,
            runstate=runstate,
            report_print=True,
        ):
          hash_value, chunk_scan_offsets = py_scanbuf(hash_value, chunk)
      elif mode == 'py_scanbuf2':
        if argv:
          raise GetoptError("extra arguments: %r", argv)
        hash_value = 0
        for chunk in progressbar(
            inbfr,
            label=mode,
            update_min_size=65536,
            itemlenfunc=len,
            total=length,
            units_scale=BINARY_BYTES_SCALE,
            runstate=runstate,
            report_print=True,
        ):
          hash_value, chunk_scan_offsets = py_scanbuf2(
              chunk, hash_value, 0, MIN_BLOCKSIZE, MAX_BLOCKSIZE
          )
      elif mode == 'read':
        if argv:
          raise GetoptError("extra arguments: %r", argv)
        for chunk in progressbar(
            inbfr,
            label=mode,
            update_min_size=65536,
            itemlenfunc=len,
            total=length,
            units_scale=BINARY_BYTES_SCALE,
            runstate=runstate,
            report_print=True,
        ):
          pass
      elif mode == 'scan':
        if argv:
          raise GetoptError("extra arguments: %r", argv)
        last_offset = 0
        for offset in progressbar(
            scan(inbfr),
            label=mode,
            update_frequency=256,
            ##update_min_size=65536,
            itemlenfunc=lambda offset: offset - last_offset,
            total=length,
            units_scale=BINARY_BYTES_SCALE,
            runstate=runstate,
            report_print=True,
        ):
          last_offset = offset
      elif mode == 'scanbuf':
        if argv:
          raise GetoptError("extra arguments: %r", argv)
        hash_value = 0
        for chunk in progressbar(
            inbfr,
            label=mode,
            ##update_min_size=65536,
            update_frequency=128,
            itemlenfunc=len,
            total=length,
            units_scale=BINARY_BYTES_SCALE,
            runstate=runstate,
            report_print=True,
        ):
          hash_value, chunk_scan_offsets = scanbuf(hash_value, chunk)
      elif mode == 'scanbuf2':
        if argv:
          raise GetoptError("extra arguments: %r", argv)
        hash_value = 0
        for chunk in progressbar(
            inbfr,
            label=mode,
            ##update_min_size=65536,
            update_frequency=128,
            itemlenfunc=len,
            total=length,
            units_scale=BINARY_BYTES_SCALE,
            runstate=runstate,
            report_print=True,
        ):
          hash_value, chunk_scan_offsets = scanbuf2(
              chunk, hash_value, 0, MIN_BLOCKSIZE, MAX_BLOCKSIZE
          )
      else:
        raise GetoptError("unknown mode")
    return 0

  def cmd_cat(self, argv):
    ''' Usage: {cmd} filerefs...
          Concatentate the contents of the supplied filerefs to stdout.
    '''
    if not argv:
      raise GetoptError("missing filerefs")
    for fileref in argv:
      cat(fileref)
    return 0

  def cmd_config(self, argv):
    ''' Usage: {cmd}
          Recite the configuration.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    print(self.options.config.as_text().rstrip())
    return 0

  def cmd_dump(self, argv):
    ''' Usage: {cmd} objects...
          Dump various objects.
    '''
    if not argv:
      raise GetoptError("missing objects")
    hashclass = DEFAULT_HASHCLASS
    one_line = True
    _, columns = ttysize(1)
    if columns is None:
      columns = 80
    max_width = columns - 1
    xit = 0
    for path in argv:
      with Pfx(path):
        if path.endswith('.vtd'):
          print(path)
          try:
            fd = os.open(path, os.O_RDONLY)
          except OSError as e:
            warning("open: %s", e)
            xit = 1
            continue
          bfr = CornuCopyBuffer.from_fd(fd)
          for offset, DR, _ in DataRecord.scan_with_offsets(bfr):
            data = DR.data
            hashcode = hashclass(data)
            leadin = '%9d %16.16s' % (offset, hashcode)
            dump_chunk(data, leadin, max_width, one_line)
          os.close(fd)
        elif path.endswith('.lmdb'):
          print(path)
          lmdb = LMDBIndex(path[:-5], hashclass)
          with lmdb:
            for hashcode, entry in lmdb.items():
              print(hashcode, entry)
        else:
          warning("unsupported file type: %r", path)
    return xit

  def cmd_fsck(self, argv):
    ''' Usage: {cmd} objects...
          Data structure inspection/repair.
    '''
    if not argv:
      raise GetoptError("missing fsck objects")
    xit = 0
    for arg in argv:
      with Pfx(arg):
        try:
          o, offset = parse(arg)
        except ValueError as e:
          error("does not seem to be a transcription: %s", e)
          xit = 1
          continue
        if offset != len(arg):
          error("unparsed text: %r", arg[offset:])
          xit = 1
          continue
        try:
          fsck_func = o.fsck
        except AttributeError:
          error("unsupported object type: %s", type(o))
          xit = 1
          continue
        if fsck_func(recurse=True):
          info("OK")
        else:
          info("BAD")
          xit = 1
    return xit

  def cmd_httpd(self, argv):
    ''' Usage: {cmd} [httpd-argv...]
          Run the HTTP daemon.
    '''
    from .httpd import main as httpd_main
    httpd_main([self.cmd + ': ' + 'httpd'] + argv)

  def cmd_import(self, argv):
    ''' Usage: {cmd} [-oW] srcpath {{-|archivepath}}
          Import paths into the Store, print top Dirent for each.

        TODO: hook into vt.merge.
    '''
    runstate = self.options.runstate
    delete = False
    overlay = False
    whole_read = False
    opts, argv = getopt(argv, 'oW')
    for opt, _ in opts:
      with Pfx(opt):
        if opt == '-D':
          delete = True
        elif opt == '-o':
          overlay = True
        elif opt == '-W':
          whole_read = True
        else:
          raise RuntimeError("unhandled option: %r" % (opt,))
    if not argv:
      raise GetoptError("missing srcpath")
    srcpath = argv.pop(0)
    if not argv:
      raise GetoptError("missing archivepath")
    archivepath = argv.pop(0)
    if archivepath == '-':
      archivepath = None
    if argv:
      raise GetoptError("extra arguments: %s" % (' '.join(argv),))
    xit = 0
    if archivepath is None:
      D = Dir('.')
    else:
      with Pfx(repr(archivepath)):
        try:
          with open(archivepath, 'a'):
            pass
        except OSError as e:
          error("cannot open archive for append: %s", e)
          return 1
        last_entry = Archive(archivepath).last
        D = last_entry.dirent
      if D is None:
        dstbase, suffix = splitext(basename(archivepath))
        D = Dir(dstbase)
    with Pfx(srcpath):
      srcbase = basename(srcpath.rstrip(os.sep))
      dst = D.get(srcbase)
      if isdirpath(srcpath):
        src = OSDir(srcpath)
        if dst is None:
          dst = D.mkdir(srcbase)
        elif not dst.isdir:
          error('target name %r is not a directory', srcbase)
          xit = 1
        elif not merge(dst, src, runstate=runstate):
          error("merge failed")
          xit = 1
      elif isfilepath(srcpath):
        src = OSFile(srcpath)
        if dst is None or dst.isfile:
          D.file_fromchunks(srcbase, src.datafrom())
        else:
          error("name %r already imported: %s", srcbase, dst)
          xit = 1
      else:
        error("unsupported file type")
        xit = 1
    if archivepath is None:
      print(D)
    else:
      with Pfx(archivepath):
        if xit == 0:
          Archive(archivepath).update(D)
        else:
          warning("archive not updated")
    return xit

  def cmd_init(self, argv):
    ''' Usage: {cmd}
          Install a default config and initialise the configured datadir Stores.
    '''
    xit = 0
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    config = self.options.config
    config_path = config.path
    if not pathexists(config_path):
      info("write %r", config_path)
      with Pfx(config_path):
        with open(config_path, 'w') as cfgf:
          config.write(cfgf)
    basedir = config.basedir
    if not isdirpath(basedir):
      with Pfx("basedir"):
        info("mkdir %r", basedir)
        with Pfx("mkdir(%r)", basedir):
          try:
            os.mkdir(basedir)
          except OSError as e:
            error("%s", e)
            xit = 1
    for clause_name, clause in sorted(config.map.items()):
      with Pfx("%s[%s]", shortpath(config_path), clause_name):
        if clause_name == 'GLOBAL':
          continue
        store_type = clause.get('type')
        if store_type == 'datadir':
          S = config[clause_name]
          try:
            S.init()
          except OSError as e:
            error("%s", e)
            xit = 1
    return xit

  def cmd_ls(self, argv):
    ''' Usage: {cmd} [-R] dirrefs...
          Do a directory listing of the specified dirrefs.
    '''
    recurse = False
    if argv and argv[0] == "-R":
      recurse = True
      argv.pop(0)
    if not argv:
      raise GetoptError("missing dirrefs")
    xit = 0
    first = True
    for path in argv:
      with Pfx(path):
        if first:
          first = False
        else:
          print()
        D, offset = parse(path)
        if offset < len(path):
          warning("unparsed text: %r, skipping", path[offset:])
          xit = 1
          continue
        if not isinstance(D, Dir):
          warning("not a Dir specification, got: %s:%r", type(D).__name__, D)
          xit = 1
          continue
        ls(path, D, recurse, sys.stdout)
    return xit

  def cmd_mount(self, argv):
    ''' Usage: {cmd} [-ar] [-o options] special [mountpoint]
          Mount the specified special on the specified mountpoint directory.
          Requires FUSE support.
          -a            Mount all dates.
          -o options    Mount options: append, readonly.
          -r            Read only, synonym for "-o readonly".
    '''
    try:
      from .fuse import mount, umount
    except ImportError as e:
      error("FUSE support not configured: %s", e)
      return 1
    badopts = False
    all_dates = False
    append_only = False
    readonly = None
    opts, argv = getopt(argv, 'ao:r')
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-a':
          all_dates = True
        elif opt == '-o':
          for option in val.split(','):
            with Pfx(option):
              if option == '':
                pass
              elif option == 'append':
                append_only = True
              elif option == 'readonly':
                readonly = True
              else:
                warning("unrecognised option")
                badopts = True
        elif opt == '-r':
          readonly = True
        else:
          raise RuntimeError("unhandled option: %r" % (opt,))
    # special is either a D{dir} or [clause] or an archive pathname
    mount_store = defaults.S
    special_basename = None
    # the special may derive directly from a config Store clause
    try:
      special = argv.pop(0)
    except IndexError:
      special = None
      error("missing special")
      badopts = True
    else:
      with Pfx("special %r", special):
        try:
          fsname, readonly, special_store, specialD, special_basename, archive = \
              self.options.config.parse_special(special, readonly)
        except ValueError as e:
          error("invalid: %s", e)
          badopts = True
        else:
          if special_basename is not None:
            # Make the name for an explicit mount safer:
            # no path components, no dots (thus no leading dots).
            special_basename = \
                special_basename.replace(os.sep, '_').replace('.', '_')
          if special_store is not None and special_store is not mount_store:
            warning(
                "replacing default Store with Store from special %s ==> %s",
                mount_store, special_store
            )
            mount_store = special_store
    if argv:
      mountpoint = argv.pop(0)
    else:
      if special_basename is None:
        if not badopts:
          error(
              'missing mountpoint, and cannot infer mountpoint from special: %r',
              special
          )
          badopts = True
      else:
        mountpoint = special_basename
    if argv:
      subpath = argv.pop(0)
    else:
      subpath = None
    if argv:
      error("extra arguments: %s", ' '.join(argv))
      badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    if all_dates:
      readonly = True
    xit = 0
    mount_base = basename(mountpoint)
    with Pfx(special):
      if specialD is not None:
        # D{dir}
        E = specialD
      else:
        # pathname or Archive obtained from Store
        if archive is None:
          warning("no Archive, writing to stdout")
          archive = FileOutputArchive(sys.stdout)
        if all_dates:
          E = Dir(mount_base)
          for when, subD in archive:
            E[datetime.fromtimestamp(when).isoformat()] = subD
        else:
          try:
            entry = archive.last
          except OSError as e:
            error("can't access special: %s", e)
            return 1
          except ValueError as e:
            error("invalid contents: %s", e)
            return 1
          # no "last entry" (==> first use) - make an empty directory
          when = entry.when
          E = entry.dirent
          if E is None:
            E = Dir(mount_base)
            X("cmd_mount: new E=%r", E)
          else:
            ##dump_Dirent(E, recurse=True)
            if not E.isdir:
              error("expected directory, not file: %s", E)
              return 1
      if E.name == '.':
        info("rename %s from %r to %r", E, E.name, mount_base)
        E.name = mount_base
      with Pfx(mountpoint):
        need_rmdir = False
        if not isdirpath(mountpoint):
          # autocreate mountpoint
          info('mkdir %r ...', mountpoint)
          try:
            os.mkdir(mountpoint)
            need_rmdir = True
          except OSError as e:
            if e.errno == errno.EEXIST:
              error("mountpoint is not a directory")
              return 1
            raise
        T = None
        try:
          T = mount(
              mountpoint,
              E,
              S=mount_store,
              archive=archive,
              subpath=subpath,
              readonly=readonly,
              append_only=append_only,
              fsname=fsname
          )
          cs.x.X_via_tty = True
        except KeyboardInterrupt:
          error("keyboard interrupt, unmounting %r", mountpoint)
          xit = umount(mountpoint)
        except Exception as e:
          exception("unexpected exception: %s", e)
          xit = 1
        finally:
          if T:
            T.join()
      if need_rmdir:
        info("rmdir %r ...", mountpoint)
        try:
          os.rmdir(mountpoint)
        except OSError as e:
          error("%r: rmdir fails: %s", mountpoint, e)
          xit = 1
    return xit

  def cmd_pack(self, argv):
    ''' Usage: {cmd} ospath
          Replace the ospath with an archive file ospath.vt
          referring to the stored content of path.
    '''
    if not argv:
      raise GetoptError("missing path")
    ospath = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments after path: %r" % (argv,))
    modes = CopyModes(trust_size_mtime=True)
    with Pfx(ospath):
      if not pathexists(ospath):
        error("missing")
        return 1
      arpath = ospath + '.vt'
      A = Archive(arpath, missing_ok=True)
      last_entry = A.last
      when, target = last_entry.when, last_entry.dirent
      if target is None:
        target = Dir(basename(ospath))
      if isdirpath(ospath):
        source = OSDir(ospath)
      else:
        source = OSFile(ospath)
      X("target = %s, source= %s", type(target), type(source))
      if not merge(target, source, runstate=self.options.runstate):
        error("merge into %r fails", arpath)
        return 1
      A.update(target)
      info("remove %r", ospath)
      if isdirpath(ospath):
        shutil.rmtree(ospath)
      else:
        os.remove(ospath)
    return 0

  def cmd_profile(self, argv):
    ''' Usage: {cmd} other-vt-subcommand [argv...]
          Wrapper to profile other subcommands and report.
    '''
    try:
      import cProfile as profile
    except ImportError:
      import profile
    if not argv:
      cmd_method = None
    else:
      subcmd = argv.pop(0)
      try:
        cmd_method = getattr(self, self.SUBCOMMAND_METHOD_PREFIX + subcmd)
      except AttributeError:
        raise GetoptError("no subcommand %r" % (subcmd,))
    P = profile.Profile()
    P.enable()
    try:
      xit = cmd_method(argv)
    finally:
      P.disable()
    P.create_stats()
    P.print_stats(sort='cumulative')
    return xit

  @pfx_method
  def _parse_pushable(self, s):
    ''' Parse an object specification and return the object.
    '''
    obj = None
    if s.startswith('/'):
      # a path, hopefully a datadir or a .vtd file
      if isdirpath(s) and isdirpath(joinpath(s, 'data')):
        # /path/to/datadir
        obj = DataDirStore(s, s)
      elif s.endswith('.vtd') and isfilepath(s):
        # /path/to/datafile.vtd
        obj = DataFilePushable(s)
      # TODO: /path/to/archive.vt
      else:
        raise ValueError("path is neither a DataDir nor a data file")
    else:
      # try a Store specification
      try:
        obj = Store(s, self.options.config)
      except ValueError:
        # try an object transcription eg "D{...}"
        try:
          obj, offset = parse(s)
        except ValueError:
          # fall back: relative path to .vtd file
          if s.endswith('.vtd') and isfilepath(s):
            # /path/to/datafile.vtd
            obj = DataFilePushable(s)
          else:
            raise
        else:
          if offset < len(s):
            raise ValueError("incomplete parse, unparsed: %r" % (s[offset:],))
    if not hasattr(obj, 'pushto_queue'):
      raise ValueError("type %s is not pushable" % (type(obj),))
    return obj

  @staticmethod
  def _push(options, srcS, dstS, pushables):
    ''' Push data from the source Store `srcS` to destination Store `dstS`
        to ensure that `dstS` has all the Blocks needs to support
        the `pushables`.
    '''
    xit = 0
    with Pfx("%s => %s", srcS.name, dstS.name):
      runstate = options.runstate
      Q, T = srcS.pushto(dstS, progress=options.progress)
      try:
        for pushable in pushables:
          if runstate.cancelled:
            xit = 1
            break
          with Pfx(str(pushable)):
            pushed_ok = pushable.pushto_queue(
                Q, runstate=runstate, progress=options.progress
            )
            assert isinstance(pushed_ok, bool)
            if not pushed_ok:
              error("push failed")
              xit = 1
      finally:
        Q.close()
        T.join()
      return xit

  def cmd_pullfrom(self, argv):
    ''' Usage: {cmd} other_store [objects...]
          Pull missing content from other Stores.
    '''
    if not argv:
      raise GetoptError("missing other_store")
    srcSspec = argv.pop(0)
    with Pfx("other_store %r", srcSspec):
      srcS = Store(srcSspec, self.options.config)
    if not argv:
      argv = (srcSspec,)
    dstS = defaults.S
    pushables = []
    ok = True
    for obj_spec in argv:
      with Pfx(obj_spec):
        try:
          obj = self._parse_pushable(obj_spec)
        except ValueError as e:
          warning("unrecognised pushable: %s", e)
          ok = False
        else:
          pushables.append(obj)
    if not ok:
      raise GetoptError("unrecognised pushables")
    return self._push(self.options, srcS, dstS, pushables)

  def cmd_pushto(self, argv):
    ''' Usage: {cmd} other_store [objects...]
          Push something to a secondary Store,
          such that the secondary store has all the required Blocks.
    '''
    if not argv:
      raise GetoptError("missing other_store")
    srcS = defaults.S
    dstSspec = argv.pop(0)
    if not argv:
      argv = (dstSspec,)
    with Pfx("other_store %r", dstSspec):
      dstS = Store(dstSspec, self.options.config)
    pushables = []
    ok = True
    for obj_spec in argv:
      with Pfx(obj_spec):
        try:
          obj = self._parse_pushable(obj_spec)
        except ValueError as e:
          warning("unrecognised pushable: %s", e)
          ok = False
        else:
          pushables.append(obj)
    if not ok:
      raise GetoptError("unrecognised pushables")
    return self._push(srcS, dstS, pushables)

  def cmd_save(self, argv):
    ''' Usage: {cmd} [-F] [{{ospath|-}}...]
          Save the contents of each ospath to the Store and print a fileref 
          or dirref for each.
          The argument "-" reads data from standard input and prints a fileref.
          The default argument list is "-".
          -F  Print a FileDirent instead of a block ref for file contents.
    '''
    runstate = self.options.runstate
    use_filedirent = False
    if argv and argv[0] == '-F':
      use_filedirent = True
      argv.pop(0)
    if not argv:
      argv = ['-']
    xit = 0
    for ospath in argv:
      with Pfx(ospath):
        if ospath == '-':
          chunks = CornuCopyBuffer.from_fd(0)
          try:
            st = os.fstat(0)
          except OSError as e:
            warning("fstat(0): %s", e)
            st = None
        elif not existspath(ospath):
          error("missing")
          xit = 1
          continue
        elif isdirpath(ospath):
          target = Dir(basename(ospath))
          source = OSDir(ospath)
          merge(target, source, runstate=self.options.runstate)
          print(target, ospath)
          continue
        else:
          try:
            st = os.stat(ospath)
          except OSError as e:
            warning("stat(%r): %s", ospath, e)
            st = None
          chunks = CornuCopyBuffer.from_filename(ospath, readsize=1024 * 1024)
        block = top_block_for(
            progressbar(
                blockify(chunks),
                label=ospath,
                itemlenfunc=len,
                units_scale=BINARY_BYTES_SCALE,
                runstate=runstate,
                update_frequency=64,
                total=(
                    st.st_size
                    if st is not None and S_ISREG(st.st_mode) else None
                ),
            )
        )
        if runstate.cancelled:
          error("cancelled")
          xit = 1
          break
        print(
            FileDirent(ospath, block=block) if use_filedirent else block,
            ospath
        )
    return xit

  def cmd_serve(self, argv):
    ''' Usage: {cmd} [{{DEFAULT|-|/path/to/socket|[host]:port}} [name:storespec]...]
          Start a service daemon listening on a TCP port
          or on a UNIX domain socket or on stdin/stdout.
          With no `name:storespec` arguments the default Store is served,
          otherwise the named Stores are exported with the first being
          served initially.
    '''
    if argv:
      address = argv.pop(0)
    else:
      address = 'DEFAULT'
    if address == 'DEFAULT':
      # obtain the address from the [server] config clause
      try:
        clause = self.options.config.get_clause('server')
      except KeyError:
        raise GetoptError(
            "no [server] clause to implement address %r" % (address,)
        )
      try:
        address = clause['address']
      except KeyError:
        raise GetoptError("[server] clause: no address field")
    if not argv:
      exports = {'': defaults.S}
    else:
      exports = {}
      for named_store_spec in argv:
        with Pfx("name:storespec %r", named_store_spec):
          name, offset = get_identifier(named_store_spec)
          if not name:
            raise GetoptError("missing name")
          with Pfx(repr(name)):
            if name in exports:
              raise GetoptError("repeated name")
            if not named_store_spec.startswith(':', offset):
              raise GetoptError("missing colon after name")
            offset += 1
            try:
              parsed, type_, params, offset = get_store_spec(
                  named_store_spec, offset
              )
            except ValueError as e:
              raise GetoptError(
                  "invalid Store specification after \"name:\": %s" % (e,)
              ) from e
            if offset < len(named_store_spec):
              raise GetoptError(
                  "extra text after storespec: %r" %
                  (named_store_spec[offset:],)
              )
            namedS = self.options.config.new_Store(parsed, type_, **params)
            exports[name] = namedS
            if '' not in exports:
              exports[''] = namedS
    runstate = self.options.runstate
    if address == '-':
      track("dispatch StreamStore(%r,stdin,stdout,..)", address)
      from .stream import StreamStore
      remoteS = StreamStore("serve -", sys.stdin, sys.stdout, exports=exports)
      remoteS.join()
    elif '/' in address:
      # path/to/socket
      socket_path = expand_path(address)
      track("dispatch serve_socket(%r,...)", socket_path)
      with defaults.S:
        srv = serve_socket(
            socket_path=socket_path, exports=exports, runstate=runstate
        )
      srv.join()
    else:
      # [host]:port
      track("dispatch serve_tcp(%r,...)", address)
      cpos = address.rfind(':')
      if cpos >= 0:
        host = address[:cpos]
        port = address[cpos + 1:]
        if not host:
          host = '127.0.0.1'
        port = int(port)
        with defaults.S:
          srv = serve_tcp(
              bind_addr=(host, port), exports=exports, runstate=runstate
          )
          runstate.notify_cancel.add(lambda runstate: srv.shutdown())
        srv.join()
      else:
        raise GetoptError(
            "invalid serve argument,"
            " I expect \"-\" or \"/path/to/socket\" or \"[host]:port\", got: %r"
            % (address,)
        )
    return 0

  def cmd_test(self, argv):
    ''' Usage: {cmd} subtest [subtestargs...]
          Test various facilites.
          blockify filenames... Blockify the contents of the filenames.
    '''
    if not argv:
      raise GetoptError("missing test subcommand")
    subcmd = argv.pop(0)
    with Pfx(subcmd):
      if subcmd == 'blockify':
        if not argv:
          raise GetoptError("missing filename")
        filename = argv.pop(0)
        with Pfx(filename):
          if argv:
            raise GetoptError("extra arguments after filename: %r" % (argv,))
          scanner = scanner_from_filename(filename)
          size_counts = defaultdict(int)
          with open(filename, 'rb') as fp:
            for chunk in blocked_chunks_of2(file_data(fp, None), scanner):
              print(len(chunk), str(chunk[:16]))
              size_counts[len(chunk)] += 1
          for size, count in sorted(size_counts.items()):
            print(size, count)
        return 0
      raise GetoptError("unrecognised subcommand")

  def cmd_unpack(self, argv):
    ''' Usage: {cmd} archive.vt [unpacked]
          Unpack arpath to unpacked. If unpacked is omitted, unpack
          from the archive file _archive_`.vt` as _archive_.
    '''
    if not argv:
      raise GetoptError("missing archive name")
    arpath = argv.pop(0)
    if argv:
      targetpath = argv.pop(0)
    else:
      targetpath, arext = splitext(arpath)
      if arext != '.vt':
        raise GetoptError("archive name does not end in .vt: %r" % (arpath,))
    if argv:
      raise GetoptError(
          "extra arguments after unpacked %r: %r" % (
              targetpath,
              argv,
          )
      )
    if pathexists(targetpath):
      error("unpacked %r already exists", targetpath)
      return 1
    with Pfx(arpath):
      entry = Archive(arpath).last
      source = entry.dirent
      if source is None:
        error("no entries in archive")
        return 1
      if source.isdir:
        target = OSDir(targetpath)
      else:
        target = OSFile(targetpath)
    with Pfx(targetpath):
      if not merge(target, source, runstate=self.options.runstate):
        return 1
    return 0

def lsDirent(fp, E, name):
  ''' Transcribe a Dirent as an ls-style listing.
  '''
  B = E.block
  st = E.stat()
  st_mode, st_ino, st_dev, st_nlink, st_uid, st_gid, st_size, \
      st_atime, st_mtime, st_ctime = st
  t = datetime.fromtimestamp(int(st_mtime))
  try:
    h = B.hashcode
  except AttributeError:
    detail = repr(B)
  else:
    detail = hexify(h)
  fp.write(
      "%c %-41s %s %6d %s\n" %
      (('d' if E.isdir else 'f'), detail, t, st_size, name)
  )

@typechecked
def ls(path: str, D: Dir, recurse: bool, fp=None):
  ''' Do an ls style directory listing with optional recursion.
  '''
  if fp is None:
    fp = sys.stdout
  fp.write('\n')
  fp.write(path)
  fp.write(":\n")
  if not recurse:
    debug("ls(): getting dirs and files...")
    names = D.dirs() + D.files()
    debug("ls(): got dirs and files = %s" % (names,))
    names.sort()
    for name in names:
      debug("ls(): D=%s (%s), name=%s" % (D, type(D), name))
      E = D[name]
      lsDirent(fp, E, name)
  else:
    dirs = D.dirs()
    dirs.sort()
    files = D.files()
    files.sort()
    for name in files:
      E = D[name]
      if E.isdir:
        warning("%s: expected file, found directory", name)
      lsDirent(fp, E, name)
    for name in dirs:
      ls(joinpath(path, name), D.chdir1(name), recurse, fp)

def cat(path, fp=None):
  ''' Write a file to the output, like cat(1).
  '''
  if fp is None:
    with os.fdopen(sys.stdout.fileno(), "wb") as bfp:
      cat(path, bfp)
  else:
    F = path_resolve(path)
    block = F.block
    for B in block.leaves:
      fp.write(B.data)

def dump(path, fp=None):
  ''' Dump the Block contents of `path`.
  '''
  if fp is None:
    fp = sys.stdout
  E = path_resolve(path)
  dump_Block(E.block, fp)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
