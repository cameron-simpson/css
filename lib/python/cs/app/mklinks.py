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
all such files found, keeping the newest version.

Unlike some rather naive tools out there, mklinks only compares
files with other files of the same size, and is hardlink aware - a
partially hardlinked tree is processed efficiently and correctly.
'''

from collections import defaultdict
from functools import cached_property
from getopt import GetoptError
import os
from os.path import dirname, isdir, join as joinpath, relpath
from stat import S_ISREG
import sys
from tempfile import NamedTemporaryFile

from cs.cmdutils import BaseCommand, popopts
from cs.fileutils import common_path_prefix, shortpath
from cs.hashindex import file_checksum
from cs.logutils import status, warning, error
from cs.progress import progressbar
from cs.pfx import Pfx, pfx_method
from cs.resources import RunState, uses_runstate
from cs.upd import UpdProxy, print, run_task  # pylint: disable=redefined-builtin

__version__ = '20221228-post'

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
        'cs.fileutils>=20200914',
        'cs.hashindex',
        'cs.logutils',
        'cs.pfx',
        'cs.progress>=20200718.3',
        'cs.units',
        'cs.upd>=20200914',
    ],
    'entry_points': {
        'console_scripts': {
            'mklinks': 'cs.app.mklinks:main'
        },
    },
}

def main(argv=None):
  ''' Main command line programme.
  '''
  return MKLinksCmd(argv).run()

class MKLinksCmd(BaseCommand):
  ''' Hard link files with identical contents.
  '''

  @popopts
  def main(self, argv):
    ''' Usage: mklinks [-n] paths...
          Hard link files with identical contents.
    '''
    if not argv:
      raise GetoptError("missing paths")
    options = self.options
    runstate = options.runstate
    xit = 0
    linker = Linker()
    with run_task("scan") as step:
      # scan the supplied paths
      for path in argv:
        runstate.raiseif()
        step("scan " + path + ' ...')
        with Pfx(path):
          try:
            linker.scan(path)
          except OSError as e:
            warning("scan fails: %s", e)
            xit = 1
    with run_task("merge"):
      linker.merge(dry_run=options.dry_run)
    return xit

class FileInfo(object):
  ''' Information about a particular inode.
  '''

  # pylint: disable=too-many-arguments
  def __init__(self, dev, ino, size, mtime, paths=()):
    self.dev = dev
    self.ino = ino
    self.size = size
    self.mtime = mtime
    self.paths = set(paths)
    self._checksum = None

  def __str__(self):
    return (
        "%d:%d:size=%d:mtime=%d" % (self.dev, self.ino, self.size, self.mtime)
    )

  def __repr__(self):
    return "FileInfo(%d,%d,%d,%d,paths=%r)" \
           % (self.dev, self.ino, self.size, self.mtime, self.paths)

  @staticmethod
  def stat_key(S):
    ''' Compute the key `(dev,ino)` from the stat object `S`.
    '''
    return S.st_dev, S.st_ino

  @property
  def key(self):
    ''' The key for this file: `(dev,ino)`.
    '''
    return self.dev, self.ino

  @property
  def path(self):
    ''' The primary path for this file, or `None` if we have no paths.
    '''
    return sorted(self.paths)[0] if self.paths else None

  @cached_property
  def checksum(self):
    ''' Checksum the file contents, used as a proxy for comparing the actual content.
    '''
    return file_checksum(self.path)

  def same_dev(self, other):
    ''' Test whether two FileInfos are on the same filesystem.
    '''
    return self.dev == other.dev

  def same_file(self, other):
    ''' Test whether two FileInfos refer to the same file.
    '''
    return self.key == other.key  # pylint: disable=comparison-with-callable

  @uses_runstate
  def assimilate(self, other, *, dry_run=False, runstate: RunState):
    ''' Link our primary path to all the paths from `other`. Return success.
    '''
    ok = True
    path = self.path
    opaths = other.paths
    pathprefix = common_path_prefix(path, *opaths)
    vpathprefix = shortpath(pathprefix)
    pathsuffix = path[len(pathprefix):]  # pylint: disable=unsubscriptable-object
    with UpdProxy() as proxy:
      proxy(
          "%s%s <= %r", vpathprefix, pathsuffix,
          list(map(lambda opath: opath[len(pathprefix):], sorted(opaths)))
      )
      with Pfx(path):
        if self is other or self.same_file(other):
          # already assimilated
          return ok
        assert self.same_dev(other)
        for opath in sorted(opaths):
          if runstate.cancelled:
            break
          with Pfx(opath):
            if opath in self.paths:
              warning("already assimilated")
              continue
            if vpathprefix:
              print(
                  "%s: %s => %s" %
                  (vpathprefix, opath[len(pathprefix):], pathsuffix)
              )
            else:
              print("%s => %s" % (opath[len(pathprefix):], pathsuffix))
            if dry_run:
              continue
            odir = dirname(opath)
            with NamedTemporaryFile(dir=odir) as tfp:
              with Pfx("unlink(%s)", tfp.name):
                os.unlink(tfp.name)
              with Pfx("rename(%s, %s)", opath, tfp.name):
                os.rename(opath, tfp.name)
              with Pfx("link(%s, %s)", path, opath):
                try:
                  os.link(path, opath)
                except OSError as e:
                  error("%s", e)
                  ok = False
                  # try to restore the previous file
                  with Pfx("restore: link(%r, %r)", tfp.name, opath):
                    os.link(tfp.name, opath)
                else:
                  self.paths.add(opath)
                  opaths.remove(opath)
    return ok

class Linker:
  ''' The class which links files with identical content.
  '''

  def __init__(self, min_size=512):
    self.sizemap = defaultdict(dict)  # file_size => FileInfo.key => FileInfo
    self.keymap = {}  # FileInfo.key => FileInfo
    self.min_size = min_size

  @pfx_method
  @uses_runstate
  def scan(self, path, *, runstate: RunState):
    ''' Scan the file tree.
    '''
    with run_task(f'scan {path}: ') as proxy:
      if isdir(path):
        for dirpath, dirnames, filenames in os.walk(path):
          runstate.raiseif()
          proxy("sweep " + relpath(dirpath, path))
          for filename in progressbar(
              sorted(filenames),
              label=relpath(dirpath, path),
          ):
            runstate.raiseif()
            filepath = joinpath(dirpath, filename)
            self.addpath(filepath)
          dirnames[:] = sorted(dirnames)
      else:
        self.addpath(path)

  def addpath(self, path):
    ''' Add a new path to the data structures.
    '''
    with Pfx("addpath(%r)", path):
      with Pfx("lstat"):
        S = os.lstat(path)
      if not S_ISREG(S.st_mode):
        return
      if S.st_size < self.min_size:
        return
      key = FileInfo.stat_key(S)
      FI = self.keymap.get(key)
      if FI:
        assert FI.key == key
        FI.paths.add(path)
      else:
        FI = FileInfo(S.st_dev, S.st_ino, S.st_size, S.st_mtime, (path,))
        assert FI.key == key  # pylint: disable=comparison-with-callable
        self.keymap[key] = FI
        assert key not in self.sizemap[S.st_size]
        self.sizemap[S.st_size][key] = FI

  @pfx_method
  @uses_runstate
  def merge(self, *, dry_run=False, runstate: RunState):
    ''' Merge files with equivalent content.
    '''
    # process FileInfo groups by size, largest to smallest
    with run_task('merge ... ') as proxy:
      for _, FImap in sorted(self.sizemap.items(), reverse=True):
        runstate.raiseif()
        # order FileInfos by mtime (newest first) and then path
        FIs = sorted(FImap.values(), key=lambda FI: (-FI.mtime, FI.path))
        if len(FIs) < 2:
          continue
        size = FIs[0].size
        with proxy.extend_prefix(f'size {size} '):
          for i, FI in enumerate(progressbar(FIs, f'size {size}')):
            runstate.raiseif()
            # skip FileInfos with no paths
            # this happens when a FileInfo has been assimilated
            if not FI.paths:
              ##warning("SKIP, no paths")
              continue
            for FI2 in FIs[i + 1:]:
              runstate.raiseif()
              status(FI2.path)
              assert FI.size == FI2.size
              assert FI.mtime >= FI2.mtime
              assert not FI.same_file(FI2)
              if not FI.same_dev(FI2):
                # different filesystems, cannot link
                continue
              if FI.checksum != FI2.checksum:
                # different content, skip
                continue
              # FI2 is the younger, keep it
              FI.assimilate(FI2, dry_run=dry_run)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
