#!/usr/bin/env python3

''' Emit a listing of file checksums or read such a listing and
    construct a matching directory tree.
    - Cameron Simpson <cs@cskk.id.au> 24jan2024
'''

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from getopt import GetoptError
import os
from os.path import (
    basename,
    dirname,
    exists as existspath,
    isabs as isabspath,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    realpath,
    relpath,
    samefile,
)
import shlex
from stat import S_ISREG
import sys
from typing import Iterable, List, Mapping, Optional, Tuple, Union

from icontract import require
from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.fs import is_valid_rpath, needdir, shortpath
from cs.fstags import FSTags, uses_fstags
from cs.hashutils import BaseHashCode
from cs.lex import split_remote_path
from cs.logutils import warning
from cs.pfx import Pfx, pfx, pfx_call
from cs.psutils import prep_argv, pipefrom, run
from cs.resources import RunState, uses_runstate
from cs.upd import Upd, uses_upd, print, run_task  # pylint: disable=redefined-builtin

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
    ssh_exe: str = 'ssh'
    symlink_mode: bool = False

  @contextmanager
  @uses_fstags
  def run_context(self, *, fstags: FSTags, **kw):
    with fstags:
      with super().run_context(**kw):
        yield

  def cmd_rearrange(
      self,
      argv,
      *,
      hashindex_exe=DEFAULT_HASHINDEX_EXE,
      ssh_exe=DEFAULT_SSH_EXE,
  ):
    ''' Usage: {cmd} [-e sshcmd] [-h hashname] [--mv] [-n] [-s] {{refdir|-}} [[user@]rhost:]targetdir
          Rearrange files in targetdir based on their positios in
          refdir.
    '''
    options = self.options
    badopts = False
    options.popopts(
        argv,
        e='ssh_exe',
        h_='hashname',
        mv='move_mode',
        n='dry_run',
        s='symlink_mode',
    )
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
          warning("remote - not supported")
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
      warning("extra arguments: %r", argv)
      badopts = True
    if badopts:
      raise GetoptError('bad arguments')
    doit = options.doit
    quiet = options.quiet
    hashname = options.hashname
    move_mode = options.move_mode
    symlink_mode = options.symlink_mode
    # scan the reference directory
    fspaths_by_hashcode = defaultdict(list)
    xit = 0
    with run_task(f'hashindex {refspec}'):
      if refhost is None:
        remote = None
        if refdir == '-':
          hindex = read_hashindex(sys.stdin, hashname=hashname)
        else:
          hindex = (
              (hashcode, relpath(fspath, refdir))
              for hashcode, fspath in hashindex(refdir, hashname=hashname)
          )
      else:
        hashindex_cmd = shlex.join(
            prep_argv(
                hashindex_exe,
                'ls',
                ('-h', hashname),
                '-r',
                refdir,
            )
        )
        remote = pipefrom([ssh_exe, refhost, hashindex_cmd])
        hindex = read_hashindex(remote.stdout, hashname=hashname)
      for hashcode, fspath in hindex:
        if hashcode is not None:
          fspaths_by_hashcode[hashcode].append(fspath)
      if remote is not None:
        xit = remote.returncode
    # rearrange the target directory.
    with run_task(f'rearrange {targetspec}'):
      if targethost is None:
        rearrange(
            targetdir,
            fspaths_by_hashcode,
            hashname=hashname,
            doit=doit,
            move_mode=move_mode,
            symlink_mode=symlink_mode,
            quiet=quiet,
        )
      else:
        hashindex_cmd = shlex.join(
            prep_argv(
                hashindex_exe,
                'rearrange',
                not doit and '-n',
                ('-h', hashname),
                '-',
                targetdir,
                move_mode and '--mv',
                symlink_mode and '-s',
                '-',
                targetdir,
            )
        )
        # prepare the remote input
        reflines = []
        for hashcode, fspaths in fspaths_by_hashcode.items():
          for fspath in fspaths:
            reflines.append(f'{hashcode} {fspath}\n')
        input_s = "".join(reflines)
        xit = run(
            [ssh_exe, targethost, hashindex_cmd],
            input=input_s,
            quiet=False,
        ).returncode
    return xit

  @uses_fstags
  @uses_upd
  def cmd_linkto(self, argv, *, fstags: FSTags, upd: Upd):
    ''' Usage: {cmd} [-f] [-h hashname] [--mv] [-n] [-q] [-s] srcdir dstdir < hashindex
          Read a hashindex with relative paths from the input
          and link files from srcdir to dstdir according the source hash.
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
        for hashcode, rfspath in read_hashindex(sys.stdin, hashname=hashname):
          runstate.raiseif()
          if hashcode is None or rfspath is None:
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
        for dirpath, dirnames, filenames in os.walk(srcdir):
          runstate.raiseif()
          dirnames[:] = sorted(dirnames)
          for filename in sorted(filenames):
            runstate.raiseif()
            if filename.startswith('.'
                                   ) or filename == fstags.tagsfile_basename:
              continue
            srcpath = joinpath(dirpath, filename)
            rsrcpath = relpath(srcpath, srcdir)
            proxy.text = rsrcpath
            with Pfx(srcpath):
              srchashcode = file_checksum(srcpath, hashname=hashname)
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
                quiet or print(opname, srcpath, dstpath)
                dstdirpath = dirname(dstpath)
                if doit:
                  needdir(dstdirpath, use_makedirs=False, log=warning)
                quiet or print(opname, srcpath, dstpath)
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
    ''' Usage: {cmd} [-h hashname] [-r] paths...
          Walk the filesystem paths and emit a listing.
          -h hahsname   Specify the file content hash alogrith name.
          -r            Emit relative paths in the listing.
                        This requires each path to be a directory.
    '''
    options = self.options
    options.relative = False
    options.popopts(
        argv,
        h_='hashname',
        r='relative',
    )
    hashname = options.hashname
    relative = options.relative
    runstate = options.runstate
    if not argv:
      raise GetoptError("missing paths")
    xit = 0
    for path in argv:
      with Pfx(path):
        runstate.raiseif()
        if relative and not isdirpath(path):
          warning("not a directory and -r (relative) specified")
          xit = 1
          continue
        for h, fspath in hashindex(path, hashname=hashname):
          runstate.raiseif()
          if h is None:
            xit = 1
          else:
            print(h, relpath(fspath, path) if relative else fspath)
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
    S = os.stat(fspath)
  except OSError as e:
    warning("stat %r: %s", fspath, e)
    return None, None
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
@uses_runstate
def hashindex(fspath, *, hashname: str, runstate: RunState, fstags: FSTags):
  ''' Generator yielding `(hashcode,filepath)` 2-tuples
      for the files in `fspath`, which may be a file or directory path.
      Note that it yields `(None,filepath)` for files which cannot be accessed.
  '''
  if isfilepath(fspath):
    h = file_checksum(fspath)
    yield h, fspath
  elif isdirpath(fspath):
    for dirpath, dirnames, filenames in os.walk(fspath):
      runstate.raiseif()
      dirnames[:] = sorted(dirnames)
      for filename in sorted(filenames):
        runstate.raiseif()
        if filename.startswith('.') or filename == fstags.tagsfile_basename:
          continue
        filepath = joinpath(dirpath, filename)
        h = file_checksum(filepath, hashname=hashname)
        yield h, filepath
  else:
    warning("hashindex(%r): neither file nor directory")

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

def dir_filepaths(dirpath: str):
  ''' Generator yielding the filesystem paths of the files in `dirpath`.
  '''
  for subdirpath, dirnames, filenames in os.walk(dirpath):
    dirnames[:] = sorted(dirnames)
    for filename in sorted(filenames):
      yield joinpath(subdirpath, filename)

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
def rearrange(
    srcdirpath: str,
    rfspaths_by_hashcode,
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
  '''
  with run_task(f'rearrange {shortpath(srcdirpath)}') as proxy:
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
          dstpath = joinpath(srcdirpath, rdstpath)
          if not quiet:
            print(opname, shortpath(srcpath), shortpath(dstpath))
          if doit:
            merge(
                srcpath,
                dstpath,
                opname=opname,
                hashname=hashname,
                move_mode=False,
                symlink_mode=symlink_mode,
                fstags=fstags,
                doit=doit,
                quiet=True,  # we do our own print above
            )
        if move_mode and rsrcpath not in rfspaths:
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
  if existspath(dstpath):
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
