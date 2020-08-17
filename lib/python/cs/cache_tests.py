#!/usr/bin/python
#
# Unit tests for cs.cache.
#       - Cameron Simpson <cs@cskk.id.au> 04aug2014
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

def check(o):
  return o._selfcheck()

class Test_LRU_Cache(unittest.TestCase):
  ''' Test `cs.cache.LRU_Cache`.
  '''

  def test_setup(self):
    self.assertRaises(ValueError, LRU_Cache, maxsize=0)
    C = LRU_Cache(maxsize=2)
    check(C)
    self.assertEqual(C, {})
    C[1] = 2
    self.assertEqual(C, {1: 2})
    check(C)
    C[3] = 4
    self.assertEqual(C, {1: 2, 3: 4})
    check(C)
    C[5] = 6
    self.assertEqual(C, {3: 4, 5: 6})
    check(C)
    C.flush()
    self.assertEqual(C, {})
    check(C)

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
