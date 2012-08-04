#!/usr/bin/python
#
# Self tests for cs.configutils.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest

class TestCOnfigUtils(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
