#!/usr/bin/python
#
# Self tests for cs.venti.block.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
##from cs.logutils import D
from cs.logutils import D
from cs.venti import totext
from .block import Block, IndirectBlock

class TestAll(unittest.TestCase):

  def setUp(self):
    import random
    random.seed()

  def testSHA1(self):
    import random
    from .cache import MemCacheStore
    S = MemCacheStore()
    with S:
      subblocks = []
      for i in range(10):
        rs = bytes( random.randint(0, 255) for x in range(100) )
        B = Block(data=rs)
        self.assertEqual(len(B), 100)
        self.assertEqual(B.span, 100)
        B.store()
        subblocks.append(B)
      IB = IndirectBlock(subblocks=subblocks, doStore=True, doFlush=True)
      self.assertEqual(IB.span, 1000)
      IBH = IB.hashcode
      IBdata = IB.all_data()
      IB2data = IndirectBlock(hashcode=IBH).all_data()
      self.assertEqual(IBdata, IB2data, "IB:  %s\nIB2: %s" % (totext(IBdata), totext(IB2data)))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
