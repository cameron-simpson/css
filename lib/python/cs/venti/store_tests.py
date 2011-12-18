#!/usr/bin/python
#
# Self tests for cs.venti.store.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.venti.store import decodeIndexEntry, encodeIndexEntry

class TestAll(unittest.TestCase):

  def setUp(self):
    import random
    random.seed()

  def testIndexEntry(self):
    import random
    for count in range(100):
      rand_n = random.randint(0, 65536)
      rand_offset = random.randint(0, 65536)
      n, offset = decodeIndexEntry(encodeIndexEntry(rand_n, rand_offset))
      self.assertEqual(rand_n, n)
      self.assertEqual(rand_offset, offset)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
