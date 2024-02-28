#!/usr/bin/env python3

''' A command and utility functions for making listings of file content hashcodes
    and manipulating directory trees based on such a hash index.
'''

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from getopt import GetoptError
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
from stat import S_ISLNK, S_ISREG
from subprocess import CalledProcessError
import sys
from typing import Iterable, List, Mapping, Optional, Tuple, Union

from icontract import require
from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.context import contextif, reconfigure_file
from cs.deco import fmtdoc
from cs.fs import is_valid_rpath, needdir, shortpath
from cs.fstags import FSTags, uses_fstags
from cs.hashutils import BaseHashCode
from cs.lex import split_remote_path
from cs.logutils import warning
from cs.pfx import Pfx, pfx, pfx_call
from cs.psutils import prep_argv, pipefrom, run
from cs.resources import RunState, uses_runstate
from cs.upd import print, run_task, without  # pylint: disable=redefined-builtin

__version__ = '20240216-post'

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
        'icontract',
        'typeguard',
    ],
}

DEFAULT_HASHNAME = 'sha256'
DEFAULT_HASHINDEX_EXE = 'hashindex'
DEFAULT_SSH_EXE = 'ssh'

def main(argv=None):
  ''' Commandline implementation.
  '''
  return HashIndexCommand(argv).run()

class HashIndexCommand(BaseCommand):
  ''' Tool to generate indices of file content hashcodes
      and to link files to destinations based on their hashcode.
  '''

  USAGE_FORMAT = r'''Usage: {cmd} subcommand...
    Generate or process file content hash listings.'''
  USAGE_KEYWORDS = dict(DEFAULT_HASHNAME=DEFAULT_HASHNAME,)

  @dataclass
  class Options(BaseCommand.Options):
    ''' Options for `HashIndexCommand`.
    '''
    hashname: str = DEFAULT_HASHNAME
    move_mode: bool = False
    ssh_exe: str = DEFAULT_SSH_EXE
    hashindex_exe: str = DEFAULT_HASHINDEX_EXE
    symlink_mode: bool = False
    relative: Optional[bool] = None

  # pylint: disable=arguments-differ
  @contextmanager
  @uses_fstags
  def run_context(self, *, fstags: FSTags, **kw):
    with fstags:
      with super().run_context(**kw):
        yield

  @uses_fstags
  def cmd_linkto(self, argv, *, fstags: FSTags):
    ''' Usage: {cmd} [-f] [-h hashname] [--mv] [-n] [-q] [-s] srcdir dstdir < hashindex
          Link files from srcdir to dstdir according the input hash index.
          -f    Force: link even if the target already exists.
          -h hashname
                Specify the hash algorithm, default: {DEFAULT_HASHNAME}
          --mv  Move: unlink the original after a successful hard link.
          -n    No action; recite planned actions.
          -q    Quiet. Do not report actions.
          -s    Symlink the source file instead of hard linking.
    '''
    options = self.options
    badopts = False
    options.popopts(
        argv,
        f='force',
        h_='hashname',
        mv='move_mode',
        n='dry_run',
        q='quiet',
        s='symlink_mode',
    )
    if not argv:
      warning("missing srcdir")
      badopts = True
    else:
      srcdir = argv.pop(0)
      if not isdirpath(srcdir):
        warning('srcdir %r: not a directory', srcdir)
        badopts = True
    if not argv:
      warning("missing dstdir")
    else:
      dstdir = argv.pop(0)
      if not isdirpath(dstdir):
        warning('dstdir %r: not a directory', dstdir)
        badopts = True
    if argv:
      warning("extra arguments: %r", argv)
      badopts = True
    if options.move_mode and options.symlink_mode:
      warning(
          "you may not specify both --mv (move mode) and -s (symlink mode)"
      )
      badopts = True
    if badopts:
      raise GetoptError('bad arguments')
    doit = options.doit
    quiet = options.quiet
    hashname = options.hashname
    move_mode = options.move_mode
    runstate = options.runstate
    symlink_mode = options.symlink_mode
    # scan the input hashcode->file-path listing
    with Pfx("scan input"):
      with run_task("scan input"):
        bad_input = False
        fspaths_by_hashcode = defaultdict(list)
        hashcode_by_fspath = {}
        for lineno, (hashcode,
                     rfspath) in enumerate(read_hashindex(sys.stdin,
                                                          hashname=hashname),
                                           1):
          runstate.raiseif()
          with Pfx("stdin:%d", lineno):
            if hashcode is None or rfspath is None:
              warning("bad hashcode or bad fspath")
              bad_input = True
              continue
            with Pfx(rfspath):
              if isabspath(rfspath):
                warning("is an absolute path")
                bad_input = True
                continue
              if rfspath in hashcode_by_fspath:
                warning("repeated mention")
                bad_input = True
                continue
            fspaths_by_hashcode[hashcode].append(rfspath)
            hashcode_by_fspath[rfspath] = hashcode
    if bad_input:
      warning("bad input data")
      return 1
    ok = True
    # scan the source tree and link according to the input
    with Pfx("scan srcdir %r", srcdir):
      with run_task(f'scan srcdir {shortpath(srcdir)}') as proxy:
        with contextif(
            not quiet,
            reconfigure_file,
            sys.stdout,
            line_buffering=True,
        ):
          for srchashcode, srcpath in hashindex(srcdir, hashname=hashname):
            rsrcpath = relpath(srcpath, srcdir)
            proxy.text = rsrcpath
            with Pfx(srcpath):
              if srchashcode is None:
                warning("no hashcode")
                continue
              rfspaths = fspaths_by_hashcode[srchashcode]
              if not rfspaths:
                warning("hashcode %s not present in the input", srchashcode)
                continue
              filed_to = []
              for rfspath in rfspaths:
                dstpath = joinpath(dstdir, rfspath)
                if existspath(dstpath):
                  if file_checksum(dstpath) != srchashcode:
                    warning(
                        "dstpath %r already exists with different hashcode",
                        dstpath
                    )
                  continue
                opname = "ln -s" if symlink_mode else "mv" if move_mode else "ln"
                if not quiet:
                  print(opname, srcpath, dstpath)
                dstdirpath = dirname(dstpath)
                if doit:
                  needdir(dstdirpath, use_makedirs=False, log=warning)
                if not quiet:
                  print(opname, srcpath, dstpath)
                if doit:
                  fstags.mv(
                      srcpath,
                      dstpath,
                      exists_ok=False,
                      symlink=symlink_mode,
                      remove=False
                  )
                  filed_to.append(dstpath)
              if filed_to and move_mode:
                pfx_call(os.remove(srcpath))
    return 0 if ok else 1

  def cmd_ls(self, argv):
    ''' Usage: {cmd} [-h hashname] [-r] [host:]path...
          Walk filesystem paths and emit a listing.
          -e ssh_exe    Specify the ssh executable.
          -h hashname   Specify the file content hash algorithm name.
          -H hashindex_exe
                        Specify the remote hashindex executable.
          -r            Emit relative paths in the listing.
                        This requires each path to be a directory.
    '''
    options = self.options
    options.relative = False
    options.popopts(
        argv,
        e_='ssh_exe',
        h_='hashname',
        H_='hashindex_exe',
        r='relative',
    )
    hashindex_exe = options.hashindex_exe
    hashname = options.hashname
    relative = options.relative
    runstate = options.runstate
    ssh_exe = options.ssh_exe
    if not argv:
      raise GetoptError("missing paths")
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
          for h, fspath in hashindex(path, hashname=hashname):
            runstate.raiseif()
            if h is not None:
              print(h, relpath(fspath, path) if relative else fspath)
        else:
          for h, fspath in read_remote_hashindex(
              rhost,
              lpath,
              hashname=hashname,
              ssh_exe=ssh_exe,
              hashindex_exe=hashindex_exe,
          ):
            runstate.raiseif()
            print(h, fspath if relative else joinpath(lpath, fspath))
    return xit

  @typechecked
  def cmd_rearrange(self, argv):
    ''' Usage: {cmd} [options...] {{[[user@]host:]refdir|-}} [[user@]rhost:]targetdir [dstdir]
          Rearrange files in targetdir based on their positions in refdir.
          Options:
            -e ssh_exe  Specify the ssh executable.
            -h hashname Specify the file content hash algorithm name.
            -H hashindex_exe
                        Specify the remote hashindex executable.
            --mv        Move mode.
            -n          No action, dry run.
            -s          Symlink mode.
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
    options.popopts(
        argv,
        e_='ssh_exe',
        h_='hashname',
        H_='hashindex_exe',
        mv='move_mode',
        n='dry_run',
        s='symlink_mode',
    )
    doit = options.doit
    hashindex_exe = options.hashindex_exe
    hashname = options.hashname
    move_mode = options.move_mode
    quiet = options.quiet
    ssh_exe = options.ssh_exe
    symlink_mode = options.symlink_mode
    if not argv:
      warning("missing refdir")
      badopts = True
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
          warning("remote \"-\" not supported")
          badopts = True
    if not argv:
      warning("missing targetdir")
      badopts = True
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
      if refhost is None:
        if refdir == '-':
          hindex = read_hashindex(sys.stdin, hashname=hashname)
        else:
          hindex = (
              (hashcode, relpath(fspath, refdir))
              for hashcode, fspath in hashindex(refdir, hashname=hashname)
          )
      else:
        hindex = read_remote_hashindex(
            refhost,
            refdir,
            hashname=hashname,
            ssh_exe=ssh_exe,
            hashindex_exe=hashindex_exe,
        )
      for hashcode, fspath in hindex:
        if hashcode is not None:
          fspaths_by_hashcode[hashcode].append(fspath)
    # rearrange the target directory.
    with run_task(f'rearrange {targetspec}'):
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
              hashname=hashname,
              doit=doit,
              move_mode=move_mode,
              symlink_mode=symlink_mode,
              quiet=quiet,
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
                ('-h', hashname),
                move_mode and '--mv',
                symlink_mode and '-s',
                '-',
                targetdir,
                dstdir,
            ],
            ssh_exe=ssh_exe,
            hashindex_exe=hashindex_exe,
            input=input_s,
            text=True,
            quiet=False,
        ).returncode
    return xit

@uses_fstags
@pfx
def file_checksum(
    fspath: str,
    hashname: str = DEFAULT_HASHNAME,
    *,
    fstags: FSTags,
) -> Union[BaseHashCode, None]:
  ''' Return the hashcode for the contents of the file at `fspath`.
      Warn and return `None` on `OSError`.
  '''
  hashcode, S = get_fstags_hashcode(fspath, hashname)
  if S_ISLNK(S.st_mode):
    # ignore symlinks
    return None
  if hashcode is None:
    hashclass = BaseHashCode.hashclass(hashname)
    with run_task(f'checksum {shortpath(fspath)}'):
      try:
        hashcode = hashclass.from_fspath(fspath)
      except OSError as e:
        warning("%s.from_fspath(%r): %s", hashclass.__name__, fspath, e)
        return None
    set_fstags_hashcode(fspath, hashcode, S, fstags=fstags)
  return hashcode

@uses_fstags
@typechecked
def get_fstags_hashcode(
    fspath: str,
    hashname: str,
    fstags: FSTags,
) -> Tuple[Optional[BaseHashCode], Optional[os.stat_result]]:
  ''' Obtain the hashcode cached in the fstags if still valid.
      Return a 2-tuple of `(hashcode,stat_result)`
      where `hashcode` is a `BaseHashCode` subclass instance is valid
      or `None` if missing or no longer valid
      and `stat_result` is the current `os.stat` result for `fspath`.
  '''
  try:
    S = os.lstat(fspath)
  except OSError as e:
    warning("stat %r: %s", fspath, e)
    return None, None
  if S_ISLNK(S.st_mode):
    # ignore symlinks
    return None, S
  if not S_ISREG(S.st_mode):
    raise ValueError("not a regular file")
  tags = fstags[fspath]
  csum = tags.subtags(f'checksum.{hashname}')
  csum_hash = csum.get('hashcode', '')
  if not csum_hash:
    return None, S
  try:
    st_size = int(csum.get('st_size', 0))
    st_mtime = int(csum.get('st_mtime', 0))
  except (TypeError, ValueError):
    return None, S
  if S.st_size != st_size or int(S.st_mtime) != st_mtime:
    # file has changed, do not return the cached hashcode
    return None, S
  hashcode = BaseHashCode.from_prefixed_hashbytes_hex(csum_hash)
  if hashcode.hashname != hashname:
    warning("ignoring unexpected hashname %r", hashcode.hashname)
    return None, S
  return hashcode, S

##@trace
@uses_fstags
@typechecked
def set_fstags_hashcode(
    fspath: str,
    hashcode,
    S: os.stat_result,
    fstags: FSTags,
):
  ''' Record `hashcode` against `fspath`.
  '''
  tags = fstags[fspath]
  csum = tags.subtags(f'checksum.{hashcode.hashname}')
  csum.hashcode = str(hashcode)
  csum.st_size = S.st_size
  csum.st_mtime = S.st_mtime

@uses_fstags
def hashindex(fspath, *, hashname: str, fstags: FSTags):
  ''' Generator yielding `(hashcode,filepath)` 2-tuples
      for the files in `fspath`, which may be a file or directory path.
      Note that it yields `(None,filepath)` for files which cannot be accessed.
  '''
  if isfilepath(fspath):
    h = file_checksum(fspath)
    yield h, fspath
  elif isdirpath(fspath):
    for filepath in dir_filepaths(fspath):
      h = file_checksum(filepath, hashname=hashname)
      yield h, filepath
  else:
    warning("hashindex(%r): neither file nor directory", fspath)

def read_hashindex(f, start=1, *, hashname: str):
  ''' A generator which reads line from the file `f`
      and yields `(hashcode,fspath)` 2-tuples.
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
def read_remote_hashindex(
    rhost: str,
    rdirpath: str,
    *,
    hashname: str,
    ssh_exe=None,
    hashindex_exe=None,
    check=True,
):
  ''' A generator which reads a hashindex of a remote directory,
      This runs: `hashindex ls -h hashname -r rdirpath` on the remote host.
      It yields `(hashcode,fspath)` 2-tuples.

      Parameters:
      * `rhost`: the remote host, or `user@host`
      * `rdirpath`: the remote directory path
      * `hashname`: the file content hash algorithm name
      * `ssh_exe`: the `ssh` executable,
        default `DEFAULT_SSH_EXE`: `{DEFAULT_SSH_EXE!r}`
      * `hashindex_exe`: the remote `hashindex` executable,
        default `DEFAULT_HASHINDEX_EXE`: `{DEFAULT_HASHINDEX_EXE!r}`
      * `check`: whether to check that the remote command has a `0` return code,
        default `True`
  '''
  if ssh_exe is None:
    ssh_exe = DEFAULT_SSH_EXE
  if hashindex_exe is None:
    hashindex_exe = DEFAULT_HASHINDEX_EXE
  hashindex_cmd = shlex.join(
      prep_argv(
          hashindex_exe,
          'ls',
          ('-h', hashname),
          '-r',
          localpath(rdirpath),
      )
  )
  remote_argv = [ssh_exe, rhost, hashindex_cmd]
  remote = pipefrom(remote_argv)
  yield from read_hashindex(remote.stdout, hashname=hashname)
  if check:
    remote.wait()
    if remote.returncode != 0:
      raise CalledProcessError(remote.returncode, remote_argv)

@fmtdoc
def run_remote_hashindex(
    rhost: str,
    argv,
    *,
    ssh_exe=None,
    hashindex_exe=None,
    check: bool = True,
    doit: bool = True,
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
      * `ssh_exe`: the `ssh` executable,
        default `DEFAULT_SSH_EXE`: `{DEFAULT_SSH_EXE!r}`
      * `hashindex_exe`: the remote `hashindex` executable,
        default `DEFAULT_HASHINDEX_EXE`: `{DEFAULT_HASHINDEX_EXE!r}`
      * `check`: whether to check that the remote command has a `0` return code,
        default `True`
      * `doit`: whether to actually run the command, default `True`
      Other keyword parameters are passed therough to `cs.psutils.run`.
  '''
  if ssh_exe is None:
    ssh_exe = DEFAULT_SSH_EXE
  if hashindex_exe is None:
    hashindex_exe = DEFAULT_HASHINDEX_EXE
  hashindex_cmd = shlex.join(prep_argv(
      hashindex_exe,
      *argv,
  ))
  remote_argv = [ssh_exe, rhost, hashindex_cmd]
  with without():
    return run(remote_argv, check=check, doit=doit, **subp_options)

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
      based on the hashcodes keying `rfspaths_by_hashcode`.
  '''
  yield from paths_remap(
      dir_filepaths(srcdirpath), fspaths_by_hashcode, hashname=hashname
  )

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
    quiet: bool = False,
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
      * `quiet`: default `False`; if true do not print
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
        assert is_valid_rpath(rsrcpath), (
            "rsrcpath:%r is not a clean subpath" % (rsrcpath,)
        )
        proxy.text = rsrcpath
        for rdstpath in rfspaths:
          assert is_valid_rpath(rdstpath), (
              "rdstpath:%r is not a clean subpath" % (rdstpath,)
          )
          if rsrcpath == rdstpath:
            continue
          dstpath = joinpath(dstdirpath, rdstpath)
          if not quiet:
            print(opname, shortpath(srcpath), shortpath(dstpath))
          if doit:
            needdir(dirname(dstpath), use_makedirs=True, log=warning)
            merge(
                srcpath,
                dstpath,
                opname=opname,
                hashname=hashname,
                move_mode=False,  # we do our own remove below
                symlink_mode=symlink_mode,
                fstags=fstags,
                doit=doit,
                quiet=True,  # we do our own print above
            )
            if move_mode and rsrcpath not in rfspaths:
              to_remove.add(srcpath)
    # purge the srcpaths last because we might want them multiple
    # times during the main loop (files with the same hashcode)
    if doit and to_remove:
      for srcpath in sorted(to_remove):
        pfx_call(os.remove, srcpath)

@pfx
@uses_fstags
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
    quiet=False,
    fstags: FSTags,
):
  ''' Merge `srcpath` to `dstpath`.

      If `dstpath` does not exist, move/link/symlink `srcpath` to `dstpath`.
      Otherwise checksum their contents and raise `FileExistsError` if they differ.
  '''
  if opname is None:
    opname = "ln -s" if symlink_mode else "mv" if move_mode else "ln"
  if not quiet:
    print(opname, shortpath(srcpath), shortpath(dstpath))
  if not doit:
    return
  if dstpath == srcpath:
    return
  if symlink_mode:
    # check for existing symlink
    sympath = abspath(srcpath)
    try:
      dstsympath = os.readlink(dstpath)
    except FileNotFoundError:
      pass
    else:
      if dstsympath == sympath:
        # identical symlinks, just update the tags
        fstags[dstpath].update(fstags[srcpath])
        return
  elif existspath(dstpath):
    if (samefile(srcpath, dstpath)
        or (file_checksum(dstpath, hashname=hashname) == file_checksum(
            srcpath, hashname=hashname))):
      fstags[dstpath].update(fstags[srcpath])
      if move_mode and realpath(srcpath) != realpath(dstpath):
        pfx_call(os.remove, srcpath)
      return
    raise FileExistsError(
        f'dstpath {dstpath!r} already exists with different hashcode'
    )
  pfx_call(fstags.mv, srcpath, dstpath, symlink=symlink_mode, remove=move_mode)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
