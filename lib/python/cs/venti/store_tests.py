#!/usr/bin/python
#
# Self tests for cs.venti.store.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from .store import MappingStore

class TestAll(unittest.TestCase):

  def setUp(self):
    import random
    random.seed()
    self.S = MappingStore({})

  def tearDown(self):
    self.S.close()

  def test01empty(self):
    S = self.S
    self.assertEquals(len(S), 0)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
