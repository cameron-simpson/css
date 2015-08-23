#!/usr/bin/python
#
# Self tests for cs.venti.store.
#       - Cameron Simpson <cs@zip.com.au>
#

import random
import sys
import unittest
from cs.logutils import X
from cs.randutils import rand0, randblock
from .store import MappingStore

class _TestStore(unittest.TestCase):

  def setUp(self):
    self._open_Store()

  def _open_Store(self):
    raise unittest.SkipTest("no Store in base class")

  def tearDown(self):
    self.S.close()

  def test01empty(self):
    S = self.S
    if hasattr(S, '__len__'):
      self.assertEqual(len(S), 0)
    else:
      X("SKIP test of len(S): no __len__ in %s", type(S))
    size = random.randint(127, 16384)
    data = randblock(size)
    h = S.hash(data)
    self.assertFalse(S.contains(h))
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

  def _open_Store(self):
    self.S = MappingStore({}).open()

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
