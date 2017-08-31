#!/usr/bin/env python3
#
# Command script for venti-ish implementation.
#       - Cameron Simpson <cs@cskk.id.au> 01may2007
#

from __future__ import with_statement
import sys
import os
from os.path import basename, dirname, splitext, \
    exists as existspath, join as joinpath, \
    isabs as isabspath, isdir as isdirpath, isfile as isfilepath
import errno
from getopt import getopt, GetoptError
import datetime
import logging
import shutil
from signal import signal, SIGINT, SIGHUP
from threading import Thread
from time import sleep
from cs.debug import ifdebug, dump_debug_threads, thread_dump
from cs.env import envsub
from cs.lex import hexify
import cs.logutils
from cs.logutils import exception, error, warning, info, debug, \
                        setup_logging, loginfo, logTo
from cs.pfx import Pfx
from cs.tty import statusline
import cs.x
from cs.x import X
from . import fromtext, defaults
from .archive import ArchiveFTP, CopyModes, \
                        last_Dirent, save_Dirent, copy_out_dir
from .block import Block, IndirectBlock, dump_block, decodeBlock
from .cache import FileCacheStore
from .compose import Store, ConfigFile
from .debug import dump_Dirent
from .datadir import DataDir, DataDir_from_spec
from .datafile import DataFile, F_COMPRESSED, decompress
from .dir import Dir, DirFTP
from .hash import DEFAULT_HASHCLASS
from .fsck import fsck_Block, fsck_dir
from .paths import decode_Dirent_text, dirent_dir, dirent_file, dirent_resolve, resolve
from .pushpull import pull_hashcodes, missing_hashcodes_by_checksum
from .smuggling import import_dir, import_file
from .store import ProgressStore, DataDirStore

def main(argv):
  global loginfo
  cmd = basename(argv[0])
  if cmd.endswith('.py'):
    cmd = 'vt'
  usage = '''Usage: %s [options...] [profile] operation [args...]
    Options:
      -C        Do not put a cache in front of the store.
      -S store  Specify the store to use:
                  [clause]        Specification from .vtrc.
                  /path/to/dir    GDBMStore
                  tcp:[host]:port TCPStore
                  |sh-command     StreamStore via sh-command
      -q        Quiet; not verbose. Default if stderr is not a tty.
      -v        Verbose; not quiet. Default if stderr is a tty.
    Operations:
      cat filerefs...
      catblock [-i] hashcodes...
      datadir [indextype:[hashname:]]/dirpath index
      datadir [indextype:[hashname:]]/dirpath pull other-datadirs...
      datadir [indextype:[hashname:]]/dirpath push other-datadir
      dump filerefs
      fsck block blockref...
      ftp archive.vt
      import [-oW] path {-|archive.vt}
      listen {-|host:port}
      ls [-R] dirrefs...
      mount [-r] [-o {append_only,readonly}] archive.vt [mountpoint [subpath]]
      pack paths...
      scan datafile
      pull other-store objects...
      report
      unpack dirrefs...
''' % (cmd,)

  badopts = False

  # verbose if stderr is a tty
  try:
    verbose = sys.stderr.isatty()
  except AttributeError:
    verbose = False

  setup_logging(cmd_name=cmd, upd_mode=sys.stderr.isatty(), verbose=verbose)
  cs.x.X_logger = logging.getLogger()

  dflt_configpath = os.environ.get('VT_CONFIG', envsub('$HOME/.vtrc'))
  dflt_vt_store = os.environ.get('VT_STORE')
  dflt_log = os.environ.get('VT_LOGFILE')
  no_cache = False

  args = argv[1:]
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
      dflt_vt_store = val
    elif opt == '-q':
      # quiet: not verbose
      verbose = False
    elif opt == '-v':
      # verbose: not quiet
      verbose = True
    else:
      raise RuntimeError("unhandled option: %s" % (opt,))

  if verbose:
    loginfo.level = logging.INFO
    loginfo.upd.nl_level = logging.INFO

  config = ConfigFile(dflt_configpath)

  if dflt_log is not None:
    logTo(dflt_log, delay=True)

  xit = None
  signal(SIGHUP, lambda sig, frame: thread_dump())
  signal(SIGINT, lambda sig, frame: sys.exit(thread_dump()))

  try:
    xit = cmd_op(args, verbose, config, dflt_vt_store, no_cache)
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

def cmd_op(args, verbose, config, dflt_vt_store, no_cache):
  try:
    op = args.pop(0)
  except IndexError:
    raise GetoptError("missing command")
  with Pfx(op):
    if op == "profile":
      return cmd_profile(args, verbose, config,
                         dflt_vt_store, no_cache)
    try:
      op_func = getattr(sys.modules[__name__], "cmd_" + op)
    except AttributeError:
      raise GetoptError("unknown operation \"%s\"" % (op,))
    # these commands run without a context Store
    if op in ("scan", "datadir", "init"):
      return op_func(args)
    # open the default Store
    if dflt_vt_store is None:
      raise GetoptError("no $VT_STORE and no -S option")
    try:
      S = Store(dflt_vt_store, config)
    except Exception as e:
      exception("can't open store \"%s\": %s", dflt_vt_store, e)
      raise GetoptError("unusable Store specification: %s" % (dflt_vt_store,))
    if not no_cache:
      S = FileCacheStore("main", S)
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
      xit = op_func(args, verbose=verbose)
    if run_ticker:
      run_ticker = False
    return xit

def cmd_profile(*a, **kw):
  try:
    import cProfile as profile
  except ImportError:
    import profile
  P = profile.Profile()
  P.enable()
  try:
    xit = cmd_op(*a, **kw)
  except Exception as e:
    P.disable()
    raise
  P.disable()
  P.create_stats()
  P.print_stats(sort='cumulative')
  return xit

def cmd_cat(args, verbose=None):
  ''' Concatentate the contents of the supplied filerefs to stdout.
  '''
  if not args:
    raise GetoptError("missing filerefs")
  for path in args:
    cat(path)
  return 0

def cmd_catblock(args, verbose=None):
  '''  Emit the content of the blocks specified by the supplied hashcodes.
  '''
  indirect = False
  if len(args) > 0 and args[0] == "-i":
    indirect = True
    args.pop(0)
  if not args:
    raise GetoptError("missing hashcodes")
  for hctext in args:
    h = S.hashclass(fromtext(hctext))
    if indirect:
      B = IndirectBlock(h)
    else:
      B = Block(h)
    for subB in B.leaves:
      sys.stdout.write(subB.data)
  return 0

def cmd_report(args, verbose=None):
  ''' Report stuff after store setup.
  '''
  print("S =", defaults.S)
  return 0

def cmd_datadir(args, verbose=None):
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

def cmd_dump(args, verbose=None):
  ''' Do a Block dump of the filerefs.
  '''
  if not args:
    raise GetoptError("missing filerefs")
  for path in args:
    dump(path)
  return 0

def cmd_fsck(args, verbose=None):
  import cs.logutils
  cs.logutils.X_via_log = True
  if not args:
    raise GetoptError("missing fsck type")
  fsck_type = args.pop(0)
  with Pfx(fsck_type):
    try:
      fsck_op = {
        "block":    cmd_fsck_block,
        "dir":      cmd_fsck_dir,
      }[fsck_type]
    except KeyError:
      raise GetoptError("unsupported fsck type")
    return fsck_op(args, verbose=verbose)

def cmd_fsck_block(args, verbose=None):
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

def cmd_fsck_dir(args, verbose=None):
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

def cmd_ftp(args, verbose=None):
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

def cmd_import(args, verbose=None):
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
      when, D = last_Dirent(special)
  if D is None:
    D = Dir('import')
  srcbase = basename(srcpath.rstrip(os.sep))
  E = D.get(srcbase)
  with Pfx(srcpath):
    try:
      S = os.lstat(srcpath)
    except OSError as e:
      error("%s", e)
      return 1
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
    fp = sys.stdout
  else:
    fp = special
  save_Dirent(fp, D)
  return xit

# TODO: create dir, dir/data
def cmd_init(args, verbose=None):
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
    with DataDirStore(statedirpath, statedirpath, datadirpath, DEFAULT_HASHCLASS) as S:
      os.system("ls -la %s" % (statedirpath,))
  return 0

def cmd_listen(args, verbose=None):
  ''' Start a daemon listening on a TCP port or on stdin/stdout.
  '''
  if len(args) != 1:
    raise GetoptError("expected a port")
  arg = args[0]
  if arg == '-':
    from .stream import StreamStore
    RS = StreamStore("%s listen -" % (cmd,), sys.stdin, sys.stdout,
                     local_store=S)
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
        signal(SIGHUP, lambda signum, frame: srv.cancel())
        signal(SIGINT, lambda signum, frame: srv.cancel())
        srv.join()
    else:
      raise GetoptError("invalid listen argument, I expect \"-\" or \"[host]:port\", got \"%s\"" % (arg,))
  return 0

def cmd_ls(args, verbose=None):
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

def cmd_mount(args, verbose=None):
  ''' Mount the specified special as on the specified mountpoint directory.
      Requires FUSE support.
  '''
  badopts = False
  readonly = False
  append_only = False
  opts, args = getopt(args, 'o:r')
  for opt, val in opts:
    with Pfx(opt):
      if opt == '-o':
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
  try:
    special = args.pop(0)
  except IndexError:
    error("missing special")
    badopts = True
  else:
    if not isfilepath(special):
      error("not a file: %r", special)
      badopts = True
  if args:
    mountpoint = args.pop(0)
  else:
    spfx, sext = splitext(special)
    if sext != '.vt':
      error('missing mountpoint, and cannot infer mountpoint from special (does not end in ".vt"): %r', special)
      badopts = True
    else:
      mountpoint = spfx
  if args:
    subpath = args.pop(0)
  else:
    subpath = None
  if args:
    error("extra arguments: %s", ' '.join(args))
    badopts = True
  if badopts:
    raise GetoptError("bad arguments")
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
  xit = 0
  with Pfx(special):
    try:
      when, E = last_Dirent(special, missing_ok=True)
    except OSError as e:
      error("can't access special: %s", e)
      return 1
    # no "last entry" (==> first use) - make an empty directory
    if E is None:
      E = Dir('/')
      X("cmd_mount: new E=%s", E)
    else:
      ##dump_Dirent(E, recurse=True)
      if not E.isdir:
        error("expected directory, not file: %s", E)
        return 1
    with Pfx("open('a')"):
      with open(special, 'a') as syncfp:
        try:
          T = mount(mountpoint, E, defaults.S, syncfp=syncfp, subpath=subpath, readonly=readonly, append_only=append_only)
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

def cmd_pack(args, verbose=None):
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

def cmd_pull(args, verbose=None):
  ''' Pull missing content from other Stores.
  '''
  if not args:
    raise GetoptError("missing stores")
  raise NotImplementedError

def cmd_scan(args, verbose=None):
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
      F = DataFile(filepath)
      with F:
        for offset, flags, data in F.scan():
          if flags & F_COMPRESSED:
            data = decompress(data)
          print(filepath, offset, "%d:%s" % (len(data), hashclass.from_chunk(data)))
  return 0

def cmd_unpack(args, verbose=None):
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
    last_entry = last_Dirent(arpath)
    if last_entry is None:
      error("no entries in archive")
      return 1
  when, rootE = last_entry
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
  t = datetime.datetime.fromtimestamp(int(st_mtime))
  try:
    h = B.hashcode
  except AttributeError:
    detail = repr(B)
  else:
    detail = hexify(B.hashcode)
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
  dump_block(E.block, fp)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
