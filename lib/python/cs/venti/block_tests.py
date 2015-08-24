#!/usr/bin/python
#
# Self tests for cs.venti.block.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.logutils import D, X
from cs.randutils import rand0, randblock
from . import totext
from .block import Block, IndirectBlock
from .cache import MemoryCacheStore

class TestAll(unittest.TestCase):

  def setUp(self):
    self.S = MemoryCacheStore()

  def test00Block(self):
    # make some randbom blocks, check size and content
    with self.S:
      for _ in range(10):
        size = rand0(16384)
        rs = randblock(size)
        self.assertEqual(len(rs), size)
        B = Block(data=rs)
        self.assertEqual(len(B), size)
        self.assertEqual(B.span, size)
        self.assertEqual(B.data, rs)
        self.assertEqual(B.all_data(), rs)

  def testSHA1(self):
    S = self.S
    with S:
      subblocks = []
      for i in range(10):
        rs = randblock(100)
        self.assertEqual(len(rs), 100)
        B = Block(data=rs)
        self.assertEqual(len(B), 100)
        self.assertEqual(B.span, 100)
        subblocks.append(B)
      IB = IndirectBlock(subblocks=subblocks)
      IBspan = IB.span
      self.assertEqual(IBspan, 1000)
      IBH = IB.hashcode
      IBdata = IB.all_data()
      IB2 = IndirectBlock(hashcode=IBH)
      IB2data = IB2.all_data()
      self.assertEqual(IBdata, IB2data, "IB:  %s\nIB2: %s" % (totext(IBdata), totext(IB2data)))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
