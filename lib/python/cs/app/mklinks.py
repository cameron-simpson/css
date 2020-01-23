#!/usr/bin/env python
#
# Recode of mklinks in Python, partly for the exercise and partly to
# improve the algorithm.
#       - Cameron Simpson <cs@cskk.id.au> 21may2006
#
# 27dec2017: recode again: prefer younger files over older file, cleaner logic.
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
from hashlib import sha1 as hashfunc
import os
from os.path import basename, dirname, isdir, isfile, join as joinpath
from stat import S_ISREG
import sys
from tempfile import NamedTemporaryFile
from cs.fileutils import read_from
from cs.logutils import setup_logging, info, warning, error
from cs.pfx import Pfx
from cs.py.func import prop

DISTINFO = {
    'description': "Tool for finding and hardlinking identical files.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.fileutils',
        'cs.logutils',
        'cs.pfx',
        'cs.py.func',
    ],
    'entry_points': {
        'console_scripts': [
            'mklinks = cs.app.mklinks:main'
        ],
    },
}

USAGE = "Usage: %s paths..."

def main(argv=None):
  ''' Usage: mklinks paths...
  '''
  if argv is None:
    argv = sys.argv
  cmd = basename(argv[0])
  setup_logging(cmd)
  usage = USAGE % (cmd,)
  paths = argv[1:]
  if not paths:
    warning("missing paths")
    print(usage, file=sys.stderr)
    return 2
  linker = Linker()
  # scan the supplied paths
  for path in paths:
    linker.scan(path)
  linker.merge()

class FileInfo(object):

  def __init__(self, dev, ino, size, mtime, paths=()):
    self.dev = dev
    self.ino = ino
    self.size = size
    self.mtime = mtime
    self.paths = set(paths)
    self._checksum = None

  def __str__(self):
    return "%d:%d:size=%d:mtime=%d:paths=%r" \
           % (self.dev, self.ino, self.size, self.mtime, self.paths)

  def __repr__(self):
    return "FileInfo(%d,%d,%d,%d,paths=%r)" \
           % (self.dev, self.ino, self.size, self.mtime, self.paths)

  @staticmethod
  def stat_key(S):
    return S.st_dev, S.st_ino

  @prop
  def key(self):
    return self.dev, self.ino

  @prop
  def path(self):
    return sorted(self.paths)[0]

  @prop
  def checksum(self):
    ''' Checksum the file contents, used as a proxy for comparing the actual content.
    '''
    csum = self._checksum
    if csum is None:
      path = self.path
      with Pfx("checksum %r", path):
        csum = hashfunc()
        with open(path, 'rb') as fp:
          read_len = 0
          for data in read_from(fp):
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

  def assimilate(self, other):
    ''' Link our primary path to all the paths from `other`. Return success.
    '''
    ok = True
    path = self.path
    with Pfx(path):
      if self is other or self.same_file(other):
        return
      assert self.same_dev(other)
      for opath in sorted(other.paths):
        with Pfx(opath):
          if opath in self.paths:
            warning("already assimilated")
            continue
          info("link")
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
                other.paths.remove(opath)
    return ok

class Linker(object):

  def __init__(self):
    self.sizemap = defaultdict(dict)    # file_size => FileInfo.key => FileInfo
    self.keymap = {}                    # FileInfo.key => FileInfo

  def scan(self, path):
    if isdir(path):
      for dirpath, dirnames, filenames in os.walk(path):
        for filename in sorted(filenames):
          path = joinpath(dirpath, filename)
          if isfile(path):
            self.addpath(path)
        dirnames[:] = sorted(dirnames)
    else:
      self.addpath(path)

  def addpath(self, path):
    with Pfx(path):
      S = os.lstat(path)
      if not S_ISREG(S.st_mode):
        return
      key = FileInfo.stat_key(S)
      FI = self.keymap.get(key)
      if FI:
        FI.paths.add(path)
      else:
        FI = FileInfo(S.st_dev, S.st_ino, S.st_size, S.st_mtime, (path,))
        self.keymap[key] = FI
        self.sizemap[S.st_size][key] = FI

  def merge(self):
    for size in reversed(sorted(self.sizemap.keys())):
      FIs = sorted(
          self.sizemap[size].values(),
          key=lambda FI: (FI.size, FI.mtime, FI.path),
          reverse=True)
      for i, FI in enumerate(FIs):
        # skip FileInfos with no paths
        if not FI.paths:
          continue
        for FI2 in FIs[i + 1:]:
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
          info("link %r => %r", FI2.path, FI.paths)
          FI.assimilate(FI2)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
