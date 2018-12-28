#!/usr/bin/python
#
# BlockMap tests.
# - Cameron Simpson <cs@cskk.id.au>
#

''' BlockMap unit tests.
'''

import sys
from random import randint
import unittest
from .randutils import randblock
import cs.x
from cs.x import X
from .block import IndirectBlock, HashCodeBlock, _IndirectBlock
from .blockmap import BlockMap
from .store import MappingStore

cs.x.X_via_tty = True

class TestAll(unittest.TestCase):
  ''' Tests for BlockMaps.
  '''

  def setUp(self):
    ''' Unit test setup.
    '''
    self.S = MappingStore("TestAll", {})

  def _gen_data(self, depth, width):
    ''' Generate a block tree of the specified width and height filled with random data.
    '''
    if depth < 0:
      raise ValueError("depth < 0 (%s)" % (depth,))
    if width < 1:
      raise ValueError("width < 1 (%s)" % (width,))
    subblocks = []
    subchunks = []
    S = self.S
    with S:
      for _ in range(width):
        if depth == 0:
          flat_data = randblock(randint(1, 16384))
          ##flat_data = bytes(randint(1, 16384))
          top_block = HashCodeBlock(data=flat_data)
        else:
          top_block, flat_data = self._gen_data(depth-1, width)
        subblocks.append(top_block)
        subchunks.append(flat_data)
      if width == 1:
        return subblocks[0], subchunks[0]
      return IndirectBlock(subblocks=subblocks), b''.join(subchunks)

  def test001(self):
    ''' Exercise the BlockMap in various configurations.
    '''
    with self.S:
      for depth in 0, 1, 2, 3:
        for width in 1, 2, 7:   # , 17:
          X("gen depth=%d, width=%d ...", depth, width)
          top_block, flat_data = self._gen_data(depth, width)
          if not isinstance(top_block, _IndirectBlock):
            top_block = IndirectBlock(subblocks=[top_block], force=True)
          for mapsize in 999999, 10000007, 13131313, None:
            with self.subTest(mapsize=mapsize, depth=depth, width=width):
              X("test mapsize=%s", mapsize)
              bmap = BlockMap(top_block, mapsize=mapsize)
              bmap.join()
              X("full data cmp...")
              bmap.self_check()
              self.assertEqual(flat_data, bmap.data(0, len(flat_data)))
              X("full data cmp DONE")
              for _ in range(16):
                start = randint(0, len(flat_data)-1)
                end = randint(start, len(flat_data))
                X("compare [%d:%d]...", start, end)
                self.assertEqual(
                    flat_data[start:end],
                    bmap.data(start, end-start),
                    'flat_data[%d:%s] != bmap.data(%d, %d)'
                    % (start, end, start, end-start))

def selftest(argv):
  ''' Run the unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
