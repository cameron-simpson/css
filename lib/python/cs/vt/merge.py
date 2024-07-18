#!/usr/bin/env python3
#

''' Code to merge directory trees.
'''

from os.path import basename, dirname, exists as existspath

from typeguard import typechecked

from cs.lex import r
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call
from cs.resources import RunState, uses_runstate
from cs.upd import run_task

from .dir import Dir, FileDirent
from .paths import DirLike, OSDir

@uses_runstate
@typechecked
def merge(
    target_root: DirLike,
    source_root: DirLike,
    *,
    runstate: RunState,
    label=None,
):
  ''' Merge contents of the DirLike `source_root`
      into the DirLike `target_root`.

      Parameters:
      * `target_root`: a `DirLike` to receive contents
      * `source_root`: a `DirLike` from which to obtain contents
      * `runstate`: a `RunState` to support cancellation
      * `upd`: an `Upd` for displaying progress

      TODO: apply .stat results to merge targets.
      TODO: many modes for conflict resolution.
      TODO: whiteout entry support.
      TODO: change hash function support? or do I need 2 Stores?
  '''
  ok = True
  if not target_root.exists():
    target_root.create()
  if label is None:
    label = f'merge {target_root}=>{source_root}'
  with run_task(label) as proxy:
    for rpath, dirnames, filenames in source_root.walk():
      with Pfx(rpath):
        runstate.raiseif()
        proxy.prefix = rpath + '/'
        proxy.text = ' ...'
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
          continue
        # import files
        for filename in filenames:
          with Pfx(filename):
            runstate.raiseif()
            proxy.text = filename
            sourcef = source.get(filename)
            if sourcef is None:
              # no longer available
              continue
            if sourcef.isdir:
              warning("source file is now a directory, skipping")
              ok = False
              continue
            targetf = target.get(filename)
            if targetf is None:
              # new file
              if isinstance(target, Dir):
                # we can put a file in target
                if isinstance(sourcef, FileDirent):
                  # create FileDirent from block
                  target[filename] = FileDirent(sourcef.block)
                else:
                  # copy data
                  targetf = target.file_fromchunks(
                      filename, sourcef.datafrom()
                  )
              elif isinstance(target, OSDir):
                filepath = target.pathto(filename)
                assert not existspath(filepath)
                with pfx_call(open, filepath, 'wb') as f:
                  for bs in sourcef.datafrom():
                    assert f.write(bs) == len(bs)
              else:
                raise RuntimeError(
                    "do not know how to write a file to %s[filename=%r]" %
                    (r(target), filename)
                )
            else:
              warning("conflicting target file")
              ok = False
  return ok
