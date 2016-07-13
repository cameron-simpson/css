#!/usr/bin/python
#
# Self tests for cs.venti.datafile.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
from os.path import abspath
import sys
from functools import partial
import random
import shutil
import tempfile
import unittest
try:
  import kyotocabinet
except ImportError:
  kyotocabinet = None
from cs.debug import thread_dump
import cs.logutils
cs.logutils.X_via_tty = True
from cs.logutils import X
from cs.randutils import rand0, randblock
from .datafile import DataFile, DataDir, DataDir_from_spec, \
                      encode_index_entry, decode_index_entry, \
                      INDEXCLASS_BY_NAME
from .hash import HASHCLASS_BY_NAME
from .hash_tests import _TestHashCodeUtils
# TODO: run _TestHashCodeUtils on DataDirs as separate test suite?

# arbitrary limit
MAX_BLOCK_SIZE = 16383
RUN_SIZE = 100

def mktmpdir(flavour=None):
  ''' Create a temporary scratch directory.
  '''
  prefix="cs.venti.datafile.testdir"
  if flavour is not None:
    prefix += '-' + flavour
  return abspath(
           tempfile.mkdtemp(
             prefix="cs.venti.datafile.testdir",
             suffix=".dir",
             dir='.'))

class TestDataFile(unittest.TestCase):

  def setUp(self):
    random.seed()
    tfd, pathname = tempfile.mkstemp(prefix="cs.venti.datafile.test", suffix=".vtd", dir='.')
    os.close(tfd)
    self.pathname = pathname
    self.datafile = DataFile(pathname, readwrite=True)
    self.datafile.open()

  def tearDown(self):
    self.datafile.close()
    os.remove(self.pathname)

  # TODO: tests:
  #   scan datafile

  def test00store1(self):
    ''' Save a single block.
    '''
    self.datafile.add(randblock(rand0(MAX_BLOCK_SIZE+1)))

  def test01fetch1(self):
    ''' Save and the retrieve a single block.
    '''
    data = randblock(rand0(MAX_BLOCK_SIZE+1))
    self.datafile.add(data)
    data2 = self.datafile.fetch(0)
    self.assertEqual(data, data2)

  def test02randomblocks(self):
    ''' Save RUN_SIZE random blocks, close, retrieve in random order.
    '''
    blocks = {}
    for n in range(RUN_SIZE):
      with self.subTest(put_block_n=n):
        data = randblock(rand0(MAX_BLOCK_SIZE+1))
        offset, offset2 = self.datafile.add(data)
        blocks[offset] = data
    offsets = list(blocks.keys())
    random.shuffle(offsets)
    for n, offset in enumerate(offsets):
      with self.subTest(shuffled_offsets_n=n, offset=offset):
        data = self.datafile.fetch(offset)
        self.assertTrue(data == blocks[offset])

class TestDataDir(unittest.TestCase):

  ##MAP_FACTORY = lambda self: DataDir(mktmpdir(), mktmpdir(), self.hashclass, self.indexclass)

  def __init__(self, *a, **kw):
    a = list(a)
    method_name = a.pop()
    if a:
      raise ValueError("unexpected arguments: %r" % (a,))
    self.indexdirpath = None
    self.datadirpath = None
    self.indexclass = None
    self.hashclass = None
    self.rollover = None
    self.__dict__.update(kw)
    unittest.TestCase.__init__(self, method_name)

  def setUp(self):
    if self.indexdirpath is None:
      self.indexdirpath = mktmpdir('indexstate')
      self.do_remove_indexdirpath = True
    else:
      self.do_remove_indexdirpath = False
    if self.datadirpath is None:
      self.datadirpath = mktmpdir('data')
      self.do_remove_datadirpath = True
    else:
      self.do_remove_datadirpath = False
    self.datadir = DataDir(self.indexdirpath,
                           self.datadirpath,
                           self.hashclass,
                           self.indexclass,
                           rollover=self.rollover)
    random.seed()
    self.datadir.open()

  def tearDown(self):
    self.datadir.close()
    os.system("ls -l -- " + self.datadirpath)
    if self.do_remove_datadirpath:
      shutil.rmtree(self.datadirpath)
    os.system("ls -l -- " + self.indexdirpath)
    if self.do_remove_indexdirpath:
      shutil.rmtree(self.indexdirpath)

  def test000IndexEntry(self):
    ''' Test roundtrip of index entry encode/decode.
    '''
    for count in range(RUN_SIZE):
      rand_n = random.randint(0, 65536)
      rand_offset = random.randint(0, 65536)
      n, offset = decode_index_entry(encode_index_entry(rand_n, rand_offset))
      self.assertEqual(rand_n, n)
      self.assertEqual(rand_offset, offset)

  def test002randomblocks(self):
    ''' Save random blocks, retrieve in random order.
    '''
    with self.datadir as D:
      hashfunc = D.hashclass.from_data
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
          ##X("D[]=%r", list(D.keys()))
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
    # explicitly close the DataDir and reopen
    # this is because the test framework normally does the outermost open/close
    # and therefore the datadir index lock is still sitting aroung
    D.close()
    D = DataDir_from_spec(datadir_spec)
    self.assertEqual(datadir_spec, D.spec())
    D.open()
    # reopen the DataDir
    with D:
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

def multitest_suite(testcase_class, *a, **kw):
  suite = unittest.TestSuite()
  for method_name in dir(testcase_class):
    if method_name.startswith('test'):
      ta = list(a) + [method_name]
      suite.addTest(testcase_class(*ta, **kw))
  return suite

def selftest(argv):
  suite = unittest.TestSuite()
  suite.addTest(multitest_suite(TestDataFile))
  for hashname in sorted(HASHCLASS_BY_NAME.keys()):
    hashclass = HASHCLASS_BY_NAME[hashname]
    for indexname in sorted(INDEXCLASS_BY_NAME.keys()):
      indexclass = INDEXCLASS_BY_NAME[indexname]
      suite.addTest(multitest_suite(TestDataDir, hashclass=hashclass, indexclass=indexclass))
  runner = unittest.TextTestRunner(failfast=True)
  runner.run(suite)
  ##if False:
  ##  import cProfile
  ##  cProfile.runctx('unittest.main(__name__, None, argv)', globals(), locals())
  ##else:
  ##  unittest.main(__name__, None, argv)
  thread_dump()

if __name__ == '__main__':
  selftest(sys.argv)
