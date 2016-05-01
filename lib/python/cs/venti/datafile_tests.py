#!/usr/bin/python
#
# Self tests for cs.venti.datafile.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
from os.path import abspath
import sys
import random
import shutil
import tempfile
import unittest
from cs.logutils import X
from cs.randutils import rand0, randblock
from .datafile import DataFile, GDBMDataDirMapping, KyotoDataDirMapping, \
                DataDirMapping_from_spec, encode_index_entry, decode_index_entry
from .hash import DEFAULT_HASHCLASS
from .hash_tests import _TestHashCodeUtils

# arbitrary limit
MAX_BLOCK_SIZE = 16383
RUN_SIZE = 100

def mktmpdir():
  return abspath(tempfile.mkdtemp(prefix="cs.venti.datafile.testdir", suffix=".dir", dir='.'))

class TestDataFile(unittest.TestCase):

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
    self.datafile.put(randblock(rand0(MAX_BLOCK_SIZE+1)))

  def test01fetch1(self):
    ''' Save and the retrieve a single block.
    '''
    data = randblock(rand0(MAX_BLOCK_SIZE+1))
    self.datafile.put(data)
    data2 = self.datafile.get(0)
    self.assertEqual(data, data2)

  def test02randomblocks(self):
    ''' Save RUN_SIZE random blocks, close, retrieve in random order.
    '''
    blocks = {}
    for n in range(RUN_SIZE):
      with self.subTest(put_block_n=n):
        data = randblock(rand0(MAX_BLOCK_SIZE+1))
        offset = self.datafile.put(data)
        blocks[offset] = data
    offsets = list(blocks.keys())
    random.shuffle(offsets)
    for n, offset in enumerate(offsets):
      with self.subTest(shuffled_offsets_n=n, offset=offset):
        data = self.datafile.get(offset)
        self.assertTrue(data == blocks[offset])

class _TestDataDirMapping:

  MAPPING_CLASS = None

  def setUp(self):
    mapping_class = self.__class__.MAPPING_CLASS
    if mapping_class is None:
      raise unittest.SkipTest("MAPPING_CLASS is None, skipping TestCase")
    random.seed()
    self.pathname = mktmpdir()
    self.datadir = mapping_class(self.pathname, rollover=200000)

  def tearDown(self):
    ##os.system("ls -l "+self.pathname)
    shutil.rmtree(self.pathname)

  def test000IndexEntry(self):
    ''' Test roundtrip of index entry encode/decode.
    '''
    for count in range(RUN_SIZE):
      rand_n = random.randint(0, 65536)
      rand_offset = random.randint(0, 65536)
      n, offset = decode_index_entry(encode_index_entry(rand_n, rand_offset))
      self.assertEqual(rand_n, n)
      self.assertEqual(rand_offset, offset)

  def test001datadir_spec(self):
    # force creation of index file
    with self.datadir:
      self.datadir.add(b'')
    datadir_spec = self.datadir.spec()
    D2 = DataDirMapping_from_spec(datadir_spec)
    self.assertEqual(datadir_spec, D2.spec())
    D2 = DataDirMapping_from_spec(self.datadir.dirpath)
    self.assertEqual(datadir_spec, D2.spec())
    for indexname in 'gdbm', 'kyoto':
      for hashname in 'sha1',:
        spec = '%s:%s:%s' % (indexname, hashname, self.datadir.dirpath)
        D3 = DataDirMapping_from_spec(spec)

  def test002randomblocks(self):
    ''' Save random blocks, retrieve in random order.
    '''
    hashclass = DEFAULT_HASHCLASS
    hashfunc = hashclass.from_data
    with self.datadir as D:
      by_hash = {}
      by_data = {}
      # store RUN_SIZE random blocks
      for n in range(RUN_SIZE):
        with self.subTest(store_block_n=n):
          data = randblock(rand0(MAX_BLOCK_SIZE+1))
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
      datadir_spec = D.spec()
    D2 = DataDirMapping_from_spec(datadir_spec)
    self.assertEqual(datadir_spec, D2.spec())
    # reopen the DataDir
    with self.__class__.MAPPING_CLASS(self.pathname, rollover=200000) as D:
      self.assertEqual(datadir_spec, D.spec())
      hashcodes = list(by_hash.keys())
      random.shuffle(hashcodes)
      for n, hashcode in enumerate(hashcodes):
        with self.subTest(n=n, reprobe_hashcode=hashcode):
          self.assertTrue(hashcode in by_hash)
          self.assertTrue(hashcode in D)
          odata = by_hash[hashcode]
          data = D[hashcode]
          self.assertEqual(data, odata)

class TestDataDirMappingGDBM(_TestDataDirMapping, unittest.TestCase):
  MAPPING_CLASS = GDBMDataDirMapping

class TestHashCodeUtilsGDBM(_TestHashCodeUtils, unittest.TestCase):
  MAP_FACTORY = lambda self: GDBMDataDirMapping(mktmpdir())

class TestDataDirMappingKyoto(_TestDataDirMapping, unittest.TestCase):
  MAPPING_CLASS = KyotoDataDirMapping

class TestHashCodeUtilsKyoto(_TestHashCodeUtils, unittest.TestCase):
  MAP_FACTORY = lambda self: KyotoDataDirMapping(mktmpdir())

def selftest(argv):
  if False:
    import cProfile
    cProfile.runctx('unittest.main(__name__, None, argv)', globals(), locals())
  else:
    unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
