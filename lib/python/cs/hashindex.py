#!/usr/bin/env python3

''' A command and utility functions for making listings of file content hashcodes
    and manipulating directory trees based on such a hash index.

    This largely exists to solve my "what has changed remotely?" or
    "what has been filed where?" problems by comparing file trees
    using the files' content hashcodes.

    This does require reading every file once to compute its hashcode,
    but the hashcodes (and file sizes and mtimes when read) are
    stored beside the file in `.fstags` files (see the `cs.fstags`
    module), so that a file does not need to be reread on subsequent
    comparisons.

    `hashindex` knows how to invoke itself remotely using `ssh`
    (this does require `hashindex` to be installed on the remote host)
    and can thus be used to compare a local and remote tree, for example:

        hashindex comm -1 localtree remotehost:remotetree

    When you point `hashindex` at a remote tree, it uses `ssh` to
    run `hashindex` on the remote host, so all the content hashing
    is done locally to the remote host instead of copying files
    over the network.

    You can also use it to rearrange a tree based on the locations
    of corresponding files in another tree. Consider a media tree
    replicated between 2 hosts. If the source tree gets rearranged,
    the destination can be equivalently rearranged without copying
    the files, for example:

        hashindex rearrange sourcehost:sourcetree localtree

    If `fstags mv` was used to do the original rearrangement then
    the hashcodes will be copied to the new locations, saving a
    rescan of the source file. I keep a shell alias `mv="fstags mv"`
    so this is routine for me.

    I have a backup script [`histbackup`](https://hg.sr.ht/~cameron-simpson/css/browse/bin/histbackup)
    which works by making a hard link tree of the previous backup
    and `rsync`ing into it.  It has long been subject to huge
    transfers if the source tree gets rearranged. Now it has a
    `--hashindex` option to get it to run a `hashindex rearrange`
    between the hard linking to the new backup tree and the `rsync`
    from the source to the new tree.

    If network bandwith is limited or quotaed, you can use the
    comparison function to prepare a list of files missing from the
    remote location and copy them to a transfer drive for carrying
    to the remote site when opportune. Example:

        hashindex comm -1 -o '{fspath}' src rhost:dst \\
        | rsync -a --files-from=- src/ xferdir/

    I've got a script [`pref-xfer`](https://hg.sr.ht/~cameron-simpson/css/browse/bin-cs/prep-xfer)
    which does this with some conveniences and sanity checks.
'''

from collections import defaultdict
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import errno
from getopt import GetoptError
from io import TextIOBase
import os
from os.path import (
    abspath,
    basename,
    dirname,
    exists as existspath,
    isabs as isabspath,
    isdir as isdirpath,
    isfile as isfilepath,
    islink,
    join as joinpath,
    realpath,
    relpath,
    samefile,
)
import shlex
from stat import S_ISREG
from subprocess import CalledProcessError
import sys
from typing import Iterable, List, Mapping, Optional, Tuple, Union

from icontract import require
from typeguard import typechecked

from cs.cmdutils import BaseCommand, popopts, vprint
from cs.context import contextif, reconfigure_file
from cs.deco import fmtdoc, uses_verbose, uses_cmd_options
from cs.fs import needdir, shortpath
from cs.fstags import FSTags, uses_fstags
from cs.hashutils import BaseHashCode
from cs.lex import r, split_remote_path
from cs.logutils import warning
from cs.pfx import Pfx, pfx, pfx_call
from cs.psutils import pipefrom, run
from cs.resources import RunState, uses_runstate
from cs.upd import above as above_upd, print, run_task  # pylint: disable=redefined-builtin

__version__ = '20241207-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'entry_points': {
        'console_scripts': {
            'hashindex': 'cs.hashindex:main'
        },
    },
    'install_requires': [
        'cs.cmdutils>=20240211',
        'cs.context',
        'cs.deco',
        'cs.fs',
        'cs.fstags',
        'cs.hashutils',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.psutils',
        'cs.resources',
        'cs.upd',
        'blake3',
        'icontract',
        'typeguard',
    ],
}

HASHNAME_DEFAULT = 'blake3'
HASHINDEX_EXE_DEFAULT = 'hashindex'
OUTPUT_FORMAT_DEFAULT = '{hashcode} {fspath}'

def main(argv=None):
  ''' Commandline implementation.
  '''
  return HashIndexCommand(argv).run()

class HashIndexCommand(BaseCommand):
  ''' A tool to generate indices of file content hashcodes
      and to link or otherwise rearrange files to destinations based
      on their hashcode.
  '''

  USAGE_FORMAT = r'''Usage: {cmd} subcommand...
    Generate or process file content hash listings.'''
  # pylint: disable=use-dict-literal
  USAGE_KEYWORDS = dict(
      HASHNAME_DEFAULT=HASHNAME_DEFAULT,
      OUTPUT_FORMAT_DEFAULT=OUTPUT_FORMAT_DEFAULT,
  )

  @dataclass
  class Options(BaseCommand.Options):
    ''' Options for `HashIndexCommand`.
    '''
    hashname: str = HASHNAME_DEFAULT
    move_mode: bool = False
    hashindex_exe: str = HASHINDEX_EXE_DEFAULT
    symlink_mode: bool = False
    relative: Optional[bool] = None
    output_format: str = OUTPUT_FORMAT_DEFAULT

    # pylint: disable=use-dict-literal
    COMMON_OPT_SPECS = dict(
        **BaseCommand.Options.COMMON_OPT_SPECS,
        h_=('hashname', 'The file content hash algorithm name.'),
        H_=('hashindex_exe', 'The remote hashindex executable.'),
        o_=(
            'output_format',
            'Output format, default: {OUTPUT_FORMAT_DEFAULT!r}'
        ),
    )

  # pylint: disable=arguments-differ
  @contextmanager
  @uses_fstags
  def run_context(self, *, fstags: FSTags, **kw):
    hashname = self.options.hashname
    try:
      pfx_call(BaseHashCode.hashclass, hashname)
    except ValueError as e:
      warning(f'{hashname=} not known: {e}')
      yield 1
    else:
      with fstags:
        with super().run_context(**kw):
          yield

  #pylint: disable=too-many-locals
  @uses_runstate
  @popopts(
      _1=('path1_only', 'List hashes and paths only present in path1.'),
      _2=('path2_only', 'List hashes and paths only present in path2.'),
      _3=('path12', 'List hashes and paths present in path1 and path2.'),
      r=('relative', 'Emit relative paths in the listing.'),
  )
  def cmd_comm(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} {{-1|-2|-3|-r}} {{path1|-}} {{path2|-}}
          Compare the filepaths in path1 and path2 by content.
    '''
    badopts = False
    options = self.options
    output_format = options.output_format
    relative = options.relative
    path1_only = options.path1_only
    path2_only = options.path2_only
    path12 = options.path12
    mode_count = len(list(filter(None, (path1_only, path2_only, path12))))
    if not mode_count:
      warning("one of -1, -2 or -3 must be provided")
      badopts = True
    elif mode_count > 1:
      warning("only one of -1, -2 or -3 may be provided")
      badopts = True
    if not argv:
      warning("missing path1")
      badopts = True
      path1spec = None
    else:
      path1spec = argv.pop(0)
      with Pfx("path1 %r", path1spec):
        path1host, path1dir = split_remote_path(path1spec)
        if path1host is None:
          if path1dir != '-':
            if not isdirpath(path1dir):
              warning("not a directory")
              badopts = True
        elif path1dir == '-':
          warning('remote "-" not supported')
          badopts = True
    if not argv:
      warning("missing path2")
      badopts = True
      path2spec = None
    else:
      path2spec = argv.pop(0)
      with Pfx("path2 %r", path2spec):
        path2host, path2dir = split_remote_path(path2spec)
        if path2host is None:
          if path2dir != '-':
            if not isdirpath(path2dir):
              warning("not a directory")
              badopts = True
        elif path2dir == '-':
          warning('remote "-" not supported')
          badopts = True
    if argv:
      warning("extra arguments after path2: %r", argv)
      badopts = True
    if path1spec == '-' and path2spec == '-':
      warning('path1 and path2 may not both be "-"')
      badopts = True
    if badopts:
      raise GetoptError('bad arguments')
    with Pfx("path1 %r", path1spec):
      fspaths1_by_hashcode = defaultdict(list)
      for hashcode, fspath in hashindex(
          (path1host, path1dir),
          relative=relative,
      ):
        runstate.raiseif()
        if hashcode is not None:
          fspaths1_by_hashcode[hashcode].append(fspath)
    with Pfx("path2 %r", path2spec):
      fspaths2_by_hashcode = defaultdict(list)
      for hashcode, fspath in hashindex(
          (path2host, path2dir),
          relative=relative,
      ):
        runstate.raiseif()
        if hashcode is not None:
          fspaths2_by_hashcode[hashcode].append(fspath)
    if path1_only:
      for hashcode in fspaths1_by_hashcode.keys() - fspaths2_by_hashcode.keys(
      ):
        runstate.raiseif()
        for fspath in fspaths1_by_hashcode[hashcode]:
          print(output_format.format(hashcode=hashcode, fspath=fspath))
    elif path2_only:
      for hashcode in fspaths2_by_hashcode.keys() - fspaths1_by_hashcode.keys(
      ):
        runstate.raiseif()
        for fspath in fspaths2_by_hashcode[hashcode]:
          print(output_format.format(hashcode=hashcode, fspath=fspath))
    else:
      assert path12
      for hashcode in fspaths2_by_hashcode.keys() & fspaths1_by_hashcode.keys(
      ):
        runstate.raiseif()
        for fspath in fspaths1_by_hashcode[hashcode]:
          print(output_format.format(hashcode=hashcode, fspath=fspath))

  @popopts(
      r=(
          'relative',
          ''' Emit relative paths in the listing.
              This requires each path to be a directory.''',
      )
  )
  @uses_runstate
  def cmd_ls(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} [options...] [[host:]path...]
          Walk filesystem paths and emit a listing.
          The default path is the current directory.
    '''
    options = self.options
    output_format = options.output_format
    relative = options.relative
    if not argv:
      argv = ['.']
    xit = 0
    for path in argv:
      runstate.raiseif()
      with Pfx(path):
        rhost, lpath = split_remote_path(path)
        if rhost is None:
          if relative and not isdirpath(path):
            warning("not a directory and -r (relative) specified")
            xit = 1
            continue
        for h, fspath in hashindex(
            (rhost, lpath),
            relative=relative,
        ):
          runstate.raiseif()
          if h is not None:
            print(output_format.format(hashcode=h, fspath=fspath))
    return xit

  @popopts(
      mv='move_mode',
      s='synmlink_mode',
  )
  @typechecked
  def cmd_rearrange(self, argv):
    ''' Usage: {cmd} [options...] {{[[user@]host:]refdir|-}} [[user@]rhost:]targetdir [dstdir]
          Rearrange files from targetdir into dstdir based on their positions in refdir.
          Other arguments:
            refdir      The reference directory, which may be local or remote
                        or "-" indicating that a hash index will be read from
                        standard input.
            targetdir   The directory containing the files to be rearranged,
                        which may be local or remote.
            dstdir      Optional destination directory for the rearranged files.
                        Default is the targetdir.
                        It is taken to be on the same host as targetdir.
    '''
    options = self.options
    badopts = False
    doit = options.doit
    move_mode = options.move_mode
    quiet = options.quiet
    symlink_mode = options.symlink_mode
    if not argv:
      warning("missing refdir")
      badopts = True
      refdir = None
    else:
      refspec = argv.pop(0)
      with Pfx("refdir %r", refspec):
        refhost, refdir = split_remote_path(refspec)
        if refhost is None:
          if refdir != '-':
            if not isdirpath(refdir):
              warning("not a directory")
              badopts = True
        elif refdir == '-':
          warning('remote "-" not supported')
          badopts = True
    if not argv:
      warning("missing targetdir")
      badopts = True
      targetdir = None
    else:
      targetspec = argv.pop(0)
      with Pfx("targetdir %r", targetspec):
        targethost, targetdir = split_remote_path(targetspec)
        if targethost is None:
          if not isdirpath(targetdir):
            warning("not a directory")
            badopts = True
    if argv:
      dstdir = argv.pop(0)
      with Pfx("dstdir %r", dstdir):
        if targethost is None and not isdirpath(dstdir):
          warning("not a directory")
          badopts = True
    else:
      dstdir = targetdir
    if argv:
      warning("extra arguments: %r", argv)
      badopts = True
    if badopts:
      raise GetoptError('bad arguments')
    # scan the reference directory
    fspaths_by_hashcode = defaultdict(list)
    xit = 0
    with run_task(f'hashindex {refspec}'):
      for hashcode, fspath in hashindex(
          (refhost, refdir),
          relative=True,
      ):
        if hashcode is not None:
          fspaths_by_hashcode[hashcode].append(fspath)
    # rearrange the target directory.
    with (nullcontext()
          if refhost or targethost else run_task(f'rearrange {targetspec}')):
      if targethost is None:
        with contextif(
            not quiet,
            reconfigure_file,
            sys.stdout,
            line_buffering=True,
        ):
          rearrange(
              targetdir,
              fspaths_by_hashcode,
              dstdir,
              move_mode=move_mode,
              symlink_mode=symlink_mode,
          )
      else:
        # prepare the remote input
        reflines = []
        for hashcode, fspaths in fspaths_by_hashcode.items():
          for fspath in fspaths:
            reflines.append(f'{hashcode} {fspath}\n')
        input_s = "".join(reflines)
        xit = run_remote_hashindex(
            targethost,
            [
                'rearrange',
                not doit and '-n',
                ('-h', options.hashname),
                move_mode and '--mv',
                symlink_mode and '-s',
                '-',
                targetdir,
                dstdir,
            ],
            input=input_s,
            text=True,
            doit=True,  # we pass -n to the remote hashindex
            quiet=False,
        ).returncode
    return xit

@pfx
@uses_fstags
def file_checksum(
    fspath: str,
    hashname: str = HASHNAME_DEFAULT,
    *,
    fstags: FSTags,
) -> Union[BaseHashCode, None]:
  ''' Return the hashcode for the contents of the file at `fspath`.
      Warn and return `None` on `OSError`.
  '''
  st = os.lstat(fspath)
  if not S_ISREG(st.st_mode):
    # ignore nonregular files
    return None
  with fstags:
    cached_hash = fstags[fspath].cached_value(
        f'checksum.{hashname}', 'hashcode'
    )
    hashcode = None
    hashcode_s, state = cached_hash.get()
    if hashcode_s is not None:
      if isinstance(hashcode_s, str):
        try:
          hashcode = BaseHashCode.from_prefixed_hashbytes_hex(hashcode_s)
        except (TypeError, ValueError) as e:
          # unrecognised hashcode
          warning("cannot decode hashcode %s: %s", r(hashcode_s), e)
        else:
          # wrong hash type
          if hashcode.hashname != hashname:
            warning("ignoring unexpected hashname %r", hashcode.hashname)
            hashcode = None
      else:
        warning("ignoring not string cached value: %s", r(hashcode_s))
    if hashcode is None:
      # out of date or no cached entry
      hashclass = BaseHashCode.hashclass(hashname)
      with contextif(
          st.st_size > 1024 * 1024,
          run_task,
          f'checksum {hashname}:{shortpath(fspath)}',
      ):
        try:
          hashcode = hashclass.from_fspath(fspath)
        except OSError as e:
          warning("%s.from_fspath(%r): %s", hashclass.__name__, fspath, e)
        else:
          cached_hash.set(str(hashcode), state=state)
  return hashcode

@uses_cmd_options(hashname=None)
def hashindex(
    fspath: Union[str, TextIOBase, Tuple[Union[None, str], str]],
    *,
    hashname: str,
    relative: bool = False,
    **kw,
) -> Iterable[Tuple[Union[None, BaseHashCode], Union[None, str]]]:
  ''' Generator yielding `(hashcode,filepath)` 2-tuples
      for the files in `fspath`, which may be a file or directory path.
      Note that it yields `(None,filepath)` for files which cannot be accessed.
  '''
  if isinstance(fspath, TextIOBase):
    # read hashindex from file
    f = fspath
    yield from read_hashindex(f, hashname=hashname, **kw)
    return
  if not isinstance(fspath, str):
    # should be a 2-tuple
    if fspath == (None, "-"):
      yield from read_hashindex(sys.stdin, hashname=hashname, **kw)
      return
    rhost, rfspath = fspath
    if rhost is not None:
      # a remote fspath
      yield from read_remote_hashindex(
          rhost,
          rfspath,
          hashname=hashname,
          relative=relative,
          **kw,
      )
      return
    # local fspath because rhost is None
    fspath = rfspath
  # local hashindex
  if isfilepath(fspath):
    h = file_checksum(fspath, hashname=hashname)
    yield h, fspath
  elif isdirpath(fspath):
    for filepath in dir_filepaths(fspath):
      h = file_checksum(filepath, hashname=hashname)
      yield h, relpath(filepath, fspath) if relative else filepath
  else:
    raise ValueError(
        f'hashindex: neither file nor directory: fspath={fspath!r}'
    )

def read_hashindex(
    f,
    start=1,
    *,
    hashname: str,
) -> Iterable[Tuple[Union[None, BaseHashCode], Union[None, str]]]:
  ''' A generator which reads line from the file `f`
      and yields `(hashcode,fspath)` 2-tuples for each line.
      If there are parse errors the `hashcode` or `fspath` may be `None`.
  '''
  for lineno, line in enumerate(f, start):
    with Pfx("%s:%d", f, lineno):
      line = line.rstrip('\n')
      try:
        hashhex, fspath = line.split(None, 1)
      except ValueError as e:
        warning(
            'invalid data, cannot split into hashcode and fspath, %s: %r',
            e,
            line,
        )
        hashcode = None
        fspath = None
      else:
        with Pfx(hashhex):
          try:
            hashcode = BaseHashCode.promote(hashhex)
          except ValueError as e:
            warning("cannot convert to hashcode: %s", e)
            hashcode = None
          else:
            if hashcode.hashname != hashname:
              warning(
                  "bad hashname %r, expected %r", hashcode.hashname, hashname
              )
              hashcode = None
    yield hashcode, fspath

def localpath(fspath: str) -> str:
  ''' Return a filesystem path modified so that it connot be
      misinterpreted as a remote path such as `user@host:path`.

      If `fspath` contains no colon (`:`) or is an absolute path
      or starts with `./` then it is returned unchanged.
      Otherwise a leading `./` is prepended.
  '''
  if ':' not in fspath or isabspath(fspath) or fspath.startswith('./'):
    return fspath
  return './' + fspath

@fmtdoc
@uses_cmd_options(ssh_exe='ssh', hashindex_exe='hashindex')
def read_remote_hashindex(
    rhost: str,
    rdirpath: str,
    *,
    hashname: str,
    quiet=True,
    ssh_exe: str,
    hashindex_exe: str,
    relative: bool = False,
) -> Iterable[Tuple[Union[None, BaseHashCode], Union[None, str]]]:
  ''' A generator which reads a hashindex of a remote directory,
      This runs: `hashindex ls -h hashname -r rdirpath` on the remote host.
      It yields `(hashcode,fspath)` 2-tuples.

      Parameters:
      * `rhost`: the remote host, or `user@host`
      * `rdirpath`: the remote directory path
      * `hashname`: the file content hash algorithm name
      * `ssh_exe`: optional `ssh` command
      * `hashindex_exe`: the remote `hashindex` executable
      * `relative`: optional flag, default `False`;
        if true pass `'-r'` to the remote `hashindex ls` command
      * `check`: whether to check that the remote command has a `0` return code,
        default `True`
  '''
  remote_argv = [
      shlex.split(hashindex_exe),
      'ls',
      ('-h', hashname),
      relative and '-r',
      '--',
      localpath(rdirpath),
  ]
  remote = pipefrom(remote_argv, remote=rhost, ssh_exe=ssh_exe, quiet=quiet)
  yield from read_hashindex(remote.stdout, hashname=hashname)
  remote.wait()
  if remote.returncode != 0:
    raise CalledProcessError(remote.returncode, remote_argv)

@fmtdoc
@uses_cmd_options(hashindex_exe='hashindex')
def run_remote_hashindex(
    rhost: str,
    argv,
    *,
    hashindex_exe: str,
    **subp_options,
):
  ''' Run a remote `hashindex` command.
      Return the `CompletedProcess` result or `None` if `doit` is false.
      Note that as with `cs.psutils.run`, the arguments are resolved
      via `cs.psutils.prep_argv`.

      Parameters:
      * `rhost`: the remote host, or `user@host`
      * `argv`: the command line arguments to be passed to the
        remote `hashindex` command
      * `check`: whether to check that the remote command has a `0` return code,
        default `True`
      Other keyword parameters are passed therough to `cs.psutils.run`.
  '''
  with above_upd():
    return run(
        shlex.split(hashindex_exe) + argv,
        remote=rhost,
        **subp_options,
    )

@uses_fstags
def dir_filepaths(dirpath: str, *, fstags: FSTags):
  ''' Generator yielding the filesystem paths of the files in `dirpath`.
  '''
  for subdirpath, dirnames, filenames in os.walk(dirpath):
    dirnames[:] = sorted(dirnames)
    for filename in sorted(filenames):
      if filename.startswith('.') or filename == fstags.tagsfile_basename:
        continue
      filepath = joinpath(subdirpath, filename)
      if islink(filepath) or not isfilepath(filepath):
        # ignore nonfiles
        continue
      yield filepath

def paths_remap(
    srcpaths: Iterable[str],
    fspaths_by_hashcode: Mapping[BaseHashCode, List[str]],
    *,
    hashname: str,
):
  ''' Generator yielding `(srcpath,fspaths)` 2-tuples.
  '''
  for srcpath in srcpaths:
    with Pfx(srcpath):
      srchashcode = file_checksum(srcpath, hashname=hashname)
      if srchashcode is None:
        warning("no hashcode")
        continue
      fspaths = fspaths_by_hashcode.get(srchashcode, [])
    yield srcpath, fspaths

def dir_remap(
    srcdirpath: str,
    fspaths_by_hashcode: Mapping[BaseHashCode, List[str]],
    *,
    hashname: str,
):
  ''' Generator yielding `(srcpath,[remapped_paths])` 2-tuples
      based on the hashcodes keying `fspaths_by_hashcode`.
  '''
  yield from paths_remap(
      dir_filepaths(srcdirpath), fspaths_by_hashcode, hashname=hashname
  )

@uses_cmd_options(doit=True, hashname=None)
@uses_fstags
@uses_runstate
@require(
    lambda move_mode, symlink_mode: not (move_mode and symlink_mode),
    'move_mode and symlink_mode may not both be true'
)
@require(
    lambda dstdirpath: dstdirpath is None or isdirpath(dstdirpath),
    'dstdirpath is not a directory'
)
def rearrange(
    srcdirpath: str,
    rfspaths_by_hashcode,
    dstdirpath=None,
    *,
    hashname: str,
    move_mode: bool = False,
    symlink_mode=False,
    doit: bool,
    fstags: FSTags,
    runstate: RunState,
):
  ''' Rearrange the files in `dirpath` according to the
      hashcode->[relpaths] `fspaths_by_hashcode`.

      Parameters:
      * `srcdirpath`: the directory whose files are to be rearranged
      * `rfspaths_by_hashcode`: a mapping of hashcode to relative
        pathname to which the original file is to be moved
      * `dstdirpath`: optional target directory for the rearranged files;
        defaults to `srcdirpath`, rearranging the files in place
      * `hashname`: the file content hash algorithm name
      * `move_move`: move files instead of linking them
      * `symlink_mode`: symlink files instead of linking them
      * `doit`: if true do the link/move/symlink, otherwise just print
  '''
  with run_task(f'rearrange {shortpath(srcdirpath)}') as proxy:
    if dstdirpath is None:
      dstdirpath = srcdirpath
    to_remove = set()
    for srcpath, rfspaths in dir_remap(srcdirpath, rfspaths_by_hashcode,
                                       hashname=hashname):
      runstate.raiseif()
      if not rfspaths:
        continue
      filename = basename(srcpath)
      if filename.startswith('.') or filename == fstags.tagsfile_basename:
        continue
      opname = "ln -s" if symlink_mode else "mv" if move_mode else "ln"
      with Pfx(srcpath):
        rsrcpath = relpath(srcpath, srcdirpath)
        ##assert is_valid_rpath(rsrcpath), (
        ##    "rsrcpath:%r is not a clean subpath" % (rsrcpath,)
        ##)
        proxy.text = rsrcpath
        for rdstpath in rfspaths:
          ##assert is_valid_rpath(rdstpath), (
          ##    "rdstpath:%r is not a clean subpath" % (rdstpath,)
          ##)
          if rsrcpath == rdstpath:
            continue
          dstpath = joinpath(dstdirpath, rdstpath)
          if doit:
            needdir(dirname(dstpath), use_makedirs=True, log=warning)
          try:
            merge(
                srcpath,
                dstpath,
                opname=opname,
                hashname=hashname,
                move_mode=False,  # we do our own remove below
                symlink_mode=symlink_mode,
                fstags=fstags,
                doit=doit,
            )
          except FileExistsError as e:
            warning("%s %s -> %s: %s", opname, srcpath, dstpath, e)
          else:
            if move_mode and rsrcpath not in rfspaths:
              if doit:
                to_remove.add(srcpath)
    # purge the srcpaths last because we might want them multiple
    # times during the main loop (files with the same hashcode)
    if doit and to_remove:
      for srcpath in sorted(to_remove):
        pfx_call(os.remove, srcpath)

@pfx
@uses_fstags
@uses_verbose
@require(
    lambda move_mode, symlink_mode: not (move_mode and symlink_mode),
    'move_mode and symlink_mode may not both be true'
)
def merge(
    srcpath: str,
    dstpath: str,
    *,
    opname=None,
    hashname: str,
    move_mode: bool = False,
    symlink_mode=False,
    doit=False,
    fstags: FSTags,
    verbose: bool,
):
  ''' Merge `srcpath` to `dstpath`.

      If `dstpath` does not exist, move/link/symlink `srcpath` to `dstpath`.
      Otherwise checksum their contents and raise `FileExistsError` if they differ.
  '''
  if opname is None:
    opname = "ln -s" if symlink_mode else "mv" if move_mode else "ln"
  if dstpath == srcpath:
    return
  if symlink_mode:
    # check for existing symlink
    sympath = abspath(srcpath)
    try:
      dstsympath = os.readlink(dstpath)
    except FileNotFoundError:
      pass
    except OSError as e:
      if e.errno == errno.EINVAL:
        # not a symlink
        raise FileExistsError(f'dstpath {dstpath!r} not a symlink: {e}') from e
      raise
    else:
      if dstsympath == sympath:
        # identical symlinks, just update the tags
        fstags[dstpath].update(fstags[srcpath])
        return
      raise FileExistsError(
          f'dstpath {dstpath!r} already exists as a symlink to {dstsympath!r}'
      )
  elif existspath(dstpath):
    if (samefile(srcpath, dstpath)
        or (file_checksum(dstpath, hashname=hashname) == file_checksum(
            srcpath, hashname=hashname))):
      # same content - update tags and remove source
      if doit:
        fstags[dstpath].update(fstags[srcpath])
      if move_mode and realpath(srcpath) != realpath(dstpath):
        vprint(
            "remove",
            shortpath(srcpath),
            "# identical content at",
            shortpath(dstpath),
            verbose=verbose,
        )
        if doit:
          pfx_call(os.remove, srcpath)
      return
    # different content, fail
    raise FileExistsError(
        f'dstpath {dstpath!r} already exists with different hashcode'
    )
  vprint(opname, shortpath(srcpath), shortpath(dstpath), verbose=verbose)
  if doit:
    pfx_call(
        fstags.mv, srcpath, dstpath, symlink=symlink_mode, remove=move_mode
    )

if __name__ == '__main__':
  sys.exit(main(sys.argv))
