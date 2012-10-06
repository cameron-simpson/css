#!/usr/bin/python
#
# Self tests for cs.connect.
#       - Cameron Simpson <cs@zip.com.au> 06oct2012
#

import sys
import unittest
from cs.connect import DataQueue

class TestConnect(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def test00DataQueue(self):
    DQ = DataQueue()
    print "DQ =", DQ
    self.assertRaises(ValueError, DQ.sent, -1)
    self.assertRaises(ValueError, DQ.sent, 1)
    DQ.close()

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
