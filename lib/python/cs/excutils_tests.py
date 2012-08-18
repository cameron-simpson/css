#!/usr/bin/python
#
# Self tests for cs.excutils.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.excutils import return_exc_info, returns_exc_info

class TestExcUtils(unittest.TestCase):

  def test_return_exc_info(self):
    def divfunc(a, b):
      return a/b
    retval, exc_info = return_exc_info(divfunc, 4, 2)
    self.assertEquals(retval, 2)
    self.assertTrue(exc_info is None)
    retval, exc_info = return_exc_info(divfunc, 4, 0)
    self.assertTrue(retval is None)
    self.assertTrue(exc_info[0] is ZeroDivisionError)

  def test_returns_exc_info(self):
    @returns_exc_info
    def divfunc(a, b):
      return a/b
    retval, exc_info = divfunc(4, 2)
    self.assertEquals(retval, 2)
    self.assertTrue(exc_info is None)
    retval, exc_info = divfunc(4, 0)
    self.assertTrue(retval is None)
    self.assertTrue(exc_info[0] is ZeroDivisionError)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
