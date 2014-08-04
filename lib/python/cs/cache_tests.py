#!/usr/bin/python
#
# Unit tests for cs.cache.
#       - Cameron Simpson <cs@zip.com.au> 04aug2014
#

from __future__ import print_function
import sys
import os
import os.path
import errno
from threading import Lock
import time
import unittest
from .cache import LRU_Cache
from .logutils import D, X

class Test_LRU_Cache(unittest.TestCase):

  def test_setup(self):
    self.assertRaises(ValueError, LRU_Cache, maxsize=0)
    C = LRU_Cache(maxsize=2)
    self.assertEqual(C, {})
    C[1] = 2
    self.assertEqual(C, {1:2})
    C[3] = 4
    self.assertEqual(C, {1:2, 3:4})
    C[5] = 6
    self.assertEqual(C, {3:4, 5:6})

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
