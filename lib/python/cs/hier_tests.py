#!/usr/bin/python
#
# Self tests for cs.hier.
#       - Cameron Simpson <cs@cskk.id.au>
#

from io import StringIO
import sys
import unittest
from cs.hier import loadfp

class TestHier(unittest.TestCase):
  ''' Test `cs.hier`.
  '''

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def _testLoad(self, text, D):
    H = loadfp(StringIO(text))
    self.assertEqual(H, D)

  def test01load(self):
    self._testLoad("A 1\nB 2\n", {"A": 1, "B": 2})

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
