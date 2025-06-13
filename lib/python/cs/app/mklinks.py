#!/usr/bin/env python3
#
# Recode of mklinks in Python, partly for the exercise and partly to
# improve the algorithm.
#       - Cameron Simpson <cs@cskk.id.au> 21may2006
#
# 27dec2017: recode again: prefer younger files over older files, cleaner logic.
#

r'''
mklinks: tool for finding and hardlinking identical files

Mklinks walks supplied paths looking for files with the same content,
based on a cryptographic checksum of their content. It hardlinks
all such files found, keeping the oldest version.

Unlike some rather naive tools out there, mklinks only compares
files with other files of the same size, and is hardlink aware;
a partially hardlinked tree is processed efficiently and correctly.
'''

from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from getopt import GetoptError
import os
from os.path import isdir, join as joinpath, relpath
from stat import S_ISREG
import sys

from typeguard import typechecked

from cs.cmdutils import BaseCommand, popopts, uses_cmd_options
from cs.deco import Promotable
from cs.fileutils import shortpath
from cs.fs import HasFSPath
from cs.hashindex import HASHNAME_DEFAULT, file_checksum, merge
from cs.logutils import warning
from cs.pfx import Pfx, pfx, pfx_method
from cs.progress import progressbar
from cs.resources import RunState, uses_runstate
from cs.seq import first
from cs.upd import run_task  # pylint: disable=redefined-builtin

__version__ = '20250530-post'

DISTINFO = {
    'description':
    "Tool for finding and hardlinking identical files.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.cmdutils>=20210404',
        'cs.deco',
        'cs.fileutils>=20200914',
        'cs.fs',
        'cs.hashindex',
        'cs.logutils',
        'cs.pfx',
        'cs.progress>=20200718.3',
        'cs.resources',
        'cs.seq',
        'cs.upd>=20200914',
    ],
    'entry_points': {
        'console_scripts': {
            'mklinks': 'cs.app.mklinks:main'
        },
    },
}

def main(argv=None):
  ''' CLI for `mklinks`.
  '''
  return MKLinksCommand(argv).run()

class MKLinksCommand(BaseCommand):
  ''' Hard link files with identical contents.
  '''

  @dataclass
  class Options(BaseCommand.Options):
    ''' Options for `MrgCommand`.
    '''
    hashname: str = HASHNAME_DEFAULT

  @popopts
  def main(self, argv):
    ''' Usage: {cmd} paths...
          Hard link files with identical contents.
    '''
    if not argv:
      raise GetoptError("missing paths")
    options = self.options
    hashname = options.hashname
    runstate = options.runstate
    xit = 0
    linker = Linker()
    with run_task("scan") as step:
      # scan the supplied paths
      for path in argv:
        runstate.raiseif()
        with step.extend_prefix(f' {path}'):
          with Pfx(path):
            try:
              linker.scan(path)
            except OSError as e:
              warning("scan fails: %s", e)
              xit = 1
    with run_task("merge"):
      linker.merge(dry_run=options.dry_run)
    return xit

@dataclass
class Inode(HasFSPath, Promotable):
  ''' Information about a particular inode.
  '''
  dev: int
  ino: int
  mtime: float
  size: int
  hashname: str = HASHNAME_DEFAULT
  paths: set = field(default_factory=set)

  def __eq__(self, other):
    return self.key == other.key

  def __hash__(self):
    return hash(self.key)

  @cached_property
  def checksum(self):
    ''' The content checksum.
    '''
    return file_checksum(self.fspath)

  @cached_property
  def fspath(self):
    ''' The first filesystem path in `.paths`, somewhat arbitrary.
    '''
    return first(self.paths)

  @property
  def key(self):
    ''' A `(ino,dev)` 2-tuple.
    '''
    return self.ino, self.dev

  @classmethod
  def stat_key(cls, fspath: str):
    ''' Compute the `(ino,dev)` 2-tuple from `os.stat(fspath)`.
    '''
    S = os.lstat(fspath)
    if not S_ISREG(S.st_mode):
      raise ValueError(
          f'{cls.__name__}.stat_key({fspath=}): not a regular file'
      )
    return S.st_ino, S.st_dev

  def samefs(self, other):
    ''' Test whether 2 `Inode`'s are on the same filesystem (same `.dev` values).
    '''
    return self.dev == other.dev

  @classmethod
  @uses_cmd_options(hashname=HASHNAME_DEFAULT)
  def from_str(cls, fspath: str, *, hashname: str):
    ''' Promote a filesystem path to an `Inode`.
    '''
    S = os.lstat(fspath)
    if not S_ISREG(S.st_mode):
      raise ValueError(
          f'{cls.__name__}.from_str({fspath=}): not a regular file'
      )
    return cls(
        dev=S.st_dev,
        ino=S.st_ino,
        mtime=S.st_mtime,
        size=S.st_size,
        hashname=hashname,
        paths={fspath},
    )

  def same_dev(self, other):
    ''' Test whether two `Inode`s are on the same filesystem.
    '''
    return self.dev == other.dev

  def same_file(self, other):
    ''' Test whether two `Inode`s refer to the same file.
    '''
    return self.key == other.key  # pylint: disable=comparison-with-callable

  @uses_runstate
  @typechecked
  def assimilate(self, other: "Inode", *, dry_run=False, runstate: RunState):
    ''' Link our primary path to all the paths from `other`. Return success.
    '''
    if self is other or self == other:
      raise ValueError(f'{self=} refers to the same inode as {other=}')
    if self.hashname != other.hashname:
      raise ValueError(f'{self.hashname=} != {other.hashname=}')
    ok = True
    opaths = other.paths
    with run_task("assimilate %d:%d -> %d:%d %s" % (
        other.dev,
        other.ino,
        self.dev,
        self.ino,
        self.shortpath,
    )) as proxy:
      for ofspath in sorted(other.paths):
        runstate.raiseif()
        proxy.text = shortpath(ofspath)
        if merge(ofspath, self.fspath, doit=not dry_run,
                 hashname=self.hashname):
          other.paths.remove(ofspath)
    return ok

@dataclass
class Linker:
  ''' The class which links files with identical content.
  '''
  # group the Inodes by their sizes
  inodes_by_size: dict = field(default_factory=lambda: defaultdict(set))
  # map Inode stat keys to Inodes
  inodes_by_key: dict = field(default_factory=dict)
  min_size: int = 512

  def __repr__(self):
    return f'<{self.__class__.__name__}>'

  @pfx_method
  @uses_runstate
  def scan(self, path, *, runstate: RunState):
    ''' Scan the file tree.
    '''
    with run_task(f'scan {path}: ') as proxy:
      if isdir(path):
        for dirpath, dirnames, filenames in os.walk(path):
          runstate.raiseif()
          proxy(f'sweep {relpath(dirpath, path)}')
          for filename in progressbar(
              sorted(filenames),
              label=relpath(dirpath, path),
          ):
            runstate.raiseif()
            filepath = joinpath(dirpath, filename)
            try:
              self.addpath(filepath)
            except ValueError as e:
              warning("%s: %s", filepath, e)
          dirnames[:] = sorted(dirnames)
      else:
        self.addpath(path)

  @pfx
  def addpath(self, path):
    ''' Add a new path to the data structures.
    '''
    key = Inode.stat_key(path)
    try:
      inode = self.inodes_by_key[key]
    except KeyError:
      inode = self.inodes_by_key[key] = Inode.from_str(path)
      assert inode.key == key, f'{inode.key=} != {Inode.stat_key(path)=}'
      self.inodes_by_size[inode.size].add(inode)

  @pfx_method
  @uses_runstate
  def merge(self, *, dry_run=False, runstate: RunState):
    ''' Merge files with equivalent content.
    '''
    # process Inode groups by size, largest to smallest
    with run_task('merge') as proxy:
      for size, inodes in progressbar(
          sorted(self.inodes_by_size.items(), reverse=True),
          'merge by size',
      ):
        if len(inodes) < 2:
          continue
        if size < self.min_size:
          continue
        runstate.raiseif()
        with proxy.extend_prefix(f' size {size}'):
          inodes_by_hashcode = defaultdict(list)
          for inode in inodes:
            runstate.raiseif()
            inodes_by_hashcode[inode.checksum].append(inode)
          for same_inodes in inodes_by_hashcode.values():
            if len(same_inodes) < 2:
              continue
            # order by mtime (newest first) and then path
            inode0, *oinodes = sorted(
                same_inodes,
                key=lambda inode: (-inode.mtime, inode.fspath),
            )
            for oinode in oinodes:
              inode0.assimilate(oinode, dry_run=dry_run)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
