#!/usr/bin/python
#
# Self tests for cs.venti.block.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
##from cs.logutils import D
from cs.venti import totext
from cs.venti.block import Block, IndirectBlock

class TestAll(unittest.TestCase):

  def setUp(self):
    import random
    random.seed()

  def testSHA1(self):
    import random
    from cs.venti.cache import MemCacheStore
    S = MemCacheStore()
    with S:
      IB = IndirectBlock()
      for i in range(10):
        rs = ''.join( chr(random.randint(0, 255)) for x in range(100) )
        B = Block(data=rs)
        self.assertEqual(len(B), 100)
        IB.append(B)
        self.assertEqual(len(IB), (i+1)*100)
      IB.store()
      self.assertEqual(len(IB), 1000)
      IBH = IB.hashcode
      IBdata = IB.data
      ##D("IBdata = %s:%d:%r", type(IBdata), len(IBdata), IBdata,)
      IB2data = IndirectBlock(hashcode=IBH, span=len(IBdata)).data
      ##D("IB2data = %s:%d:%r", type(IB2data), len(IB2data), IB2data,)
      self.assertEqual(IBdata, IB2data, "IB:  %s\nIB2: %s" % (totext(IBdata), totext(IB2data)))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
