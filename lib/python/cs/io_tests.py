#!/usr/bin/python
#
# Self tests for cs.io.
#       - Cameron Simpson <cs@zip.com.au>
#

from StringIO import StringIO
import sys
import unittest
from cs.io import contlines

class TestIO(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def _testContlines(self, text, lines):
    self.assertEquals( list( contlines(StringIO(text)) ), lines )

  def test00contlines(self):
    self._testContlines("", [])
    self._testContlines("line 1\nline 2\n", ["line 1\n", "line 2\n"])
    self._testContlines("line 1\n  line 1b\n", ["line 1\n  line 1b\n"])
    self._testContlines("line 0\nline 1\n  line 1b\nline 2\n",
                        ["line 0\n", "line 1\n  line 1b\n", "line 2\n"])

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
