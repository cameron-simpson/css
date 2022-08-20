#!/usr/bin/python
#
# Pushpull tests. - Cameron Simpson <cs@cskk.id.au> (formerly cs@zip.com.au) 18dec2015
#

import unittest
from random import randint
from cs.randutils import rand0, make_randblock
from cs.x import X
from .hash import HashUtilDict
from .pushpull import missing_hashcodes, missing_hashcodes_by_checksum
from cs.x import X
import cs.x

cs.x.X_via_tty = True

class _TestMissingHashCodes:
  ''' Base class for testing missing hashcodes.
  '''

  def setUp(self):
    self.map1 = HashUtilDict()
    self.map2 = HashUtilDict()

  def test00empty(self):
    ''' Compare two empty maps.
    '''
    missing = list(self.miss_generator(self.map1, self.map2))
    self.assertEqual(len(missing), 0)

  def test01random_identical(self):
    ''' Fill map1 and map2 with identical some random blocks.
    '''
    for _ in range(32):
      data = make_randblock(rand0(8193))
      h1 = self.map1.add(data)
      h2 = self.map2.add(data)
      self.assertEqual(h1, h2)
    missing = list(self.miss_generator(self.map1, self.map2))
    self.assertEqual(len(missing), 0)

  def test02random1only(self):
    ''' Fill map1 with random blocks, nothing in map2.
    '''
    for n in range(32):
      data = make_randblock(rand0(8193))
      h1 = self.map1.add(data)
    missing = list(self.miss_generator(self.map1, self.map2))
    self.assertEqual(len(missing), 0)

  def test03random2only(self):
    ''' Fill map2 with random blocks, nothing in map1.
    '''
    ks2 = set()
    for n in range(32):
      data = make_randblock(rand0(8193))
      h2 = self.map2.add(data)
      ks2.add(h2)
    missing = list(self.miss_generator(self.map1, self.map2))
    self.assertEqual(len(missing), len(ks2))

  def test04random_mixed(self):
    ''' Fill both maps with some overlap.
    '''
    ks1 = set()
    ks2 = set()
    for n in range(32):
      data = make_randblock(rand0(8193))
      choice = randint(0, 2)
      if choice <= 1:
        h1 = self.map1.add(data)
        ks1.add(h1)
      if choice >= 1:
        h2 = self.map2.add(data)
        ks2.add(h2)
    for window_size in 1, 7, 16, 23, 32, 1024:
      with self.subTest(window_size=window_size):
        # items in map1 not in map2
        missing = set(
            self.miss_generator(self.map2, self.map1, window_size=window_size)
        )
        self.assertEqual(missing, ks1 - ks2)
        # items in map2 not in map1
        missing = set(
            self.miss_generator(self.map1, self.map2, window_size=window_size)
        )
        self.assertEqual(missing, ks2 - ks1)

class TestMissingHashCodes_Missing_hashcodes(_TestMissingHashCodes,
                                             unittest.TestCase):
  ''' Test basic missing hashcodes function.
  '''

  def __init__(self, *a, **kw):
    self.miss_generator = missing_hashcodes
    unittest.TestCase.__init__(self, *a, **kw)

class TestMissingHashCodes_Missing_hashcodes_checksum(_TestMissingHashCodes,
                                                      unittest.TestCase):
  ''' Test checksum based missing hashcodes function.
  '''

  def __init__(self, *a, **kw):
    self.miss_generator = missing_hashcodes_by_checksum
    unittest.TestCase.__init__(self, *a, **kw)

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('__main__')
