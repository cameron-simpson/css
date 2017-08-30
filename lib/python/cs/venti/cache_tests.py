#!/usr/bin/python
#
# Self tests for cs.venti.cache.
#       - Cameron Simpson <cs@zip.com.au> 25aug2015
#

import sys
import unittest
from .store import MappingStore
from .store_tests import TestStore
from .cache import FileCacheStore

class TestFileCacheStore(TestStore, unittest.TestCase):
  def _init_Store(self):
    self.fastS = MappingStore("TestCacheStore.fastS", {}, hashclass=self.hashclass)
    self.slowS = MappingStore("TestCacheStore.slowS", {}, hashclass=self.hashclass)
    self.S = FileCacheStore("TestCacheStore.S", self.fastS, self.slowS, hashclass=self.hashclass)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
