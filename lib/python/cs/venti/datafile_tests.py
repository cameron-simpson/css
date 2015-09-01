#!/usr/bin/python
#
# Self tests for cs.venti.datafile.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import sys
import random
import shutil
import tempfile
import unittest
from unittest import TestCase, skip
from cs.logutils import D, X
from cs.randutils import rand0, randblock
from .datafile import DataFile, DataDir
from .hash import DEFAULT_HASHCLASS

# arbitrary limit
MAX_BLOCK_SIZE = 16383
RUN_SIZE = 100

class TestDataFile(TestCase):

  def setUp(self):
    random.seed()
    tfd, pathname = tempfile.mkstemp(prefix="cs.venti.datafile.test", suffix=".vtd", dir='.')
    os.close(tfd)
    self.pathname = pathname
    self.datafile = DataFile(pathname)
    self.datafile.open()

  def tearDown(self):
    self.datafile.close()
    os.remove(self.pathname)

  # TODO: tests:
  #   scan datafile

  def test00store1(self):
    ''' Save a single block.
    '''
    self.datafile.put(randblock(rand0(MAX_BLOCK_SIZE)))

  def test01fetch1(self):
    ''' Save and the retrieve a single block.
    '''
    data = randblock(rand0(MAX_BLOCK_SIZE))
    self.datafile.put(data)
    data2 = self.datafile.get(0)
    self.assertEqual(data, data2)

  def test02randomblocks(self):
    ''' Save RUN_SIZE random blocks, close, retrieve in random order.
    '''
    blocks = {}
    for n in range(RUN_SIZE):
      with self.subTest(put_block_n=n):
        data = randblock(rand0(MAX_BLOCK_SIZE))
        offset = self.datafile.put(data)
        blocks[offset] = data
    offsets = list(blocks.keys())
    random.shuffle(offsets)
    for n, offset in enumerate(offsets):
      with self.subTest(shuffled_offsets_n=n, offset=offset):
        data = self.datafile.get(offset)
        self.assertTrue(data == blocks[offset])

class TestDataDir(TestCase):

  def setUp(self):
    random.seed()
    self.pathname = tempfile.mkdtemp(prefix="cs.venti.datafile.testdir", suffix=".dir", dir='.')
    self.datadir = DataDir(self.pathname, rollover=200000)

  def tearDown(self):
    shutil.rmtree(self.pathname)

  def test000IndexEntry(self):
    ''' Test roundtrip of index entry encode/decode.
    '''
    for count in range(RUN_SIZE):
      rand_n = random.randint(0, 65536)
      rand_offset = random.randint(0, 65536)
      n, offset = DataDir.decodeIndexEntry(DataDir.encodeIndexEntry(rand_n, rand_offset))
      self.assertEqual(rand_n, n)
      self.assertEqual(rand_offset, offset)

  def test001randomblocks(self):
    ''' Save random blocks, retrieve in random order.
    '''
    hashclass = DEFAULT_HASHCLASS
    hashfunc = hashclass.from_data
    with self.datadir as D:
      D = self.datadir
      by_hash = {}
      by_data = {}
      # store RUN_SIZE random blocks
      for n in range(RUN_SIZE):
        with self.subTest(store_block_n=n):
          data = randblock(rand0(MAX_BLOCK_SIZE))
          if data in by_data:
            continue
          hashcode = hashfunc(data)
          # test integrity first
          self.assertFalse(hashcode in by_hash)
          self.assertFalse(data in by_data)
          self.assertFalse(hashcode in D)
          # store block/hashcode
          by_hash[hashcode] = data
          by_data[data] = hashcode
          D[hashcode] = data
          # test integrity afterwards
          self.assertTrue(hashcode in by_hash)
          self.assertTrue(data in by_data)
          self.assertTrue(hashcode in D)
      # now retrieve in random order
      hashcodes = list(by_hash.keys())
      random.shuffle(hashcodes)
      for hashcode in hashcodes:
        with self.subTest(probe_hashcode=hashcode):
          self.assertTrue(hashcode in by_hash)
          self.assertTrue(hashcode in D)
          odata = by_hash[hashcode]
          data = D[hashcode]
          self.assertEqual(data, odata)
    # reopen the DataDir
    with DataDir(self.pathname, rollover=200000) as D:
      hashcodes = list(by_hash.keys())
      random.shuffle(hashcodes)
      for n, hashcode in enumerate(hashcodes):
        with self.subTest(n=n, reprobe_hashcode=hashcode):
          self.assertTrue(hashcode in by_hash)
          self.assertTrue(hashcode in D)
          odata = by_hash[hashcode]
          data = D[hashcode]
          self.assertEqual(data, odata)

def selftest(argv):
  if False:
    import cProfile
    cProfile.runctx('unittest.main(__name__, None, argv)', globals(), locals())
  else:
    unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
