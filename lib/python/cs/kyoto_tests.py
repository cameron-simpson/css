#!/usr/bin/python
#
# Self tests for cs.kyoto.
#       - Cameron Simpson <cs@cskk.id.au>
#

import sys
import unittest

class TestLater(unittest.TestCase):
  pass

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
