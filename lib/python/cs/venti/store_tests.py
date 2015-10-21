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
from cs.logutils import X
from cs.randutils import rand0, randblock
from .datafile import GDBMIndex, KyotoIndex
from .store import MappingStore, DataDirStore

class _TestStore(unittest.TestCase):

  def setUp(self):
    self._init_Store()
    self.S.open()

  def _init_Store(self):
    raise unittest.SkipTest("no Store in base class")

  def tearDown(self):
    self.S.close()

  def test00empty(self):
    S = self.S
    try:
      len_method = S.__len__
    except AttributeError as e:
      raise unittest.SkipTest("no __len__ in %s: %s" % (type(S), e))
    else:
      try:
        store_len = len(S)
      except TypeError as e:
        raise unittest.SkipTest(".len not implemented: %s" % (e,))
      else:
        self.assertEqual(len(S), 0)
    S.flush()

  def test01add_new_block(self):
    S = self.S
    size = random.randint(127, 16384)
    data = randblock(size)
    h = S.hash(data)
    ok = S.contains(h)
    self.assertFalse(ok)
    self.assertNotIn(h, S)
    S.flush()

  def test02add_get(self):
    S = self.S
    random_chunk_map = {}
    for _ in range(16):
      size = random.randint(127, 16384)
      data = randblock(size)
      h = S.hash(data)
      h2 = S.add(data)
      self.assertEqual(h, h2)
      random_chunk_map[h] = data
    for h in random_chunk_map:
      chunk = S.get(h)
      self.assertIsNot(chunk, None)
      self.assertEqual(chunk, random_chunk_map[h])
    S.flush()

  def test03first(self):
    S = self.S
    try:
      first_hashcode = S.first()
    except NotImplementedError as e:
      raise unittest.SkipTest("no .first in %s: %s" % (S, e))
    random_chunk_map = {}
    for _ in range(16):
      size = random.randint(127, 16384)
      data = randblock(size)
      h = S.hash(data)
      h2 = S.add(data)
      self.assertEqual(h, h2)
      random_chunk_map[h] = data
    ordered_hashcodes = sorted(random_chunk_map.keys())
    first_hashcode = S.first()
    self.assertEqual(first_hashcode, ordered_hashcodes[0])

class TestMappingStore(_TestStore):

  def _init_Store(self):
    self.S = MappingStore({}).open()

class _TestDataDirStore(_TestStore):

  INDEX_CLASS = None

  def _init_Store(self):
    indexclass = self.__class__.INDEX_CLASS
    if indexclass is None:
      raise unittest.SkipTest("INDEX_CLASS is None, skipping TestCase")
    random.seed()
    self.pathname = abspath(tempfile.mkdtemp(prefix="cs.venti.store.testdatadir", suffix=".dir", dir='.'))
    self.S = DataDirStore(self.pathname, indexclass=indexclass, rollover=200000)

  def tearDown(self):
    os.system("ls -l "+self.pathname)
    shutil.rmtree(self.pathname)
    _TestStore.tearDown(self)

class TestDataDirStoreGDBM(_TestDataDirStore):
  INDEX_CLASS = GDBMIndex

class TestDataDirStoreKyoto(_TestDataDirStore):
  INDEX_CLASS = KyotoIndex

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
