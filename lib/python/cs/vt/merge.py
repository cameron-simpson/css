#!/usr/bin/env python3
#

''' Code to merge directories trees.
'''

from os.path import basename, dirname
from icontract import require
from cs.logutils import warning
from cs.pfx import Pfx, XP
from .dir import Dir, FileDirent
from .paths import DirLike

@require(lambda target_root: isinstance(target_root, DirLike))
@require(lambda source_root: isinstance(source_root, DirLike))
def merge(target_root, source_root):
  ''' Merge contents of the DirLike `source_root`
      into the DirLike `target_root`.
  '''
  for rpath, dirnames, filenames in source_root.walk():
    with Pfx(rpath):
      XP("LOOP TOP: rpath=%r", rpath)
      source = source_root.resolve(rpath)
      if source is None:
        warning("no longer resolves, pruning this branch")
        dirnames[:] = []
        filenames[:] = []
        continue
      target = target_root.resolve(rpath)
      if target is None:
        # new in target tree: mkdir the target node
        rpath_up = dirname(rpath)
        rpath_base = basename(rpath)
        target_up = target_root.resolve(rpath_up)
        target = target_up.mkdir(rpath_base)
      elif target.isdir:
        pass
      else:
        warning("conflicting item in target: not a directory")
      # import files
      for name in filenames:
        with Pfx(name):
          XP("FILE LOOP TOP")
          sourcef = source.get(name)
          if sourcef is None:
            # no longer available
            continue
          if sourcef.isdir:
            warning("source now a directory, skipping")
            continue
          targetf = target.get(name)
          if targetf is None:
            # new file
            if isinstance(target, Dir):
              if isinstance(sourcef, FileDirent):
                target[name] = FileDirent(sourcef.block)
              else:
                targetf = target.file_fromchunks(name, sourcef.datafrom())
          else:
            warning("conflicting target file")
