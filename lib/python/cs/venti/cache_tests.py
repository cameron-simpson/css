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
from .hash import DEFAULT_HASHCLASS

class TestCacheStore(_TestStore, unittest.TestCase):
  def _init_Store(self):
    self.fastS = MappingStore("TestCacheStore.fastS", {}, hashclass=self.hashclass)
    self.slowS = MappingStore("TestCacheStore.slowS", {}, hashclass=self.hashclass)
    self.S = CacheStore("TestCacheStore.S", self.fastS, self.slowS, hashclass=self.hashclass)

class TestMemoryCacheStore(_TestStore, unittest.TestCase):
  def _init_Store(self):
    self.S = MemoryCacheStore(16, hashclass=self.hashclass)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
