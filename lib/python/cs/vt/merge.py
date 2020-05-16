#!/usr/bin/env python3
#

''' Code to merge directory trees.
'''

from os.path import basename, dirname
from icontract import require
from cs.logutils import warning
from cs.pfx import Pfx
from cs.resources import RunState
from .dir import Dir, FileDirent
from .paths import DirLike

@require(lambda target_root: isinstance(target_root, DirLike))
@require(lambda source_root: isinstance(source_root, DirLike))
@require(lambda runstate: isinstance(runstate, RunState))
def merge(target_root, source_root, runstate):
  ''' Merge contents of the DirLike `source_root`
      into the DirLike `target_root`.

      Parameters:
      * `target_root`: a `DirLike` to receive contents
      * `source_root`: a `DirLike` from which to obtain contents
      * `runstate`: a `RunState` to support cancellation

      TODO: apply .stat results to merge targets.
      TODO: many modes for conflict resolution.
      TODO: whiteout entry support.
      TODO: change hash function support? or do I need 2 Stores?
  '''
  ok = True
  if not target_root.exists():
    target_root.create()
  for rpath, dirnames, filenames in source_root.walk():
    with Pfx(rpath):
      if runstate.cancelled:
        warning("cancelled")
        break
      source = source_root.resolve(rpath)
      if source is None:
        warning("no longer resolves, pruning this branch")
        ok = False
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
        ok = False
      # import files
      for name in filenames:
        with Pfx(name):
          if runstate.cancelled:
            warning("cancelled")
            break
          sourcef = source.get(name)
          if sourcef is None:
            # no longer available
            continue
          if sourcef.isdir:
            warning("source now a directory, skipping")
            ok = False
            continue
          targetf = target.get(name)
          if targetf is None:
            # new file
            if isinstance(target, Dir) and isinstance(sourcef, FileDirent):
              # create FileDirent from block
              target[name] = FileDirent(sourcef.block)
            else:
              # copy data
              targetf = target.file_fromchunks(name, sourcef.datafrom())
          else:
            warning("conflicting target file")
            ok = False
  if runstate.cancelled:
    ok = False
  return ok
