#!/usr/bin/python
#
# Self tests for cs.venti.store.
#       - Cameron Simpson <cs@zip.com.au>
#

import random
import sys
import unittest
from cs.excutils import logexc
from cs.logutils import X
from cs.randutils import rand0, randblock
from .store import MappingStore

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
    if hasattr(S, '__len__'):
      self.assertEqual(len(S), 0)
    else:
      raise unittest.SkipTest("no __len__ in %s" % (type(S),))

  def test01add_new_block(self):
    S = self.S
    size = random.randint(127, 16384)
    data = randblock(size)
    h = S.hash(data)
    ok = S.contains(h)
    self.assertFalse(ok)
    self.assertNotIn(h, S)

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

class TestMappingStore(_TestStore):

  def _init_Store(self):
    self.S = MappingStore({}).open()

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
