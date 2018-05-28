#!/usr/bin/env python3
#
# Command script for venti-ish implementation.
#       - Cameron Simpson <cs@cskk.id.au> 01may2007
#

from __future__ import with_statement
from collections import defaultdict
from datetime import datetime
import errno
from getopt import getopt, GetoptError
import logging
import os
from os.path import basename, splitext, expanduser, \
    exists as existspath, join as joinpath, \
    isabs as isabspath, isdir as isdirpath, isfile as isfilepath
import shutil
from signal import signal, SIGINT, SIGHUP, SIGQUIT
import sys
from threading import Thread
from time import sleep
from cs.debug import ifdebug, dump_debug_threads, thread_dump
from cs.fileutils import file_data
from cs.lex import hexify
import cs.logutils
from cs.logutils import exception, error, warning, info, debug, \
                        setup_logging, loginfo, logTo
from cs.pfx import Pfx
from cs.resources import RunState
from cs.tty import statusline, ttysize
import cs.x
from cs.x import X
from . import fromtext, defaults
from .archive import Archive, ArchiveFTP, CopyModes, copy_out_dir, copy_out_file
from .block import Block, IndirectBlock, decodeBlock
from .blockify import blocked_chunks_of
from .cache import FileCacheStore
from .config import Config, Store
from .datadir import DataDir, DataDir_from_spec, DataDirIndexEntry
from .datafile import DataFile, F_COMPRESSED, decompress
from .debug import dump_chunk, dump_Block
from .dir import Dir, DirFTP
from .fsck import fsck_Block, fsck_dir
from .hash import DEFAULT_HASHCLASS
from .index import LMDBIndex
from .parsers import scanner_from_filename
from .paths import decode_Dirent_text, dirent_dir, dirent_file, dirent_resolve
from .pushpull import pull_hashcodes, missing_hashcodes_by_checksum
from .smuggling import import_dir, import_file
from .store import ProgressStore, DataDirStore
from .transcribe import parse

def main(argv):
  return VTCmd().main(argv)

class VTCmd:

  USAGE = '''Usage: %s [options...] [profile] operation [args...]
  Options:
    -C        Do not put a cache in front of the store.
              Default: use the filecache specified by the "[cache]"
              configuration clause.
    -S store  Specify the store to use:
                [clause]        Specification from .vtrc.
                /path/to/dir    GDBMStore
                tcp:[host]:port TCPStore
                |sh-command     StreamStore via sh-command
              Default from $VT_STORE, or "[default]".
    -f        Config file. Default from $VT_CONFIG, otherwise ~/.vtrc
    -q        Quiet; not verbose. Default if stderr is not a tty.
    -v        Verbose; not quiet. Default if stderr is a tty.
  Operations:
    cat filerefs...
    catblock [-i] hashcodes...
    datadir [indextype:[hashname:]]/dirpath index
    datadir [indextype:[hashname:]]/dirpath pull other-datadirs...
    datadir [indextype:[hashname:]]/dirpath push other-datadir
    dump {datafile.vtd|index.gdbm|index.lmdb}
    fsck block blockref...
    ftp archive.vt
    import [-oW] path {-|archive.vt}
    ls [-R] dirrefs...
    mount [-a] [-o {append_only,readonly}] [-r] {Dir|config-clause|archive.vt} [mountpoint [subpath]]
      -a  All dates. Implies readonly.
      -o options
          Mount options:
            append_only Files may not be truncated or overwritten.
            readonly    Read only; data may not be modified.
      -r  Readonly, the same as "-o readonly".
    pack paths...
    pull other-store objects...
    report
    scan datafile
    serve {-|host:port}
    test blockify file
    unpack dirrefs...
'''

  def main(self, argv=None, environ=None, verbose=None):
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

    store_spec = os.environ.get('VT_STORE', '[default]')
    dflt_log = os.environ.get('VT_LOGFILE')
    no_cache = False

    try:
      opts, args = getopt(args, 'CS:qv')
    except GetoptError as e:
      error("unrecognised option: %s: %s"% (e.opt, e.msg))
      badopts = True
      opts, args = [], []

    for opt, val in opts:
      if opt == '-C':
        no_cache = True
      elif opt == '-S':
        # specify Store
        store_spec = val
      elif opt == '-q':
        # quiet: not verbose
        self.verbose = False
      elif opt == '-v':
        # verbose: not quiet
        self.verbose = True
      else:
        raise RuntimeError("unhandled option: %s" % (opt,))

    self.store_spec = store_spec
    self.no_cache = no_cache

    if self.verbose:
      loginfo.level = logging.INFO
      upd = loginfo.upd
      if upd is not None:
        upd.nl_level = logging.INFO

    self.config = Config()

    if dflt_log is not None:
      logTo(dflt_log, delay=True)

    xit = None
    self.runstate = RunState()

    # catch signals, flag termination
    def sig_handler(sig, frame):
      ''' Signal handler
      '''
      warning("received signal %s from %s", sig, frame)
      if sig == SIGQUIT:
        thread_dump()
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
      if op in ("datadir", "init", "dump", "scan", "test"):
        return op_func(args)
      # open the default Store
      if self.store_spec is None:
        raise GetoptError("no $VT_STORE and no -S option")
      try:
        S = Store(self.store_spec, self.config)
      except Exception as e:
        exception("can't open store %r: %s", self.store_spec, e)
        raise GetoptError("unusable Store specification: %s" % (self.store_spec,))
      defaults.push_Ss(S)
      if self.no_cache:
        cacheS = None
      else:
        cacheS = self.config['cache']
        cacheS.backend = S
        S = cacheS
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
    try:
      import cProfile as profile
    except ImportError:
      import profile
    P = profile.Profile()
    P.enable()
    try:
      xit = self.cmd_op(*a, **kw)
    except Exception as e:
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

  def cmd_catblock(self, args):
    '''  Emit the content of the blocks specified by the supplied hashcodes.
    '''
    indirect = False
    if len(args) > 0 and args[0] == "-i":
      indirect = True
      args.pop(0)
    if not args:
      raise GetoptError("missing hashcodes")
    for hctext in args:
      h = defaults.S.hashclass(fromtext(hctext))
      if indirect:
        B = IndirectBlock(h)
      else:
        B = Block(h)
      for subB in B.leaves:
        sys.stdout.write(subB.data)
    return 0

  def cmd_report(self, args):
    ''' Report stuff after store setup.
    '''
    print("S =", defaults.S)
    return 0

  def cmd_datadir(self, args):
    ''' Perform various operations on DataDirs.
    '''
    xit = 1
    if not args:
      raise GetoptError("missing datadir spec")
    datadir_spec = args.pop(0)
    with Pfx(datadir_spec):
      D = DataDir_from_spec(datadir_spec)
      if not args:
        raise GetoptError("missing subop")
      subop = args.pop(0)
      with Pfx(subop):
        if subop == 'index':
          if args:
            raise GetoptError("extra arguments: %s" % (' '.join(args),))
          D.reindex()
        elif subop == 'pull':
          if not args:
            raise GetoptError("missing other-datadirs")
          else:
            for other_spec in args:
              with Pfx(other_spec):
                Dother = DataDir_from_spec(other_spec)
                pull_hashcodes(D, Dother, missing_hashcodes_by_checksum(D, Dother))
        elif subop == 'push':
          if not args:
            raise GetoptError("missing other-datadir")
          else:
            other_spec = args.pop(0)
            if args:
              raise GetoptError("extra arguments after other_spec: %s" % (' '.join(args),))
            with Pfx(other_spec):
              Dother = DataDir_from_spec(other_spec)
              pull_hashcodes(Dother, D, missing_hashcodes_by_checksum(Dother, D))
        else:
          raise GetoptError('unrecognised subop')
    return xit

  def cmd_dump(self, args):
    ''' Dump various file types.
    '''
    if not args:
      raise GetoptError("missing filerefs")
    hashclass = DEFAULT_HASHCLASS
    long_format = True
    one_line = True
    rows, columns = ttysize(1)
    if columns is None:
      columns = 80
    max_width = columns - 1
    for path in args:
      if path.endswith('.vtd'):
        print(path)
        with open(path, 'rb') as fp:
          try:
            for offset, flags, data, offset2 in DataFile.scan_records(fp, do_decompress=True):
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
    import cs.logutils
    cs.logutils.X_via_log = True
    if not args:
      raise GetoptError("missing fsck type")
    fsck_type = args.pop(0)
    with Pfx(fsck_type):
      try:
        fsck_op = {
          "block":    self.cmd_fsck_block,
          "dir":      self.cmd_fsck_dir,
        }[fsck_type]
      except KeyError:
        raise GetoptError("unsupported fsck type")
      return fsck_op()

  def cmd_fsck_block(self, args):
    xit = 0
    if not args:
      raise GetoptError("missing blockrefs")
    for blockref in args:
      with Pfx(blockref):
        blockref_bs = fromtext(blockref)
        B, offset = decodeBlock(blockref_bs)
        if offset < len(blockref_bs):
          raise ValueError("invalid blockref, extra bytes: %r" % (blockref[offset:],))
        if not fsck_Block(B):
          error("fsck failed")
          xit = 1
    return xit

  def cmd_fsck_dir(self, args):
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

  def cmd_ftp(self, args):
    if not args:
      raise GetoptError("missing dirent or archive")
    target = args.pop(0)
    if args:
      raise GetoptError("extra arguments: " + ' '.join(args))
    with Pfx(target):
      if isabspath(target):
        archive = target
        ArchiveFTP(archive).cmdloop()
      else:
        D = decode_Dirent_text(target)
        DirFTP(D).cmdloop()
      return 0

  def cmd_import(self, args):
    ''' Import paths into the Store, print top Dirent for each.
    '''
    xit = 0
    delete = False
    overlay = False
    whole_read = False
    opts, args = getopt(args, 'oW')
    for opt, val in opts:
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
        when, D = Archive(special).last
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
        E, errors = import_dir(srcpath, E,
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
      Archive(special).save(D)
    return xit

  # TODO: create dir, dir/data
  def cmd_init(self, args):
    ''' Initialise a directory for use as a store.
        Usage: init dirpath [datadir]
    '''
    if not args:
      raise GetoptError("missing dirpath")
    statedirpath = args.pop(0)
    if args:
      datadirpath = args.pop(0)
    else:
      datadirpath = statedirpath
    if args:
      raise GetoptError("extra arguments after datadir: %s" % (' '.join(args),))
    for dirpath in statedirpath, datadirpath:
      with Pfx(dirpath):
        if not isdirpath(dirpath):
          raise GetoptError("not a directory")
      with DataDirStore(statedirpath, statedirpath, datadirpath, DEFAULT_HASHCLASS):
        os.system("ls -la %s" % (statedirpath,))
    return 0

  def cmd_serve(self, args):
    ''' Start a service daemon listening on a TCP port or on stdin/stdout.
    '''
    if len(args) != 1:
      raise GetoptError("expected a port")
    arg = args[0]
    if arg == '-':
      from .stream import StreamStore
      RS = StreamStore("serve -", sys.stdin, sys.stdout,
                       local_store=defaults.S)
      RS.join()
    else:
      cpos = arg.rfind(':')
      if cpos >= 0:
        host = arg[:cpos]
        port = arg[cpos+1:]
        if len(host) == 0:
          host = '127.0.0.1'
        port = int(port)
        from .tcp import TCPStoreServer
        with TCPStoreServer((host, port), defaults.S) as srv:
          self.runstate.notify_cancel.add(lambda rs: srv.cancel())
          with self.runstate:
            srv.join()
      else:
        raise GetoptError("invalid serve argument, I expect \"-\" or \"[host]:port\", got \"%s\"" % (arg,))
    return 0

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
        print
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
    A = None            # becomes not None for a pathname
    specialD = None     # becomes not None for a D{dir}
    mount_store = defaults.S
    special_store = None # the special may derive directly from a config Store clause
    special_basename = None
    archive = None
    try:
      special = args.pop(0)
    except IndexError:
      error("missing special")
      badopts = True
    else:
      with Pfx("special %r", special):
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
        elif special.startswith('[') and special.endswith(']'):
          special_basename = special[1:-1].strip()
          special_store = self.config.Store_from_spec(special)
          X("special_store=%s", special_store)
          if special_store is not mount_store:
            warning(
                "mounting using Store from special %r instead of default: %s",
                special, mount_store)
            mount_store = special_store
          try:
            get_Archive = special_store.get_Archive
          except AttributeError:
            error("%s: no get_Archive method", special_store)
            badopts = True
          else:
            X("MAIN: get_Archive=%s", get_Archive)
            archive = get_Archive()
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
        mountdir = mount_store.mountdir
        if mountdir is None:
          error('missing mountpoint, no Sotre.mountdir, cannot infer mountpoint: store=%s', mount_store)
          badopts = True
        else:
          mountdir = expanduser(mountdir)
          mountpoint = joinpath(mountdir, special_basename)
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
            X("cmd_mount: new E=%s", E)
          else:
            ##dump_Dirent(E, recurse=True)
            if not E.isdir:
              error("expected directory, not file: %s", E)
              return 1
      if E.name== '.':
        info("rename %s from %r to %r", E, E.name, mount_base)
        E.name = mount_base
      # import vtfuse before doing anything with side effects
      from .vtfuse import mount, umount
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
            else:
              raise
        try:
          T = mount(mountpoint, E, mount_store, archive=archive, subpath=subpath, readonly=readonly, append_only=append_only, fsname=special)
          cs.x.X_via_tty = True
          T.join()
        except KeyboardInterrupt as e:
          error("keyboard interrupt, unmounting %r", mountpoint)
          xit = umount(mountpoint)
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
    ''' Replace each I<path> with an archive file I<path>B<.vt> referring
        to the stored content of I<path>.
    '''
    if not args:
      raise GetoptError("missing paths")
    xit = 0
    modes = CopyModes(trust_size_mtime=True)
    for ospath in args:
      with Pfx(ospath):
        if not existspath(ospath):
          error("missing")
          xit = 1
          continue
        arpath = ospath + '.vt'
        try:
          update_archive(arpath, ospath, modes, create_archive=True)
        except IOError as e:
          error("%s" % (e,))
          xit = 1
          continue
        info("remove %r", ospath)
        if isdirpath(ospath):
          shutil.rmtree(ospath)
        else:
          os.remove(ospath)
    return xit

  def cmd_pull(self, args):
    ''' Pull missing content from other Stores.
    '''
    if not args:
      raise GetoptError("missing stores")
    raise NotImplementedError

  def cmd_scan(self, args):
    ''' Read a datafile and report.
    '''
    if len(args) < 1:
      raise GetoptError("missing datafile/datadir")
    hashclass = DEFAULT_HASHCLASS
    for arg in args:
      if isdirpath(arg):
        dirpath = arg
        D = DataDir(dirpath)
        with D:
          for n, offset, data in D.scan():
            print(dirpath, n, offset, "%d:%s" % (len(data), hashclass.from_chunk(data)))
      else:
        filepath = arg
        DF = DataFile(filepath)
        with DF:
          for offset, flags, data in DF.scan():
            if flags & F_COMPRESSED:
              data = decompress(data)
            print(filepath, offset, "%d:%s" % (len(data), hashclass.from_chunk(data)))
    return 0

  def cmd_test(self, args):
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
      else:
        raise GetoptError("unrecognised subcommand")

  def cmd_unpack(self, args):
    ''' Unpack the archive file I<archive>B<.vt> as I<archive>.
    '''
    if len(args) < 1:
      raise GetoptError("missing archive name")
    arpath = args.pop(0)
    arbase, arext = splitext(arpath)
    X("arbase=%r, arext=%r", arbase, arext)
    if arext != '.vt':
      raise GetoptError("archive name does not end in .vt: %r" % (arpath,))
    if len(args) > 0:
      raise GetoptError("extra arguments after archive name %r" % (arpath,))
    if existspath(arbase):
      error("archive base already exists: %r", arbase)
      return 1
    with Pfx(arpath):
      when, rootE = Archive(arpath).last
      if rootE is None:
        error("no entries in archive")
        return 1
    with Pfx(arbase):
      if rootE.isdir:
        os.mkdir(arbase)
        copy_out_dir(rootE, arbase, CopyModes(do_mkdir=True))
      else:
        copy_out_file(rootE, arbase)
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
  fp.write("%c %-41s %s %6d %s\n" \
           % (('d' if E.isdir else 'f'),
              detail, t, st_size, name))

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
      return cat(path, bfp)
  else:
    F = dirent_file(path)
    block = F.block
    for B in block.leaves:
      fp.write(B.data)

def dump(path, fp=None):
  if fp is None:
    fp = sys.stdout
  E, subname = dirent_resolve(path)
  if subname:
    E = E[subname]
  dump_Block(E.block, fp)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
