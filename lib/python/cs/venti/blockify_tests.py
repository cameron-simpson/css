#!/usr/bin/python
#
# Self tests for cs.venti.blockify.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.logutils import D
from .blockify import blockify, Blockifier
from .cache import MemCacheStore

class TestAll(unittest.TestCase):

  def setUp(self):
    self.fp = open(__file__, "rb")

  def tearDown(self):
    self.fp.close()

  def test00blockifyAndRetrieve(self):
    with MemCacheStore():
      data = self.fp.read()
      blocks = list(blockify([data]))
      data2 = b''.join( b.data for b in blocks )
      self.assertEqual(len(data), len(data2), "data mismatch: len(data)=%d, len(data2)=%d" % (len(data), len(data2)))
      self.assertEqual(data, data2, "data mismatch: data and data2 same length but contents differ")
      ##for b in blocks: print("[", b.data, "]")

  def test01blockifier(self):
    with MemCacheStore():
      BL = Blockifier()
      alldata = []
      for data in self.fp:
        BL.add(data)
        alldata.append(data)
      top = BL.close()
      alldata = b''.join(alldata)
      stored = top.all_data()
      self.assertEqual( alldata, stored )

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
