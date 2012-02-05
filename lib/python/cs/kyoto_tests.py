#!/usr/bin/python
#
# Self tests for cs.kyoto.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.kyoto import KyotoCabinet

class TestLater(unittest.TestCase):

  pass

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
