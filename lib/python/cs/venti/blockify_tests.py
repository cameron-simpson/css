#!/usr/bin/python
#
# Self tests for cs.venti.blockify.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.logutils import D
from .blockify import blockify
from .cache import MemoryCacheStore

class TestAll(unittest.TestCase):

  def setUp(self):
    self.fp = open(__file__, "rb")

  def tearDown(self):
    self.fp.close()

  def test00blockifyAndRetrieve(self):
    with MemoryCacheStore():
      data = self.fp.read()
      blocks = list(blockify([data]))
      data2 = b''.join( b.data for b in blocks )
      self.assertEqual(len(data), len(data2), "data mismatch: len(data)=%d, len(data2)=%d" % (len(data), len(data2)))
      self.assertEqual(data, data2, "data mismatch: data and data2 same length but contents differ")
      ##for b in blocks: print("[", b.data, "]")

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
