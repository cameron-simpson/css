#!/usr/bin/env python3

r'''A command and utility functions for making listings of file content hashcodes
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

    A common "backup to remote" use case of mine is addressed by:

        hashindex rsync src dst

    which rearranges `dst` based on `src`, then uses rsync(1) to update `dst`.

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

        hashindex comm -1 -o '{fspath}' src rhost:dst \
        | rsync -a --files-from=- src/ xferdir/

    I've got a script [`pref-xfer`](https://hg.sr.ht/~cameron-simpson/css/browse/bin-cs/prep-xfer)
    which does this with some conveniences and sanity checks.
'''

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from getopt import GetoptError
from io import TextIOBase
import os
from os.path import (
    basename,
    dirname,
    exists as existspath,
    isabs as isabspath,
    isdir as isdirpath,
    isfile as isfilepath,
    islink,
    join as joinpath,
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

from cs.cmdutils import BaseCommand, popopts, qprint
from cs.context import contextif
from cs.deco import fmtdoc, uses_quiet, uses_cmd_options
from cs.fs import needdir, RemotePath, remove_protecting, shortpath
from cs.fstags import FSTags, uses_fstags
from cs.hashutils import BaseHashCode
from cs.lex import r
from cs.logutils import warning
from cs.pfx import Pfx, pfx, pfx_call
from cs.psutils import pipefrom, run
from cs.resources import RunState, uses_runstate
from cs.upd import (
    above as above_upd,
    print,
    run_task,  # pylint: disable=redefined-builtin
)

__version__ = '20250531'

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
  ''' A tool to generate and use indices of file content hashcodes.
  '''

  # pylint: disable=use-dict-literal
  USAGE_KEYWORDS = dict(
      HASHNAME_DEFAULT=HASHNAME_DEFAULT,
      OUTPUT_FORMAT_DEFAULT=OUTPUT_FORMAT_DEFAULT,
  )

  SUBCOMMAND_ARGV_DEFAULT = 'ls'

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
    ''' Sanity check the hashname, open the fstags.
    '''
    hashname = self.options.hashname
    try:
      pfx_call(BaseHashCode.hashclass, hashname)
    except ValueError as e:
      warning(f'{hashname=} not known: {e}')
      yield 1
    else:
      with super().run_context(**kw):
        with fstags:
          yield

  @staticmethod
  def poppathspec(
      argv: List[str], name: str = 'dirspec', check_isdir=False
  ) -> RemotePath:
    ''' Pop a leading dirspec from `argv`, a filesystem path with
        an optional leading `[user@]rhost:` prefix.
        Return a `(host,fspath)` 2-tuple being the remote host (`None` if omitted)
        and the filesystem path.
        Raises `GetoptError` on a missing or invalid argument.
    '''
    if not argv:
      raise GetoptError(f'missing {name}')
    spec = argv.pop(0)
    with Pfx("%s %r", name, spec):
      dirspec = RemotePath.from_str(spec)
      host, fspath = dirspec
      if host is None:
        if check_isdir and fspath != '-' and not isdirpath(fspath):
          raise GetoptError(f'not a directory: {fspath!r}')
      elif fspath == '-':
        raise GetoptError(f'remote {fspath!r} not supported')
    return dirspec

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
    path1 = self.poppathspec(argv, 'path1')
    path2 = self.poppathspec(argv, 'path2')
    if argv:
      warning("extra arguments after path2: %r", argv)
      badopts = True
    if path1 == (None, '-') and path2 == (None, '-'):
      warning('path1 and path2 may not both be "-"')
      badopts = True
    if badopts:
      raise GetoptError('bad arguments')
    with run_task(f'scan {path1}'):
      fspaths1_by_hashcode = defaultdict(list)
      for hashcode, fspath in hashindex(path1, relative=relative):
        runstate.raiseif()
        if hashcode is not None:
          fspaths1_by_hashcode[hashcode].append(fspath)
    with run_task(f'scan {path2}'):
      fspaths2_by_hashcode = defaultdict(list)
      for hashcode, fspath in hashindex(path2, relative=relative):
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
              This requires each command line path to be a directory.''',
      )
  )
  @uses_runstate
  def cmd_ls(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} [options...] [[host:]path...]
          Walk filesystem paths and emit a listing.
          The default path is the current directory.
          In quiet mode (-q) the hash indicies are just updated
          and nothing is printed.
    '''
    options = self.options
    output_format = options.output_format
    quiet = options.quiet
    relative = options.relative
    if not argv:
      argv = ['.']
    xit = 0
    while argv:
      runstate.raiseif()
      path = self.poppathspec(argv, '[host:]path')
      with Pfx(path):
        if path.host is None:
          if relative and not isdirpath(path.fspath):
            warning("not a directory and -r (relative) specified")
            xit = 1
            continue
        current_dirpath = None
        with run_task("scan") as proxy:
          for h, fspath in hashindex(path, relative=relative):
            runstate.raiseif()
            dirpath = dirname(fspath)
            if dirpath != current_dirpath:
              proxy.text = shortpath(dirpath)
              current_dirpath = dirpath
            if h is not None:
              quiet or print(output_format.format(hashcode=h, fspath=fspath))
    return xit

  @popopts(
      _1=('once', 'Rearrange only one file.'),
      ln=('link_mode', 'Hard link files instead of moving them.'),
      s='symlink_mode',
  )
  @typechecked
  def cmd_rearrange(self, argv):
    ''' Usage: {cmd} {{[[user@]host:]refdir|-}} [[user@]rhost:]srcdir [dstdir]
          Rearrange files from srcdir into dstdir based on their positions in refdir.
          Arguments:
            refdir    The reference directory, which may be local or remote
                      or "-" indicating that a hash index will be read from
                      standard input.
            srcdir    The directory containing the files to be rearranged,
                      which may be local or remote.
            dstdir    Optional destination directory for the rearranged files.
                      Default is the srcdir.
    '''
    options = self.options
    badopts = False
    doit = options.doit
    hashname = options.hashname
    move_mode = not options.link_mode
    once = options.once
    quiet = options.quiet
    verbose = options.verbose or not quiet
    symlink_mode = options.symlink_mode
    if not argv:
      warning("missing refdir")
      badopts = True
    elif argv[0] == '-':
      argv.pop(0)
      refdir = None  # read hashindex from standard input
    else:
      refdir = self.poppathspec(argv, 'refdir', check_isdir=True)
    srcdir = self.poppathspec(argv, 'srcdir', check_isdir=True)
    if argv:
      dstdir = self.poppathspec(argv, 'dstdir', check_isdir=True)
      if dstdir.host != srcdir.host:
        warning("srcdir host must be the same as dstdir host")
        badopts = True
    else:
      dstdir = srcdir
    if argv:
      warning("extra arguments: %r", argv)
      badopts = True
    if badopts:
      raise GetoptError('bad arguments')
    xit = 0
    if refdir is None:
      # read hash index from standard input
      fspaths_by_hashcode = defaultdict(list)
      for hashcode, fspath in read_hashindex(sys.stdin, hashname=hashname):
        fspaths_by_hashcode[hashcode].append(fspath)
    else:
      # scan the reference directory
      with run_task(f'scan refdir {refdir}'):
        fspaths_by_hashcode = hashindex_map(refdir, relative=True)
    if not fspaths_by_hashcode:
      qprint("no files in refdir, nothing to rearrange")
      return xit
    # rearrange the source directory.
    assert srcdir.host == dstdir.host
    if srcdir.host is None:
      # local srcdir and dstdir
      rearrange(
          srcdir.fspath,
          fspaths_by_hashcode,
          dstdir.fspath,
          doit=doit,
          move_mode=move_mode,
          once=once,
          quiet=quiet,
          symlink_mode=symlink_mode,
      )
    else:
      # remote srcdir and dstdir
      xit = remote_rearrange(
          srcdir.host,
          srcdir.fspath,
          dstdir.fspath,
          fspaths_by_hashcode,
          move_mode=move_mode,
          once=once,
          quiet=quiet,
          symlink_mode=symlink_mode,
      )
    return xit

  @uses_fstags
  @popopts(
      bwlimit_='Rsync bandwidth limit, passed to rsync.',
      delete='Delete from dstdir, passed to rsync.',
      partial='Keep partially transferred files, passed to rsync.',
  )
  def cmd_rsync(self, argv, *, fstags: FSTags):
    ''' Usage: {cmd} [options] srcdir dstdir
          Rearrange dstdir according to srcdir then rsync srcdir into dstdir.
    '''
    options = self.options
    bwlimit = options.bwlimit
    delete = options.delete
    doit = options.doit
    partial = options.partial
    quiet = options.quiet
    runstate = options.runstate
    ssh_exe = options.ssh_exe
    verbose = options.verbose
    srcdir = self.poppathspec(argv, 'srcdir', check_isdir=True)
    dstdir = self.poppathspec(argv, 'dstdir', check_isdir=True)
    with run_task(f'scan srcdir {srcdir}'):
      fspaths_by_hashcode = hashindex_map(srcdir, relative=True)
    xit = 0
    # rearrange the source directory.
    with run_task(f'rearrange dstdir {dstdir}'):
      if dstdir.host is None:
        # local srcdir and dstdir
        rearrange(
            srcdir.fspath,
            fspaths_by_hashcode,
            dstdir.fspath,
            move_mode=True,
            symlink_mode=False,
        )
      else:
        # remote srcdir and dstdir
        xit = remote_rearrange(
            dstdir.host,
            dstdir.fspath,
            dstdir.fspath,
            fspaths_by_hashcode,
            move_mode=True,
            symlink_mode=False,
        )
    if xit == 0:
      # rsync source to destination
      with above_upd():
        run(
            [
                'rsync',
                not doit and '-n',
                ('-e', ssh_exe),
                not quiet and '-i',
                verbose and '-v',
                partial and '--partial',
                bwlimit and ('--bwlimit', bwlimit),
                doit and not quiet and sys.stderr.isatty() and '--progress',
                '-ar',
                delete and '--delete',
                f'--exclude={fstags.tagsfile_basename}',
                '--',
                f'{srcdir}/',
                f'{dstdir}/',
            ],
            doit=True,
            quiet=quiet,
        )

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
@uses_runstate
def hashindex(
    src: Union[TextIOBase, RemotePath, str, Tuple[Union[None, str], str]],
    *,
    hashname: str,
    relative: bool = False,
    runstate: RunState,
    **kw,
) -> Iterable[Tuple[Union[None, BaseHashCode], Union[None, str]]]:
  ''' Generator yielding `(hashcode,filepath)` 2-tuples
      for the files in `src`, which may be a file or a `RemotePath`
      or a `(host,fspath)` 2-tuple or a filesystem path.
      Note that this yields `(None,filepath)` for files which cannot be accessed.
  '''
  if isinstance(src, TextIOBase):
    # read hashindex from file
    yield from read_hashindex(src, hashname=hashname, **kw)
    return
  rhost, fspath = RemotePath.promote(src)
  if rhost is None and fspath == '-':
    # read hashindex from stdin
    for item in read_hashindex(sys.stdin, hashname=hashname, **kw):
      runstate.raiseif()
      yield item
    return
  if rhost is not None:
    # read hashindex from remote
    if fspath == '-':
      raise ValueError("cannot read remote stdin")
    for item in read_remote_hashindex(
        rhost,
        fspath,
        hashname=hashname,
        relative=relative,
        **kw,
    ):
      runstate.raiseif()
      yield item
    return
  # local hashindex
  if isfilepath(fspath):
    h = file_checksum(fspath, hashname=hashname)
    yield h, fspath
  elif isdirpath(fspath):
    for filepath in dir_filepaths(fspath):
      runstate.raiseif()
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
@typechecked
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

@uses_cmd_options(hashname=None)
def hashindex_map(dirpath: str,
                  *,
                  hashname: str,
                  relative=False) -> dict[BaseHashCode, list[str]]:
  ''' Construct a mapping of hashcodes to filesystem paths
      by walking `dirpath`.
  '''
  fspaths_by_hashcode = defaultdict(list)
  for hashcode, fspath in hashindex(dirpath, hashname=hashname,
                                    relative=relative):
    if hashcode is not None:
      fspaths_by_hashcode[hashcode].append(fspath)
  return fspaths_by_hashcode

# TODO: use the functions from cs.fs ?
@uses_fstags
def dir_filepaths(dirpath: str, *, fstags: FSTags):
  ''' Generator yielding the filesystem paths of the files in `dirpath`.
  '''
  if not isdirpath(dirpath):
    raise ValueError(f'dir_filepaths: not a directory: {dirpath=}')
  # TODO: use cs.fs.scandirtree (os.walk ignores errors)
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

@uses_cmd_options(doit=True, hashname=None, quiet=True)
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
@typechecked
def rearrange(
    srcdirpath: str,
    rfspaths_by_hashcode,
    dstdirpath: str | None = None,
    *,
    hashname: str,
    move_mode: bool = False,
    once: bool = False,
    symlink_mode=False,
    doit: bool,
    fstags: FSTags,
    runstate: RunState,
    quiet: bool,
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
      * `move_mode`: move files instead of linking them
      * `symlink_mode`: symlink files instead of linking them
      * `doit`: if true do the link/move/symlink, otherwise just print
  '''
  if dstdirpath is None:
    dstdirpath = srcdirpath
    task_label = f'rearrange {shortpath(srcdirpath)}'
  elif dstdirpath == srcdirpath:
    task_label = f'rearrange {shortpath(srcdirpath)}'
  else:
    task_label = f'rearrange {shortpath(srcdirpath)} into {shortpath(dstdirpath)}'
  with run_task(task_label) as proxy:
    to_remove = set()
    try:
      for srcpath, rfspaths in dir_remap(
          srcdirpath,
          rfspaths_by_hashcode,
          hashname=hashname,
      ):
        runstate.raiseif()
        if not rfspaths:
          continue
        filename = basename(srcpath)
        if filename.startswith('.') or filename == fstags.tagsfile_basename:
          # skip hidden or fstags files
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
              # already there, skip
              continue
            dstpath = joinpath(dstdirpath, rdstpath)
            if doit:
              needdir(dirname(dstpath), use_makedirs=True, log=warning)
            # merge the src to the dst
            try:
              merged = merge(
                  srcpath,
                  dstpath,
                  opname=opname,
                  hashname=hashname,
                  move_mode=move_mode and len(rfspaths) == 1,
                  symlink_mode=symlink_mode,
                  fstags=fstags,
                  doit=doit,
                  quiet=quiet,
              )
            except FileExistsError as e:
              warning("%s %s -> %s: %s", opname, srcpath, dstpath, e)
            else:
              if move_mode and len(rfspaths) > 1 and rsrcpath not in rfspaths:
                if doit:
                  to_remove.add(srcpath)
            if merged and once:
              raise StopIteration
    except StopIteration:
      pass
    # purge the srcpaths last because we might want them multiple
    # times during the main loop (files with the same hashcode)
    if doit and to_remove:
      for srcpath in sorted(to_remove):
        pfx_call(os.remove, srcpath)

@fmtdoc
@uses_cmd_options(
    doit=True,
    hashindex_exe=HASHINDEX_EXE_DEFAULT,
    hashname=HASHNAME_DEFAULT,
    move_mode=True,
    once=False,
    quiet=False,
    symlink_mode=False,
)
@typechecked
def remote_rearrange(
    rhost: str,
    srcdir: str,
    dstdir: str,
    fspaths_by_hashcode: Mapping[BaseHashCode, List[str]],
    *,
    doit: bool,
    hashindex_exe: str,
    hashname: str,
    move_mode: bool,
    once: bool,
    quiet: bool,
    symlink_mode: bool,
):
  ''' Rearrange a remote directory `srcdir` on `rhost` into `dstdir`
      on `rhost` according to the hashcode mapping `fspaths_by_hashcode`.
  '''
  # remote srcdir and dstdir
  # prepare the remote input
  reflines = []
  for hashcode, fspaths in fspaths_by_hashcode.items():
    for fspath in fspaths:
      reflines.append(f'{hashcode} {fspath}\n')
  input_s = "".join(reflines)
  return run_remote_hashindex(
      rhost,
      [
          'rearrange',
          not doit and '-n',
          ('-h', hashname),
          once and '-1',
          quiet and '-q',
          not (move_mode or symlink_mode) and '--ln',
          symlink_mode and '-s',
          '-',
          RemotePath.str(None, srcdir),
          RemotePath.str(None, dstdir),
      ],
      hashindex_exe=hashindex_exe,
      input=input_s,
      text=True,
      doit=True,  # we pass -n to the remote hashindex
      quiet=False,
  ).returncode

@pfx
@uses_fstags
@uses_quiet
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
    quiet: bool,
) -> bool:
  ''' Merge `srcpath` to `dstpath`, _preserving `dstpath` if present_.
      Return `True` if something was done, `False` if this was a no-op.
      Raise `FileExistsError` if `dstpath` exists with different content.

      This is aimed at situations such as merging downloads with
      an existing corpus, which might have hard links etc, so
      `dstpath` is the important half of the pair.

      NB: `symlink_mode` is currently disabled.

      If 'dstpath' exists, checksum their contents and raise
      `FileExistsError` if they differ.
      If `dstpath` does not exist, move/link/symlink `srcpath` to `dstpath`.
      This also merges the fstags from `srcpath` to `dstpath`.

      Otherwise the files have the same content, merge while preserving `dstpath`.
  '''
  if symlink_mode:
    raise RuntimeError("symlink_mode disabled for now, untrusted")
  if opname is None:
    opname = "ln -s" if symlink_mode else "mv" if move_mode else "ln"
  if dstpath == srcpath:
    # fast no-op
    return False
  if not existspath(srcpath):
    # fast failure
    raise FileNotFoundError(srcpath)
  assert not symlink_mode
  if not existspath(dstpath):
    # link or move to dstpath
    qprint(
        opname,
        shortpath(srcpath),
        "->",
        shortpath(dstpath),
        quiet=quiet,
        flush=True,
    )
    if doit:
      # safe rename - link to dst them remove src
      needdir(dirname(dstpath))
      pfx_call(os.link, srcpath, dstpath)
      fstags[dstpath].update(fstags[srcpath])
      pfx_call(os.remove, srcpath)
    return True
  if samefile(srcpath, dstpath):
    # links to same file, merge the tags from srcpath -> dstpath
    fstags[dstpath].update(fstags[srcpath])
    if not move_mode:
      # we're done
      return False
    # Remove srcpath.
    qprint(
        "remove",
        shortpath(srcpath),
        "# same file as",
        shortpath(dstpath),
        quiet=quiet,
        flush=True,
    )
    if doit:
      remove_protecting(srcpath, dstpath)
    return True
  # distinct files, compare their contents
  srchashcode = file_checksum(srcpath, hashname=hashname)
  dsthashcode = file_checksum(dstpath, hashname=hashname)
  if srchashcode != dsthashcode:
    # different content, fail
    raise FileExistsError(
        'dstpath already exists with different content hashcode'
        f': {srchashcode=} != {dsthashcode=}'
    )
  # same content
  if not move_mode:
    # Link dstpath to srcpath.
    qprint(
        "link",
        shortpath(dstpath),
        "to",
        shortpath(srcpath),
        "# same content",
        quiet=quiet,
        flush=True,
    )
    if doit:
      remove_protecting(srcpath, dstpath)
      pfx_call(os.link, dstpath, srcpath)
      fstags[srcpath].update(fstags[dstpath])
    return True
  # Remove srcpath.
  qprint(
      "remove",
      shortpath(srcpath),
      "# identical content at",
      shortpath(dstpath),
      quiet=quiet,
      flush=True,
  )
  if doit:
    remove_protecting(srcpath, dstpath)
  return True

if __name__ == '__main__':
  sys.exit(main(sys.argv))
