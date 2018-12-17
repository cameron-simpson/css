#!/usr/bin/env python3
#
# Command script for venti-ish implementation.
#       - Cameron Simpson <cs@cskk.id.au> 01may2007
#

''' cs.vt command line utility.
'''

from __future__ import with_statement
from collections import defaultdict
from datetime import datetime
import errno
from getopt import getopt, GetoptError
import logging
import os
from os.path import basename, splitext, \
    exists as existspath, join as joinpath, \
    isdir as isdirpath, isfile as isfilepath
import shutil
from signal import signal, SIGINT, SIGHUP, SIGQUIT
import sys
from threading import Thread
from time import sleep
from cs.debug import ifdebug, dump_debug_threads, thread_dump
from cs.env import envsub
from cs.fileutils import file_data
from cs.lex import hexify, get_identifier
import cs.logutils
from cs.logutils import exception, error, warning, info, debug, \
                        setup_logging, logTo, loginfo
from cs.pfx import Pfx
from cs.resources import RunState
from cs.tty import statusline, ttysize
import cs.x
from cs.x import X
from . import fromtext, defaults
from .archive import Archive, CopyModes, copy_out_dir, copy_out_file
from .block import BlockRecord
from .blockify import blocked_chunks_of
from .compose import get_store_spec
from .config import Config, Store
from .convert import expand_path
from .datadir import DataDirIndexEntry
from .datafile import DataFileReader
from .debug import dump_chunk, dump_Block, dump_Store
from .dir import Dir
from .fsck import fsck_Block, fsck_dir
from .hash import DEFAULT_HASHCLASS
from .index import LMDBIndex
from .merge import merge
from .parsers import scanner_from_filename
from .paths import OSDir, OSFile, decode_Dirent_text, dirent_dir, dirent_file, dirent_resolve
from .server import serve_tcp, serve_socket
from .smuggling import import_dir, import_file
from .store import ProgressStore, ProxyStore
from .transcribe import parse

def main(argv=None):
  ''' Create a VTCmd instance and call its main method.
  '''
  return VTCmd().main(argv=argv)

class VTCmd:
  ''' A main programme instance.
  '''

  USAGE = '''Usage: %s [options...] [profile] operation [args...]
  Options:
    -C store  Specify the store to use as a cache.
              Specify "NONE" for no cache.
              Default: from $VT_CACHE_STORE or "[cache]".
    -S store  Specify the store to use:
                [clause]        Specification from .vtrc.
                /path/to/dir    GDBMStore
                tcp:[host]:port TCPStore
                |sh-command     StreamStore via sh-command
              Default from $VT_STORE, or "[default]", except for
              the "serve" operation which defaults to "[server]"
              and ignores $VT_STORE.
    -f config Config file. Default from $VT_CONFIG, otherwise ~/.vtrc
    -q        Quiet; not verbose. Default if stderr is not a tty.
    -v        Verbose; not quiet. Default if stderr is a tty.
  Operations:
    cat filerefs...
    dump {datafile.vtd|index.gdbm|index.lmdb}
    fsck block blockref...
    import [-oW] path {-|archive.vt}
    ls [-R] dirrefs...
    mount [-a] [-o {append_only,readonly}] [-r] {Dir|config-clause|archive.vt} [mountpoint [subpath]]
      -a  All dates. Implies readonly.
      -o options
          Mount options:
            append_only Files may not be truncated or overwritten.
            readonly    Read only; data may not be modified.
      -r  Readonly, the same as "-o readonly".
    pack path
    pullfrom other-store objects...
    pushto other-store objects...
    serve [{DEFAULT|-|/path/to/socket|host:port} [name:storespec]...]
    test blockify file
    unpack archive.vt
'''

  def main(self, argv=None, environ=None, verbose=None):
    ''' The main function for this programme.
    '''
    global loginfo
    if argv is None:
      argv = sys.argv
    self.argv = argv
    if environ is None:
      environ = os.environ

    if verbose is None:
      # verbose if stderr is a tty
      try:
        verbose = sys.stderr.isatty()
      except AttributeError:
        verbose = False
    self.verbose = verbose

    cmd = basename(argv[0])
    args = argv[1:]
    if cmd.endswith('.py'):
      cmd = 'vt'
    self.cmd = cmd

    usage = self.USAGE % (cmd,)

    badopts = False

    setup_logging(cmd_name=cmd, upd_mode=sys.stderr.isatty(), verbose=self.verbose)
    ####cs.x.X_logger = logging.getLogger()

    config_path = os.environ.get('VT_CONFIG', envsub('$HOME/.vtrc'))
    store_spec = None
    cache_store_spec = os.environ.get('VT_CACHE_STORE', '[cache]')
    dflt_log = os.environ.get('VT_LOGFILE')

    try:
      opts, args = getopt(args, 'C:S:f:qv')
    except GetoptError as e:
      error("unrecognised option: %s: %s", e.opt, e.msg)
      badopts = True
      opts, args = [], []

    for opt, val in opts:
      if opt == '-C':
        if val == 'NONE':
          cache_store_spec = None
        else:
          cache_store_spec = val
      elif opt == '-S':
        # specify Store
        store_spec = val
      elif opt == '-f':
        config_path = val
      elif opt == '-q':
        # quiet: not verbose
        self.verbose = False
      elif opt == '-v':
        # verbose: not quiet
        self.verbose = True
      else:
        raise RuntimeError("unhandled option: %s" % (opt,))

    self.config_path = config_path
    self.store_spec = store_spec
    self.cache_store_spec = cache_store_spec

    if self.verbose:
      loginfo.level = logging.INFO
      upd = loginfo.upd
      if upd is not None:
        upd.nl_level = logging.INFO

    if dflt_log is not None:
      logTo(dflt_log, delay=True)

    xit = None
    self.runstate = RunState("main")
    with defaults.push_runstate(self.runstate):
      self.config = Config(self.config_path)

      # catch signals, flag termination
      def sig_handler(sig, frame):
        ''' Signal handler
        '''
        warning("received signal %s from %s", sig, frame)
        if sig == SIGQUIT:
          thread_dump()
        X("%s.cancel()...", self.runstate)
        self.runstate.cancel()
        if sig == SIGQUIT:
          sys.exit(1)
      signal(SIGHUP, sig_handler)
      signal(SIGINT, sig_handler)
      signal(SIGQUIT, sig_handler)

      try:
        xit = self.cmd_op(args)
      except GetoptError as e:
        error("%s", e)
        badopts = True

      if badopts:
        sys.stderr.write(usage)
        return 2

      if not isinstance(xit, int):
        raise RuntimeError("exit code not set by operation: %r" % (xit,))

      if ifdebug():
        dump_debug_threads()

    return xit

  def cmd_op(self, args):
    ''' Run a command operation from `args`.
    '''
    try:
      op = args[0]
    except IndexError:
      raise GetoptError("missing command")
    args = args[1:]
    with Pfx(op):
      if op == "profile":
        return self.cmd_profile(args)
      try:
        op_func = getattr(self, "cmd_" + op)
      except AttributeError:
        raise GetoptError("unknown operation \"%s\"" % (op,))
      # these commands run without a context Store
      if op in ("dump", "scan", "test"):
        return op_func(args)
      # open the default Store
      if self.store_spec is None:
        if op == "serve":
          store_spec = '[server]'
        else:
          store_spec = os.environ.get('VT_STORE', '[default]')
        self.store_spec = store_spec
      try:
        # set up the primary Store using the main programme RunState for control
        S = Store(self.store_spec, self.config, runstate=self.runstate)
      except ValueError as e:
        raise GetoptError("unusable Store specification: %s: %s" % (self.store_spec, e))
      except Exception as e:
        exception("UNEXPECTED EXCEPTION: can't open store %r: %s", self.store_spec, e)
        raise GetoptError("unusable Store specification: %s" % (self.store_spec,))
      defaults.push_Ss(S)
      if self.cache_store_spec is None:
        cacheS = None
      else:
        try:
          cacheS = Store(self.cache_store_spec, self.config)
        except Exception as e:
          exception("can't open cache store %r: %s", self.cache_store_spec, e)
          raise GetoptError(
              "unusable Store specification: %s"
              % (self.cache_store_spec,))
        else:
          S = ProxyStore(
              "%s:%s" % (cacheS.name, S.name),
              read=(cacheS,),
              read2=(S,),
              copy2=(cacheS,),
              save=(cacheS, S)
          )
          S.config = self.config
      ##X("MAIN CMD_OP S:")
      ##dump_Store(S)
      defaults.push_Ss(S)
      # start the status ticker
      if False and sys.stdout.isatty():
        X("wrap in a ProgressStore")
        run_ticker = True
        S = ProgressStore("ProgressStore(%s)" % (S,), S)
        def ticker():
          old_text = ''
          while run_ticker:
            text = S.status_text()
            if text != old_text:
              statusline(text)
              old_text = text
            sleep(0.25)
        T = Thread(name='%s-status-line' % (S,), target=ticker)
        T.daemon = True
        T.start()
      else:
        run_ticker = False
      with S:
        xit = op_func(args)
      if cacheS:
        cacheS.backend = None
      if run_ticker:
        run_ticker = False
      return xit

  def cmd_profile(self, *a, **kw):
    ''' Wrapper to profile other operations and report.
    '''
    try:
      import cProfile as profile
    except ImportError:
      import profile
    P = profile.Profile()
    P.enable()
    try:
      xit = self.cmd_op(*a, **kw)
    except Exception:
      P.disable()
      raise
    P.disable()
    P.create_stats()
    P.print_stats(sort='cumulative')
    return xit

  def cmd_cat(self, args):
    ''' Concatentate the contents of the supplied filerefs to stdout.
    '''
    if not args:
      raise GetoptError("missing filerefs")
    for path in args:
      cat(path)
    return 0

  def cmd_dump(self, args):
    ''' Dump various file types.
    '''
    if not args:
      raise GetoptError("missing filerefs")
    hashclass = DEFAULT_HASHCLASS
    one_line = True
    _, columns = ttysize(1)
    if columns is None:
      columns = 80
    max_width = columns - 1
    for path in args:
      if path.endswith('.vtd'):
        print(path)
        DF = DataFileReader(path)
        with DF:
          try:
            for DR in DF.scanfrom(0):
              data = DR.data
              hashcode = hashclass(data)
              leadin = '%9d %16.16s' % (offset, hashcode)
              dump_chunk(data, leadin, max_width, one_line)
          except EOFError:
            pass
      elif path.endswith('.lmdb'):
        print(path)
        lmdb = LMDBIndex(path[:-5], hashclass, decode=DataDirIndexEntry.from_bytes)
        with lmdb:
          for hashcode, entry in lmdb.items():
            print(hashcode, entry)
      else:
        warning("unsupported file type: %r", path)
    return 0

  def cmd_fsck(self, args):
    ''' Data structure inspection/repair.
    '''
    if not args:
      raise GetoptError("missing fsck type")
    fsck_type = args.pop(0)
    with Pfx(fsck_type):
      try:
        fsck_op = {
            "block": self.cmd_fsck_block,
            "dir": self.cmd_fsck_dir,
        }[fsck_type]
      except KeyError:
        raise GetoptError("unsupported fsck type")
      return fsck_op()

  def cmd_fsck_block(self, args):
    ''' Inspect a single Block.
        TODO: fromtext -> transcribe.
    '''
    xit = 0
    if not args:
      raise GetoptError("missing blockrefs")
    for blockref in args:
      with Pfx(blockref):
        blockref_bs = fromtext(blockref)
        B, offset = BlockRecord.value_from_bytes(blockref_bs)
        if offset < len(blockref_bs):
          raise ValueError("invalid blockref, extra bytes: %r" % (blockref[offset:],))
        if not fsck_Block(B):
          error("fsck failed")
          xit = 1
    return xit

  def cmd_fsck_dir(self, args):
    ''' Inspect a Dir.
    '''
    xit = 0
    if not args:
      raise GetoptError("missing dirents")
    for dirent_txt in args:
      with Pfx(dirent_txt):
        D = decode_Dirent_text(dirent_txt)
        if not fsck_dir(D):
          error("fsck failed")
          xit = 1
    return xit

  def cmd_import(self, args):
    ''' Import paths into the Store, print top Dirent for each.
    '''
    xit = 0
    delete = False
    overlay = False
    whole_read = False
    opts, args = getopt(args, 'oW')
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
    if not args:
      raise GetoptError("missing path")
    srcpath = args.pop(0)
    if not args:
      raise GetoptError("missing archive.vt")
    special = args.pop(0)
    if special == '-':
      special = None
    if args:
      raise GetoptError("extra arguments: %s" % (' '.join(args),))
    if special is None:
      D = None
    else:
      with Pfx(repr(special)):
        try:
          with open(special, 'a'):
            pass
        except OSError as e:
          error("cannot open archive for append: %s", e)
          return 1
        _, D = Archive(special).last
    if D is None:
      D = Dir('import')
    srcbase = basename(srcpath.rstrip(os.sep))
    E = D.get(srcbase)
    with Pfx(srcpath):
      if isdirpath(srcpath):
        if E is None:
          E = D.mkdir(srcbase)
        elif not overlay:
          error("name %r already imported", srcbase)
          return 1
        elif not E.isdir:
          error("name %r is not a directory", srcbase)
        E, errors = import_dir(
            srcpath, E,
            delete=delete, overlay=overlay, whole_read=whole_read)
        if errors:
          warning("directory not fully imported")
          for err in errors:
            warning("  %s", err)
          xit = 1
      elif isfilepath(srcpath):
        if E is not None:
          error("name %r already imported", srcbase)
          return 1
        E = D[srcbase] = import_file(srcpath)
      else:
        error("not a file or directory")
        xit = 1
        return 1
    if xit != 0:
      fp = sys.stderr
      print("updated dirent after import:", file=fp)
    elif special is None:
      Archive.write(sys.stdout, D)
    else:
      Archive(special).update(D)
    return xit

  def cmd_ls(self, args):
    ''' Do a directory listing of the specified I<dirrefs>.
    '''
    recurse = False
    if args and args[0] == "-R":
      recurse = True
      args.pop(0)
    if not args:
      raise GetoptError("missing dirrefs")
    first = True
    for path in args:
      if first:
        first = False
      else:
        print()
      D = dirent_dir(path)
      ls(path, D, recurse, sys.stdout)
    return 0

  def cmd_mount(self, args):
    ''' Mount the specified special on the specified mountpoint directory.
        Requires FUSE support.
    '''
    badopts = False
    all_dates = False
    append_only = False
    readonly = None
    opts, args = getopt(args, 'ao:r')
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
    specialD = None     # becomes not None for a D{dir}
    mount_store = defaults.S
    # the special may derive directly from a config Store clause
    special_store = None
    special_basename = None
    archive = None
    try:
      special = args.pop(0)
    except IndexError:
      special = None
      error("missing special")
      badopts = True
    else:
      with Pfx("special %r", special):
        fsname = special
        if special.startswith('D{') and special.endswith('}'):
          # D{dir}
          try:
            D, offset = parse(special)
          except ValueError as e:
            error("does not seem to be a Dir transcription: %s", e)
          else:
            if offset != len(special):
              error("unparsed text: %r", special[offset:])
            elif not isinstance(D, Dir):
              error("does not seem to be a Dir transcription, looks like a %s", type(D))
            else:
              specialD = D
          if specialD is None:
            badopts = True
          else:
            special_basename = D.name
            if not readonly:
              warning("setting readonly")
              readonly = True
        elif special.startswith('[') and special.endswith(']'):
          matched, type_, params, offset = get_store_spec(special)
          if 'clause_name' not in params:
            error("no clause name")
            badopts = True
          else:
            fsname = str(self.config) + special
            special_basename = special[1:-1].strip()
            special_store = self.config.Store_from_spec(special)
            X("special_store=%s", special_store)
            if special_store is not mount_store:
              warning(
                  "mounting using Store from special %r instead of default: %s",
                  special, mount_store)
              mount_store = special_store
            archive = self.config.archive(special_basename)
        else:
          # pathname to archive file
          archive = special
          if not isfilepath(archive):
            error("not a file: %r", archive)
            badopts = True
          else:
            spfx, sext = splitext(basename(special))
            if spfx and sext == '.vt':
              special_basename = spfx
            else:
              special_basename = special
    if special_basename is not None:
      # Make the name for an explicit mount safer:
      # no path components, no dots (thus no leading dots).
      special_basename = special_basename.replace(os.sep, '_').replace('.', '_')
    if args:
      mountpoint = args.pop(0)
    else:
      if special_basename is None:
        error('missing mountpoint, and cannot infer mountpoint from special: %r', special)
        badopts = True
      else:
        mountpoint = special_basename
    if args:
      subpath = args.pop(0)
    else:
      subpath = None
    if args:
      error("extra arguments: %s", ' '.join(args))
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
        if isinstance(archive, str):
          archive = Archive(archive)
        if all_dates:
          E = Dir(mount_base)
          for when, subD in archive:
            E[datetime.fromtimestamp(when).isoformat()] = subD
        else:
          try:
            when, E = archive.last
          except OSError as e:
            error("can't access special: %s", e)
            return 1
          except ValueError as e:
            error("invalid contents: %s", e)
            return 1
          # no "last entry" (==> first use) - make an empty directory
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
      # import vtfuse before doing anything with side effects
      try:
        from cs.vtfuse import mount, umount
      except ImportError as e:
        error("required module cs.vtfuse not available: %s", e)
        return 1
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
              error("mountpoint is not a directory", mountpoint)
              return 1
            raise
        T = None
        try:
          T = mount(
              mountpoint, E,
              S=mount_store, archive=archive, subpath=subpath,
              readonly=readonly, append_only=append_only,
              fsname=fsname)
          cs.x.X_via_tty = True
        except KeyboardInterrupt:
          error("keyboard interrupt, unmounting %r", mountpoint)
          xit = umount(mountpoint)
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

  def cmd_pack(self, args):
    ''' Replace the I<path> with an archive file I<path>B<.vt> referring
        to the stored content of I<path>.
    '''
    if not args:
      raise GetoptError("missing path")
    ospath = args.pop(0)
    if args:
      raise GetoptError("extra arguments after path: %r" % (args,))
    modes = CopyModes(trust_size_mtime=True)
    with Pfx(ospath):
      if not existspath(ospath):
        error("missing")
        return 1
      arpath = ospath + '.vt'
      A = Archive(arpath, missing_ok=True)
      when, target = A.last
      if target is None:
        target = Dir(basename(ospath))
      if isdirpath(ospath):
        source = OSDir(ospath)
      else:
        source = OSFile(ospath)
      X("target = %s, source= %s", type(target), type(source))
      if not merge(target, source):
        error("merge into %r fails", arpath)
        return 1
      A.update(target)
      info("remove %r", ospath)
      if isdirpath(ospath):
        shutil.rmtree(ospath)
      else:
        os.remove(ospath)
    return 0

  def cmd_pullfrom(self, args):
    ''' Pull missing content from other Stores.

        Usage: pullfrom other_store objects...
    '''
    if not args:
      raise GetoptError("missing other_store")
    S1spec = args.pop(0)
    if not args:
      raise GetoptError("missing objects")
    with Pfx("other_store %r", S1spec):
      S1 = Store(S1spec, self.config)
    S2 = defaults.S
    with Pfx("%s => %s", S1.name, S2.name):
      with S1:
        for obj_spec in args:
          with Pfx(obj_spec):
            try:
              obj = parse(obj_spec)
            except ValueError as e:
              raise GetoptError("unparsed: %s" % (e,)) from e
            try:
              pushto = obj.pushto
            except AttributeError:
              raise GetoptError("no pushto facility for %s objects" % (type(obj_spec),))
            pushto(S2, runstate=defaults.runstate)
    return 0

  def cmd_pushto(self, args):
    ''' Push something to a secondary Store,
        such that the secondary store has all the required Blocks.

        Usage: pushto other_store objects...
    '''
    if not args:
      raise GetoptError("missing other_store")
    S2spec = args.pop(0)
    if not args:
      raise GetoptError("missing objects")
    with Pfx("other_store %r", S2spec):
      S2 = Store(S2spec, self.config)
    S1 = defaults.S
    with Pfx("%s => %s", S1.name, S2spec):
      for obj_spec in args:
        with Pfx(obj_spec):
          try:
            obj = parse(obj_spec)
          except ValueError as e:
            raise GetoptError("unparsed: %s" % (e,)) from e
          try:
            pushto = obj.pushto
          except AttributeError:
            raise GetoptError("no pushto facility for %s objects" % (type(obj_spec),))
          pushto(S2, runstate=defaults.runstate)
    return 0

  def cmd_serve(self, args):
    ''' Start a service daemon listening on a TCP port
        or on a UNIX domain socket or on stdin/stdout.

        Usage: serve [{DEFAULT|-|/path/to/socket|[host]:port} [name:storespec]...]

        With no `name:storespec` arguments the default Store is served,
        otherwise the named Stores are exported with the first being
        served initially.
    '''
    if args:
      address = args.pop(0)
    else:
      address = 'DEFAULT'
    if address == 'DEFAULT':
      # obtain the address from the [server] config clause
      try:
        clause = self.config.get_clause('server')
      except KeyError:
        raise GetoptError(
            "no [server] clause to implement address %r"
            % (address,))
      try:
        address = clause['address']
      except KeyError:
        raise GetoptError("[server] clause: no address field")
    if not args:
      exports = {'': defaults.S}
    else:
      exports = {}
      for named_store_spec in args:
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
              parsed, type_, params, offset = get_store_spec(named_store_spec, offset)
            except ValueError as e:
              raise GetoptError(
                  "invalid Store specification after \"name:\": %s"
                  % (e,)) from e
            if offset < len(named_store_spec):
              raise GetoptError(
                  "extra text after storespec: %r"
                  % (named_store_spec[offset:],))
            namedS = self.config.new_Store(parsed, type_, params)
            exports[name] = namedS
            if '' not in exports:
              exports[''] = namedS
    if address == '-':
      from .stream import StreamStore
      remoteS = StreamStore(
          "serve -", sys.stdin, sys.stdout,
          exports=exports)
      remoteS.join()
    elif '/' in address:
      # path/to/socket
      socket_path = expand_path(address)
      X("serve via UNIX socket at %r", address)
      with defaults.S:
        srv = serve_socket(
            socket_path=socket_path,
            exports=exports,
            runstate=self.runstate)
      srv.join()
    else:
      # [host]:port
      cpos = address.rfind(':')
      if cpos >= 0:
        host = address[:cpos]
        port = address[cpos+1:]
        if not host:
          host = '127.0.0.1'
        port = int(port)
        with defaults.S:
          srv = serve_tcp(bind_addr=(host, port), exports=exports, runstate=self.runstate)
        srv.join()
      else:
        raise GetoptError(
            "invalid serve argument,"
            " I expect \"-\" or \"/path/to/socket\" or \"[host]:port\", got: %r"
            % (address,))
    return 0

  def cmd_test(self, args):
    ''' Test various facilites.
    '''
    if not args:
      raise GetoptError("missing test subcommand")
    subcmd = args.pop(0)
    with Pfx(subcmd):
      if subcmd == 'blockify':
        if not args:
          raise GetoptError("missing filename")
        filename = args.pop(0)
        with Pfx(filename):
          if args:
            raise GetoptError("extra arguments after filename: %r" % (args,))
          scanner = scanner_from_filename(filename)
          size_counts = defaultdict(int)
          with open(filename, 'rb') as fp:
            for chunk in blocked_chunks_of(file_data(fp, None), scanner):
              print(len(chunk), str(chunk[:16]))
              size_counts[len(chunk)] += 1
          for size, count in sorted(size_counts.items()):
            print(size, count)
        return 0
      raise GetoptError("unrecognised subcommand")

  def cmd_unpack(self, args):
    ''' Unpack the archive file _archive_`.vt` as _archive_.
    '''
    if len(args) < 1:
      raise GetoptError("missing archive name")
    arpath = args.pop(0)
    arbase, arext = splitext(arpath)
    if arext != '.vt':
      raise GetoptError("archive name does not end in .vt: %r" % (arpath,))
    if args:
      raise GetoptError("extra arguments after archive name %r" % (arpath,))
    if existspath(arbase):
      error("archive base already exists: %r", arbase)
      return 1
    with Pfx(arpath):
      when, source = Archive(arpath).last
      if source is None:
        error("no entries in archive")
        return 1
      if source.isdir:
        target = OSDir(arbase)
      else:
        target = OSFile(arbase)
    with Pfx(arbase):
      if not merge(target, source):
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
      "%c %-41s %s %6d %s\n"
      % (('d' if E.isdir else 'f'), detail, t, st_size, name))

def ls(path, D, recurse, fp=None):
  ''' Do an ls style directory listing with optional recursion.
  '''
  if fp is None:
    fp = sys.stdout
  fp.write('\n')
  fp.write(path)
  fp.write(":\n")
  if not recurse:
    debug("ls(): getting dirs and files...")
    names = D.dirs()+D.files()
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
    F = dirent_file(path)
    block = F.block
    for B in block.leaves:
      fp.write(B.data)

def dump(path, fp=None):
  ''' Dump the Block contents of `path`.
  '''
  if fp is None:
    fp = sys.stdout
  E, subname, unresolved = dirent_resolve(path)
  if unresolved:
    warning("%r: unresolved components: %r", path, unresolved)
  if subname:
    E = E[subname]
  dump_Block(E.block, fp)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
