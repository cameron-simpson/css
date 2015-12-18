#!/usr/bin/python
#
# Tests for cs.venti.pushpull.  - Cameron Simpson <cs@zip.com.au> 18dec2015
#

import unittest
from random import randint
from cs.randutils import rand0, randblock
from .hash import HashUtilDict
from .pushpull import missing_hashcodes

class TestMissingHashCodes(unittest.TestCase):

  def setUp(self):
    self.map1 = HashUtilDict()
    self.map2 = HashUtilDict()

  def test00empty(self):
    ''' Compare two empty maps.
    '''
    missing = list(missing_hashcodes(self.map1, self.map2))
    self.assertEqual(len(missing), 0)

  def test01random_identical(self):
    ''' Fill map1 and map2 with the same fandom blocks.
    '''
    for n in range(32):
      data = randblock(rand0(8192))
      h1 = self.map1.add(data)
      h2 = self.map2.add(data)
      self.assertEqual(h1, h2)
    missing = list(missing_hashcodes(self.map1, self.map2))
    self.assertEqual(len(missing), 0)

  def test02random1only(self):
    ''' Fill map1 with random blocks, nothing in map2.
    '''
    for n in range(32):
      data = randblock(rand0(8192))
      h1 = self.map1.add(data)
    missing = list(missing_hashcodes(self.map1, self.map2))
    self.assertEqual(len(missing), 0)

  def test03random2only(self):
    ''' Fill map2 with random blocks, nothing in map1.
    '''
    ks2 = set()
    for n in range(32):
      data = randblock(rand0(8192))
      h2 = self.map2.add(data)
      ks2.add(h2)
    missing = list(missing_hashcodes(self.map1, self.map2))
    self.assertEqual(len(missing), len(ks2))

  def test03random_mixed(self):
    ''' Fill both maps with some overlap.
    '''
    ks1 = set()
    ks2 = set()
    for n in range(32):
      data = randblock(rand0(8192))
      choice = randint(0, 2)
      if choice <= 1:
        h1 = self.map1.add(data)
        ks1.add(h1)
      if choice >= 1:
        h2 = self.map2.add(data)
        ks2.add(h2)
    for window_size in 1, 7, 23, 1024:
      with self.subTest(window_size=window_size):
        # items in map1 not in map2
        missing = set(missing_hashcodes(self.map2, self.map1, window_size=window_size))
        self.assertEqual(missing, ks1 - ks2)
        # items in map2 not in map1
        missing = set(missing_hashcodes(self.map1, self.map2, window_size=window_size))
        self.assertEqual(missing, ks2 - ks1)

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('__main__')
