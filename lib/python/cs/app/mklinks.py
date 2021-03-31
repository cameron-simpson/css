#!/usr/bin/env python
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

from __future__ import print_function
from collections import defaultdict
from getopt import GetoptError
from hashlib import sha1 as hashfunc
import os
from os.path import dirname, isdir, isfile, join as joinpath, relpath
from stat import S_ISREG
import sys
from tempfile import NamedTemporaryFile
from cs.cmdutils import BaseCommand
from cs.fileutils import read_from, common_path_prefix, shortpath
from cs.logutils import status, warning, error
from cs.progress import progressbar
from cs.pfx import Pfx, pfx_method
from cs.py.func import prop
from cs.units import BINARY_BYTES_SCALE
from cs.upd import UpdProxy, Upd, print  # pylint: disable=redefined-builtin

__version__ = '20210306-post'

DISTINFO = {
    'description':
    "Tool for finding and hardlinking identical files.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.cmdutils',
        'cs.fileutils>=20200914',
        'cs.logutils',
        'cs.pfx',
        'cs.progress>=20200718.3',
        'cs.py.func',
        'cs.units',
        'cs.upd>=20200914',
    ],
    'entry_points': {
        'console_scripts': ['mklinks = cs.app.mklinks:main'],
    },
}

def main(argv=None):
  ''' Main command line programme.
  '''
  return MKLinksCmd(argv).run()

class MKLinksCmd(BaseCommand):
  ''' Main programme command line class.
  '''

  USAGE_FORMAT = r'''Usage: {cmd} paths...
          Hard link files with identical contents.
          -n    No action. Report proposed actions.'''

  GETOPT_SPEC = 'n'

  def apply_defaults(self):
    ''' Set up the default values in `options`.
    '''
    self.options.no_action = False

  def apply_opts(self, opts):
    ''' Apply command line options.
    '''
    for opt, _ in opts:
      with Pfx(opt):
        if opt == '-n':
          self.options.no_action = True
        else:
          raise RuntimeError("unhandled option")

  def main(self, argv):
    ''' Usage: mklinks [-n] paths...
          Hard link files with identical contents.
          -n    No action. Report proposed actions.
    '''
    if not argv:
      raise GetoptError("missing paths")
    options = self.options
    linker = Linker()
    with options.upd.insert(1) as step:
      # scan the supplied paths
      for path in argv:
        step("scan " + path + ' ...')
        with Pfx(path):
          linker.scan(path)
      step("merge ...")
      linker.merge(no_action=options.no_action)

class FileInfo(object):
  ''' Information about a particular inode.
  '''

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

  @prop
  def key(self):
    ''' The key for this file: `(dev,ino)`.
    '''
    return self.dev, self.ino

  @prop
  def path(self):
    ''' The primary path for this file, or `None` if we have no paths.
    '''
    return sorted(self.paths)[0] if self.paths else None

  @prop
  def checksum(self):
    ''' Checksum the file contents, used as a proxy for comparing the actual content.
    '''
    csum = self._checksum
    if csum is None:
      path = self.path
      U = Upd()
      pathspace = U.columns - 64
      label = "scan " + (
          path if len(path) < pathspace else '...' + path[-(pathspace - 3):]
      )
      with Pfx("checksum %r", path):
        csum = hashfunc()
        with open(path, 'rb') as fp:
          length = os.fstat(fp.fileno()).st_size
          read_len = 0
          for data in progressbar(
              read_from(fp),
              label=label,
              total=length,
              units_scale=BINARY_BYTES_SCALE,
              itemlenfunc=len,
              update_frequency=128,
              upd=U,
          ):
            csum.update(data)
            read_len += len(data)
          assert read_len == self.size
      csum = csum.digest()
      self._checksum = csum
    return csum

  def same_dev(self, other):
    ''' Test whether two FileInfos are on the same filesystem.
    '''
    return self.dev == other.dev

  def same_file(self, other):
    ''' Test whether two FileInfos refer to the same file.
    '''
    return self.key == other.key

  def assimilate(self, other, no_action=False):
    ''' Link our primary path to all the paths from `other`. Return success.
    '''
    ok = True
    path = self.path
    opaths = other.paths
    pathprefix = common_path_prefix(path, *opaths)
    vpathprefix = shortpath(pathprefix)
    pathsuffix = path[len(pathprefix):]
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
            if no_action:
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

class Linker(object):
  ''' The class which links files with identical content.
  '''

  def __init__(self, min_size=512):
    self.sizemap = defaultdict(dict)  # file_size => FileInfo.key => FileInfo
    self.keymap = {}  # FileInfo.key => FileInfo
    self.min_size = min_size

  @pfx_method
  def scan(self, path):
    ''' Scan the file tree.
    '''
    with UpdProxy() as proxy:
      proxy.prefix = "scan %s: " % (path)
      if isdir(path):
        for dirpath, dirnames, filenames in os.walk(path):
          proxy("sweep " + relpath(dirpath, path))
          for filename in progressbar(
              sorted(filenames),
              label=relpath(dirpath, path),
              update_frequency=32,
          ):
            filepath = joinpath(dirpath, filename)
            self.addpath(filepath)
          dirnames[:] = sorted(dirnames)
      else:
        self.addpath(path)

  def addpath(self, path):
    ''' Add a new path to the data structures.
    '''
    with Pfx(path):
      with Pfx("lstat"):
        S = os.lstat(path)
      if not S_ISREG(S.st_mode):
        return
      if S.st_size < self.min_size:
        return
      key = FileInfo.stat_key(S)
      FI = self.keymap.get(key)
      if FI:
        FI.paths.add(path)
      else:
        FI = FileInfo(S.st_dev, S.st_ino, S.st_size, S.st_mtime, (path,))
        self.keymap[key] = FI
        self.sizemap[S.st_size][key] = FI

  @pfx_method
  def merge(self, no_action=False):
    ''' Merge files with equivalent content.
    '''
    # process FileInfo groups by size, largest to smallest
    for _, FImap in sorted(self.sizemap.items(), reverse=True):
      # order FileInfos by mtime (newest first) and then path
      FIs = sorted(FImap.values(), key=lambda FI: (-FI.mtime, FI.path))
      size = FIs[0].size
      with UpdProxy(text="merge size %d " % (size,)) as proxy:
        for i, FI in enumerate(FIs):
          # skip FileInfos with no paths
          # this happens when a FileInfo has been assimilated
          if not FI.paths:
            ##warning("SKIP, no paths")
            continue
          for FI2 in FIs[i + 1:]:
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
            FI.assimilate(FI2, no_action=no_action)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
