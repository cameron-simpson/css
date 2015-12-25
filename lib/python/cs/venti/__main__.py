#!/usr/bin/env python3
#
# Command script for venti-ish implementation.
#       - Cameron Simpson <cs@zip.com.au> 01may2007
#

from __future__ import with_statement
import sys
import os
import os.path
from getopt import getopt, GetoptError
import datetime
import shutil
from signal import signal, SIGINT, SIGHUP
from cs.debug import ifdebug, dump_debug_threads
from cs.lex import hexify
import cs.logutils
from cs.logutils import Pfx, exception, error, warning, debug, setup_logging, logTo, X, nl
from . import totext, fromtext, defaults
from .archive import CopyModes, update_archive, toc_archive, last_Dirent, copy_out_dir
from .block import Block, IndirectBlock, dump_block
from .cache import CacheStore, MemoryCacheStore
from .debug import dump_Dirent
from .datafile import DataDirMapping_from_spec
from .dir import Dir
from .hash import DEFAULT_HASHCLASS, HASHCLASS_BY_NAME
from .paths import dirent_dir, dirent_file, dirent_resolve, resolve
from .pushpull import pull_hashcodes, missing_hashcodes_by_checksum
from .store import Store

def main(argv):
  cmd = os.path.basename(argv[0])
  if cmd.endswith('.py'):
    cmd = 'vt'
  setup_logging(cmd_name=cmd, upd_mode=False)
  usage = '''Usage:
    %s [options...] ar tar-options paths..
    %s [options...] cat filerefs...
    %s [options...] catblock [-i] hashcodes...
    %s [options...] datadir [indextype:[hashname:]]/dirpath index
    %s [options...] datadir [indextype:[hashname:]]/dirpath pull other-datadirs...
    %s [options...] datadir [indextype:[hashname:]]/dirpath push other-datadir
    %s [options...] dump filerefs
    %s [options...] listen {-|host:port}
    %s [options...] ls [-R] dirrefs...
    %s [options...] mount dirref mountpoint
    %s [options...] pack paths...
    %s [options...] scan datafile
    %s [options...] pull stores...
    %s [options...] unpack dirrefs...
    Options:
      -C store    Use this as a front end cache store.
                  "-" means no front end cache.
      -M          Don't use an additional MemoryCacheStore front end.
      -S store    Specify the store to use:
                    /path/to/dir  GDBMStore
                    tcp:[host]:port TCPStore
                    |sh-command   StreamStore via sh-command
      -q          Quiet; not verbose. Default if stdout is not a tty.
      -v          Verbose; not quiet. Default it stdout is a tty.
''' % (cmd, cmd, cmd, cmd, cmd, cmd, cmd, cmd, cmd, cmd, cmd, cmd, cmd, cmd)

  badopts = False

  # verbose if stdout is a tty
  try:
    verbose = sys.stdout.isatty()
  except:
    verbose = False

  dflt_cache = os.environ.get('VT_STORE_CACHE')
  dflt_vt_store = os.environ.get('VT_STORE')
  dflt_log = os.environ.get('VT_LOGFILE')
  useMemoryCacheStore = True

  try:
    opts, args = getopt(argv[1:], 'C:MS:qv')
  except GetoptError as e:
    error("unrecognised option: %s: %s"% (e.opt, e.msg))
    badopts = True
    opts, args = [], []

  for opt, val in opts:
    if opt == '-C':
      # specify caching Store
      if val == '-':
        dflt_cache = None
      else:
        dflt_cache = val
    elif opt == '-M':
      # do not use the in-memory caching store
      useMemoryCacheStore = False
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

  if dflt_log is not None:
    logTo(dflt_log, delay=True)

  # log message function(msg, *args)
  if verbose:
    log = nl
  else:
    log = silent

  xit = None
  S = None

  if len(args) < 1:
    error("missing command")
    badopts = True
  else:
    import signal
    from cs.debug import thread_dump
    signal.signal(signal.SIGHUP, lambda sig, frame: thread_dump())
    signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(thread_dump()))
    op = args.pop(0)
    with Pfx(op):
      try:
        op_func = getattr(sys.modules[__name__], "cmd_" + op)
      except AttributeError:
        error("unknown operation \"%s\"", op)
        badopts = True
      else:
        if op in ("scan", "datadir"):
          # run without a context store
          try:
            xit = op_func(args)
          except GetoptError as e:
            error("%s", e)
            badopts = True
        else:
          if dflt_vt_store is None:
            error("no $VT_STORE and no -S option")
            badopts = True
          else:
            try:
              S = Store(dflt_vt_store)
            except Exception as e:
              exception("can't open store \"%s\": %s", dflt_vt_store, e)
              badopts = True
            else:
              if dflt_cache is not None:
                try:
                  C = Store(dflt_cache)
                except:
                  exception("can't open cache store \"%s\"", dflt_cache)
                  badopts = True
                else:
                  S = CacheStore(S, C)
              if not badopts:
                # put an in-memory cache in front of the main cache
                if useMemoryCacheStore:
                  S = CacheStore(S, MemoryCacheStore())
                with S:
                  try:
                    xit = op_func(args, verbose=verbose, log=log)
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

def cmd_ar(args, verbose=None, log=None):
  ''' Archive or retrieve files.
      Usage: ar tar-like-options pathnames...
  '''
  if len(args) < 1:
    raise GetoptError("missing options")
  opts = args.pop(0)
  if len(opts) == 0:
    raise GetoptError("empty options")

  badopts = False
  modes = CopyModes(trust_size_mtime=True)
  modes.trust_size_mtime = True
  arpath = '-'
  mode = opts[0]
  for opt in opts[1:]:
    if opt == 'f':
      # archive filename
      arpath = args.pop(0)
    elif opt == 'q':
      # quiet: not verbose
      verbose = False
    elif opt == 'v':
      # verbose: not quiet
      verbose = True
    elif opt == 'A':
      # archive all files, not just those with differing size or mtime
      modes.trust_size_mtime = False
    else:
      error("%s: unsupported option", opt)
      badopts = True

  if (mode == 'c' or mode == 'u') and len(args) < 1:
    error("missing pathnames")
    badopts = True

  if badopts:
    raise GetoptError("bad options")

  # log message function(msg, *args)
  if verbose:
    log = nl
  else:
    log = silent

  xit = 0
  
  if mode == 't':
    if args:
      ospaths = args
    else:
      ospaths = None
    with Pfx("tf %s" % (arpath,)):
      toc_archive(arpath, ospaths)
  elif mode == 'c' or mode == 'u':
    for ospath in args:
      try:
        update_archive(arpath, ospath, modes, create_archive=True, arsubpath=ospath, log=log)
      except IOError as e:
        error("archive %s: %s" % (ospath, e))
        xit = 1
  elif mode == 'x':
    if args:
      ospaths = args
    else:
      ospaths = ('.',)
    xit = 0
    with Pfx("xf %s" % (arpath,)):
      with Pfx(arpath):
        last_entry = last_Dirent(arpath)
        if last_entry is None:
          error("no entries in archive")
          return 1
      when, rootE = last_entry
      for ospath in ospaths:
        with Pfx(ospath):
          E, Eparent, tail = resolve(rootE, ospath)
          if tail:
            error("not in archive")
            xit = 1
            continue
          log("ar x %s", ospath)
          if E.isdir:
            with Pfx("makedirs"):
              try:
                os.makedirs(ospath, exist_ok=True)
              except OSError as e:
                error("%s", e)
                xit = 1
                continue
            copy_out_dir(E, ospath, modes, log=log)
          else:
            if os.path.exists(ospath):
              error("already exists")
              xit = 1
              continue
            osparent = os.path.dirname(ospath)
            if not os.path.isdir(osparent):
              with Pfx("makedirs(%s)", osparent):
                try:
                  os.makedirs(osparent)
                except OSError as e:
                  error("%s", e)
                  xit = 1
                  continue
            copy_out_file(E, ospath, modes, log=log)
  else:
    raise GetoptError("%s: unsupported mode" % (mode,))

  return xit

def cmd_cat(args, verbose=None, log=None):
  ''' Concatentate the contents of the supplied filerefs to stdout.
  '''
  if not args:
    raise GetoptError("missing filerefs")
  for path in args:
    cat(path)
  return 0

def cmd_catblock(args, verbose=None, log=None):
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
      B = IndirectBlock(hashcode)
    else:
      B = Block(hashcode)
    for subB in B.leaves:
      sys.stdout.write(subB.data)
  return 0

def cmd_datadir(args, verbose=None, log=None):
  ''' Perform various operations on DataDirs.
  '''
  xit = 1
  if not args:
    raise GetoptError("missing datadir spec")
  datadir_spec = args.pop(0)
  with Pfx(datadir_spec):
    D = DataDirMapping_from_spec(datadir_spec)
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
              Dother = DataDirMapping_from_spec(other_spec)
              pull_hashcodes(D, Dother, missing_hashcodes_by_checksum(D, Dother))
      elif subop == 'push':
        if not args:
          raise GetoptError("missing other-datadir")
        else:
          other_spec = args.pop(0)
          if args:
            raise GetoptError("extra arguments after other_spec: %s" % (' '.join(args),))
          with Pfx(other_spec):
            Dother = DataDirMapping_from_spec(other_spec)
            pull_hashcodes(Dother, D, missing_hashcodes_by_checksum(Dother, D))
      else:
        raise GetoptError('unrecognised subop')
  return xit

def cmd_dump(args, verbose=None, log=None):
  ''' Do a Block dump of the filerefs.
  '''
  if not args:
    raise GetoptError("missing filerefs")
  for path in args:
    dump(path)
  return 0

def cmd_init(args, verbose=None, log=None):
  ''' Initialise a directory for use as a store, using the GDBM backend.
  '''
  if not args:
    raise GetoptError("missing dirpath")
  dirpath = args.pop(0)
  if args:
    raise GetoptError("extra arguments after dirpath: %s" % (' '.join(args),))
  with Pfx(dirpath):
    if not os.path.isdir(dirpath):
      raise GetoptError("not a directory")
    with Store("file:"+dirpath):
      pass
  return 0

def cmd_listen(args, verbose=None, log=None):
  ''' Start a daemon listening on a TCP port or in stdin/stdout.
  '''
  if len(args) != 1:
    raise GetoptError("expected a port")
  arg = args[0]
  if arg == '-':
    from cs.venti.stream import StreamDaemon
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
      import cs.venti.tcp
      with cs.venti.tcp.TCPStoreServer((host, port), defaults.S) as srv:
        signal(SIGHUP, lambda signum, frame: srv.cancel())
        signal(SIGINT, lambda signum, frame: srv.cancel())
        srv.join()
    else:
      raise GetoptError("invalid listen argument, I expect \"-\" or \"[host]:port\", got \"%s\"" % (arg,))
  return 0

def cmd_ls(args, verbose=None, log=None):
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

def cmd_mount(args, verbose=None, log=None):
  ''' Mount the specified special as on the specified mountpoint directory.
      Requires FUSE support.
  '''
  badopts = False
  try:
    special = args.pop(0)
  except IndexError:
    error("missing special")
    badopts = True
  try:
    mountpoint = args.pop(0)
  except IndexError:
    error("missing mountpoint")
    badopts = True
  if args:
    error("extra arguments: %s", ' '.join(args))
    badopts = True
  if badopts:
    raise GetoptError("bad arguments")
  from cs.venti.vtfuse import mount
  if not os.path.isdir(mountpoint):
    error("%s: mountpoint is not a directory", mountpoint)
    return 1
  with Pfx(special):
    try:
      when, E = last_Dirent(special, missing_ok=True)
    except OSError as e:
      error("can't access special: %s", e)
      return 1
    # no "last entry" (==> first use) - make an empty directory
    if E is None:
      E = Dir('/')
    else:
      dump_Dirent(E, recurse=True)
      if not E.isdir:
        error("expected directory, not file: %s", E)
        return 1
    with Pfx("open('a')"):
      syncfp = open(special, 'a')
  S = defaults.S
  mount(mountpoint, E, S, syncfp=syncfp)
  return 0

def cmd_pack(args, verbose=None, log=None):
  ''' Replace each I<path> with an archive file I<path>B<.vt> referring
      to the stored content of I<path>.
  '''
  if not args:
    raise GetoptError("missing paths")
  xit = 0
  modes = CopyModes(trust_size_mtime=True)
  
  for ospath in args:
    with Pfx(ospath):
      if not os.path.exists(ospath):
        error("missing")
        xit = 1
        continue
      arpath = ospath + '.vt'
      try:
        update_archive(arpath, ospath, modes, create_archive=True, log=log)
      except IOError as e:
        error("%s" % (e,))
        xit = 1
        continue
      log("remove %r", ospath)
      if os.path.isdir(ospath):
        shutil.rmtree(ospath)
      else:
        os.remove(ospath)
  return xit

def cmd_pull(args, verbose=None, log=None):
  ''' Pull missing content from other Stores.
  '''
  if not args:
    raise GetoptError("missing stores")
  raise NotImplementedError

def cmd_scan(args, verbose=None, log=None):
  ''' Read a datafile and report.
  '''
  if len(args) != 1:
    raise GetoptError("missing datafile")
  datafile = args[0]
  from cs.venti.datafile import DataFile, F_COMPRESSED, decompress
  from cs.venti.hash import Hash_SHA1
  with Pfx(datafile):
    with DataFile(datafile) as dfp:
      for offset, flags, data in dfp.scan():
        if flags & F_COMPRESSED:
          data2 = decompress(data)
        else:
          data2 = data
        print(Hash_SHA1.from_data(data2), offset, flags, len(data))
  return 0

def cmd_unpack(args, verbose=None, log=None):
  ''' Unpack the archive file I<archive>B<.vt> as I<archive>.
  '''
  if len(args) < 1:
    raise GetoptError("missing archive name")
  arpath = args.pop(0)
  arbase, arext = os.path.splitext(arpath)
  X("arbase=%r, arext=%r", arbase, arext)
  if arext != '.vt':
    raise GetoptError("archive name does not end in .vt: %r" % (arpath,))
  if len(args) > 0:
    raise GetoptError("extra arguments after archive name %r" % (arpath,))
  if os.path.exists(arbase):
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
      copy_out_dir(rootE, arbase, CopyModes(do_mkdir=True), log=log)
    else:
      copy_out_file(rootE, arbase, log=log)
  return 0

def lsDirent(fp, E, name):
  ''' Transcribe a Dirent as an ls-style listing.
  '''
  B = E.block
  st = E.stat()
  st_mode, st_ino, st_dev, st_nlink, st_uid, st_gid, st_size, \
    st_atime, st_mtime, st_ctime = st
  t = datetime.datetime.fromtimestamp(int(st_mtime))
  fp.write("%c %-41s %s %6d %s\n" \
           % (('d' if E.isdir else 'f'),
              hexify(B.hashcode), t, st_size, name))

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
      ls(os.path.join(path, name), D.chdir1(name), recurse, fp)

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

def silent(msg, *args, file=None):
  ''' Dummy function to discard messages.
  '''
  pass

if __name__ == '__main__':
  sys.exit(main(sys.argv))
