#!/usr/bin/python
#
# Self tests for cs.venti.store.
#       - Cameron Simpson <cs@zip.com.au>
#

import random
import sys
import unittest
from .store import MappingStore
from cs.logutils import X

def rand0(maxn):
  return random.randint(0, maxn)

def randblock(size):
  ''' Generate a pseudorandom chunk of bytes of the specified size.
  '''
  chunk = bytes( rand0(255) for x in range(size) )
  if type(chunk) is not bytes:
    raise RuntimeError("BANG2")
  return chunk

class _TestStore(unittest.TestCase):

  def setUp(self):
    self._open_Store()

  def _open_Store(self):
    raise unittest.SkipTest("no Store in base class")

  def tearDown(self):
    self.S.close()

  def test01empty(self):
    S = self.S
    self.assertEqual(len(S), 0)
    size = random.randint(127, 16384)
    data = randblock(size)
    h = S.hash(data)
    self.assertFalse(S.contains(h))
    self.assertNotIn(h, S)

  def test02add(self):
    S = self.S
    for _ in range(16):
      size = random.randint(127, 16384)
      data = randblock(size)
      h = S.hash(data)
      h2 = S.add(data)
      self.assertEqual(h, h2)

class TestMappingStore(_TestStore):

  def _open_Store(self):
    self.S = MappingStore({}).open()

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
