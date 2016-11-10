#!/usr/bin/python
#
# Self tests for cs.venti.store.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
from os.path import abspath
import random
import shutil
import sys
import tempfile
import unittest
from cs.excutils import logexc
import cs.logutils
cs.logutils.X_via_tty = True
from cs.logutils import setup_logging, warning, X
from cs.randutils import randblock
from . import _TestAdditionsMixin
from .datafile import GDBMIndex, KyotoIndex
from .store import MappingStore, DataDirStore, ProgressStore
from .hash import HashUtilDict, DEFAULT_HASHCLASS
from .hash_tests import _TestHashCodeUtils

class _TestStore(_TestAdditionsMixin):

  hashclass = DEFAULT_HASHCLASS

  def setUp(self):
    self._init_Store()
    self.S.open()

  def tearDown(self):
    self.S.close()

  def _init_Store(self):
    raise unittest.SkipTest("no Store in base class")

  def test00empty(self):
    S = self.S
    self.assertLen(S, 0)
    S.flush()

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
    S.flush()

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
    S.flush()

class TestMappingStore(_TestStore, unittest.TestCase):

  def _init_Store(self):
    self.S = MappingStore("TestMappingStore", {}, hashclass=self.hashclass)

class TestProgressStore(_TestStore, unittest.TestCase):

  def _init_Store(self):
    self.S = ProgressStore("ProgressMappingStore", MappingStore("TestProgressStore", self.hashclass, {}).open()).open()

class TestHashCodeUtilsMappingStoreDict(_TestHashCodeUtils, unittest.TestCase):
  ''' Test HashUtils on a MappingStore on a plain dict.
  '''
  MAP_FACTORY = lambda self: MappingStore("TestHashCodeUtilsMappingStoreDict", {}, hashclass=DEFAULT_HASHCLASS)

class TestHashCodeUtilsMappingStoreHashUtilDict(_TestHashCodeUtils, unittest.TestCase):
  ''' Test HashUtils on a MappingStore on a HashUtilDict.
  '''
  MAP_FACTORY = lambda self: MappingStore("TestHashCodeUtilsMappingStoreHashUtilDict", HashUtilDict(), hashclass=DEFAULT_HASHCLASS)

class _TestDataDirStore(_TestStore):

  INDEX_CLASS = None

  def _init_Store(self):
    indexclass = self.__class__.INDEX_CLASS
    random.seed()
    self.pathname = self.mktmpdir()
    self.S = DataDirStore("_TestDataDirStore", self.pathname, indexclass=indexclass, hashclass=self.hashclass, rollover=200000)

  def tearDown(self):
    ##os.system("ls -l "+self.pathname)
    _TestStore.tearDown(self)
    shutil.rmtree(self.pathname)

class TestDataDirStoreGDBM(_TestDataDirStore, unittest.TestCase):
  INDEX_CLASS = GDBMIndex
class TestHashCodeUtilsDataDirStoreGDBMStore(_TestHashCodeUtils, unittest.TestCase):
  MAP_FACTORY = lambda self: DataDirStore("TestHashCodeUtilsDataDirStoreGDBMStore", self.mktmpdir(), hashclass=DEFAULT_HASHCLASS, indexclass=GDBMIndex, rollover=200000)

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
