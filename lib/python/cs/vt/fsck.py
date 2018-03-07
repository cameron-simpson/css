#!/usr/bin/python
#
# Filesystem checks.
#   - Cameron Simpson <cs@cskk.id.au> 11feb2017
#

from cs.logutils import error, warning, info
from cs.pfx import Pfx
from cs.x import X
from . import defaults
from .block import HashCodeBlock, LiteralBlock
from .paths import walk

def fsck_dir(rootD):
  ''' Inspect a Dir for correctness.
  '''
  ok = True
  for step in walk(rootD, yield_status=True):
    if isinstance(step, bool):
      if not step:
        error("errors from walk")
        ok = False
      break
    D, relpath, dirnames, filenames = step
    with Pfx(relpath):
      for filename in sorted(filenames):
        with Pfx("filename=%r", filename):
          try:
            E = D[filename]
          except KeyError as e:
            error("not in D")
          else:
            B = E.block
            if not fsck_Block(B):
              error("E.block fails fsck")
              ok = False
      for dirname in sorted(dirnames):
        with Pfx("dirname=%r", dirname):
          try:
            subD = D[dirname]
          except KeyError as e:
            error("not in D")
          else:
            B = subD.block
            if not fsck_Block(B):
              error("E.block fails fsck")
              ok = False
  return ok

def fsck_Block(B):
  ''' Check Block.
  '''
  ok = False
  with defaults.S as S:
    if isinstance(B, HashCodeBlock):
      hashcode = B.hashcode
      with Pfx("%s", hashcode):
        try:
          data = S[hashcode]
        except KeyError as e:
          error("not in Store %s", S)
        else:
          ok = True
          if B.indirect:
            suboffset = 0
            for i, subB in enumerate(B.subblocks):
              with Pfx("subblocks[%d]:%d..%d", i, suboffset, suboffset+len(subB)):
                if not fsck_Block(subB):
                  ok = False
                suboffset += len(subB)
            if suboffset != len(B):
              error("len(B)=%d, sum(len(subblocks))=%d", len(B), suboffset)
          else:
            if len(B) != len(data):
              error("len(B)=%d, len(data)=%d", len(B), len(data))
              ok = False
    elif isinstance(B, LiteralBlock):
      ok = True
      data = B.data
      if len(B) != len(data):
        error("len(B)=%d, len(data)=%d", len(B), len(data))
        ok = False
    else:
      error("unsupported Block type: %s", B.__class__)
  return ok
