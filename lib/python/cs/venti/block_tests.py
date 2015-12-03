#!/usr/bin/python
#
# Self tests for cs.venti.block.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.logutils import D, X
from cs.randutils import rand0, randblock
from cs.py3 import bytes
from . import totext
from .block import Block, IndirectBlock, verify_block
from .cache import MemoryCacheStore

class TestAll(unittest.TestCase):

  def setUp(self):
    self.S = MemoryCacheStore()

  def _verify_block(self, B, **kw):
    errs = list(verify_block(B, **kw))
    self.assertEqual(errs, [])
    
  def test00Block(self):
    # make some randbom blocks, check size and content
    with self.S:
      for _ in range(10):
        size = rand0(16384)
        rs = randblock(size)
        self.assertEqual(len(rs), size)
        B = Block(data=rs)
        self._verify_block(B)
        self.assertEqual(len(B), size)
        self.assertEqual(B.span, size)
        self.assertEqual(B.data, rs)
        self.assertEqual(B.all_data(), rs)

  def test10IndirectBlock(self):
    S = self.S
    with S:
      for _ in range(8):
        fullblock = bytes(())
        subblocks = []
        total_length = 0
        for _ in range(rand0(16)):
          size = rand0(16384)
          rs = randblock(size)
          total_length += len(rs)
          B = Block(data=rs)
          subblocks.append(B)
          fullblock += rs
        IB = IndirectBlock(subblocks=subblocks)
        self._verify_block(IB, recurse=True)
        IBspan = IB.span
        self.assertEqual(IBspan, total_length)
        IBH = IB.hashcode
        IBdata = IB.all_data()
        self.assertEqual(len(IBdata), total_length)
        self.assertEqual(IBdata, fullblock)
        # refetch block by hashcode
        IB2 = IndirectBlock(hashcode=IBH)
        self._verify_block(IB2, recurse=True)
        IB2data = IB2.all_data()
        self.assertEqual(IBdata, IB2data, "IB:  %s\nIB2: %s" % (totext(IBdata), totext(IB2data)))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
