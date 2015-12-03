#!/usr/bin/python
#
# Self tests for cs.venti.cache.
#       - Cameron Simpson <cs@zip.com.au> 25aug2015
#

import os
import random
import sys
import unittest
from cs.logutils import X
from .store import MappingStore
from .store_tests import _TestStore
from .cache import CacheStore, MemoryCacheStore

class TestCacheStore(_TestStore, unittest.TestCase):

  def _init_Store(self):
    self.fastS = MappingStore({})
    self.slowS = MappingStore({})
    self.S = CacheStore(self.fastS, self.slowS)

class TestMemoryCacheStore(_TestStore, unittest.TestCase):

  def _init_Store(self):
    self.S = MemoryCacheStore(16)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
