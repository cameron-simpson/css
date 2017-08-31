#!/usr/bin/python
#
# Self tests for cs.venti.store.
#       - Cameron Simpson <cs@cskk.id.au>
#

import os
from os.path import abspath
import random
import shutil
import sys
import tempfile
import unittest
from cs.excutils import logexc
from cs.logutils import setup_logging, warning
from cs.randutils import randblock
from cs.x import X
import cs.x
cs.x.X_via_tty = True
from . import _TestAdditionsMixin
from .datadir import GDBMIndex, KyotoIndex
from .store import MappingStore, DataDirStore, ProgressStore
from .hash import HashUtilDict, DEFAULT_HASHCLASS, HASHCLASS_BY_NAME
from .hash_tests import _TestHashCodeUtils

def multitest(method):
  ''' Decorator to permute a test method for multplie Store types and hash classes.
  '''
  def testMethod(self):
    for test_store, factory, kwargs, *args in (
        ('MappingStore', MappingStore, {'mapping': {}}),
        ('DataDirStore', DataDirStore,
                         {'indexclass': GDBMIndex, 'rollover': 200000}),
      ):
      for hashclass_name in sorted(HASHCLASS_BY_NAME.keys()):
        hashclass = HASHCLASS_BY_NAME[hashclass_name]
        with self.subTest(test_store=test_store, hashclass=hashclass_name):
          args = ["%s:%s:%s" % (test_store, hashclass_name, method.__name__)] + args
          T = self.mktmpdir(prefix=method.__module__+'.'+method.__name__)
          with T as tmpdirpath:
            if factory in (DataDirStore,):
              args.insert(1, tmpdirpath)
            self.S = factory(*args, hashclass=hashclass, **kwargs)
            with self.S:
              method(self)
              self.S.flush()
  return testMethod

class TestStore(unittest.TestCase, _TestAdditionsMixin):

  @multitest
  def test00empty(self):
    S = self.S
    self.assertLen(S, 0)

  @multitest
  def test01add_new_block(self):
    S = self.S
    self.assertLen(S, 0)
    size = random.randint(127, 16384)
    data = randblock(size)
    # compute block hash but do not store
    h = S.hash(data)
    self.assertLen(S, 0)
    ok = S.contains(h)
    self.assertFalse(ok)
    self.assertNotIn(h, S)
    # now add the block
    h2 = S.add(data)
    self.assertEqual(h, h2)
    self.assertLen(S, 1)
    ok = S.contains(h)
    self.assertTrue(ok)
    self.assertIn(h, S)

  @multitest
  def test02add_get(self):
    S = self.S
    self.assertLen(S, 0)
    random_chunk_map = {}
    for _ in range(16):
      size = random.randint(127, 16384)
      data = randblock(size)
      h = S.hash(data)
      h2 = S.add(data)
      self.assertEqual(h, h2)
      random_chunk_map[h] = data
    self.assertLen(S, 16)
    for h in random_chunk_map:
      chunk = S.get(h)
      self.assertIsNot(chunk, None)
      self.assertEqual(chunk, random_chunk_map[h])

##class TestMappingStore(TestStore, unittest.TestCase):
##
##  def _init_Store(self):
##    self.S = MappingStore("TestMappingStore", {}, hashclass=self.hashclass)
##
##class TestProgressStore(TestStore, unittest.TestCase):
##
##  def _init_Store(self):
##    M = MappingStore("TestProgressStore", {}, hashclass=self.hashclass)
##    MO = M.open()
##    P = ProgressStore("ProgressMappingStore", MO)
##    self.S = P.open()

class TestHashCodeUtilsMappingStoreDict(_TestHashCodeUtils, unittest.TestCase):
  ''' Test HashUtils on a MappingStore on a plain dict.
  '''
  MAP_FACTORY = lambda self: MappingStore("TestHashCodeUtilsMappingStoreDict", {}, hashclass=DEFAULT_HASHCLASS)

class TestHashCodeUtilsMappingStoreHashUtilDict(_TestHashCodeUtils, unittest.TestCase):
  ''' Test HashUtils on a MappingStore on a HashUtilDict.
  '''
  MAP_FACTORY = lambda self: MappingStore("TestHashCodeUtilsMappingStoreHashUtilDict", HashUtilDict(), hashclass=DEFAULT_HASHCLASS)

try:
  import kyotocabinet
except ImportError:
  pass
else:
  class TestDataDirStoreKyoto(_TestDataDirStore, unittest.TestCase):
    INDEX_CLASS = KyotoIndex
  class TestHashCodeUtilsDataDirStoreKyotoStore(_TestHashCodeUtils, unittest.TestCase):
    MAP_FACTORY = lambda self: DataDirStore("TestHashCodeUtilsDataDirStoreKyotoStore", self.mktmpdir(), hashclass=DEFAULT_HASHCLASS, indexclass=KyotoIndex, rollover=200000)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  setup_logging(__file__)
  selftest(sys.argv)
