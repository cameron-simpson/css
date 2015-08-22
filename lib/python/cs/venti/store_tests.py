#!/usr/bin/python
#
# Self tests for cs.venti.store.
#       - Cameron Simpson <cs@zip.com.au>
#

import random
import sys
import unittest
from .store import MappingStore

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

class TestMappingStore(_TestStore):

  def _open_Store(self):
    self.S = MappingStore({}).open()

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
