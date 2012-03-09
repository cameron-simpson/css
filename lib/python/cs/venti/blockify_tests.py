#!/usr/bin/python
#
# Self tests for cs.venti.blockify.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from .blockify import blocksOf, Blockifier

class TestAll(unittest.TestCase):

  def setUp(self):
    self.fp = open(__file__)

  def tearDown(self):
    self.fp.close()

  def test00blockifyAndRetrieve(self):
    data = self.fp.read()
    blocks = list(blocksOf([data]))
    data2 = "".join( b.data for b in blocks )
    self.assertEqual(len(data), len(data2), "data mismatch: len(data)=%d, len(data2)=%d" % (len(data), len(data2)))
    self.assertEqual(data, data2, "data mismatch: data and data2 same length but contents differ")
    ##for b in blocks: print "[", b.data, "]"

  def test01blockifier(self):
    from .cache import MemCacheStore
    with MemCacheStore():
      BL = Blockifier()
      alldata = []
      for data in self.fp:
        BL.add(data)
        alldata.append(data)
      top = BL.close()
      alldata = ''.join(alldata)
      stored = top[:]
      self.assertEqual( ''.join(alldata), stored )

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
