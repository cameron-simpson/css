#!/usr/bin/python
#
# Self tests for cs.venti.datadir.
# - Cameron Simpson <cs@zip.com.au>
#

import os
from os.path import abspath
import random
import shutil
import sys
import tempfile
import unittest
from cs.randutils import rand0, randblock
from .datadir import DataDir, DataDir_from_spec, \
                      encode_index_entry, decode_index_entry
from .datadir import INDEXCLASS_BY_NAME
from .hash import HASHCLASS_BY_NAME

MAX_BLOCK_SIZE = 16383
RUN_SIZE = 100

def mktmpdir(flavour=None):
  ''' Create a temporary scratch directory.
  '''
  prefix = "cs.venti.datafile.testdir"
  if flavour is not None:
    prefix += '-' + flavour
  return abspath(
           tempfile.mkdtemp(
             prefix="cs.venti.datafile.testdir",
             suffix=".dir",
             dir='.'))

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
    D = self.datadir
    with D:
      hashfunc = D.hashclass.from_chunk
      by_hash = {}
      by_data = {}
      # store RUN_SIZE random blocks
      for n in range(RUN_SIZE):
        with self.subTest(store_block_n=n):
          data = randblock(rand0(MAX_BLOCK_SIZE + 1))
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
    # explicitly close the DataDir and reopen
    # this is because the test framework normally does the outermost open/close
    # and therefore the datadir index lock is still sitting aroung
    D.close()
    D = self.datadir = DataDir_from_spec(datadir_spec)
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
  for hashname in sorted(HASHCLASS_BY_NAME.keys()):
    hashclass = HASHCLASS_BY_NAME[hashname]
    for indexname in sorted(INDEXCLASS_BY_NAME.keys()):
      indexclass = INDEXCLASS_BY_NAME[indexname]
      suite.addTest(multitest_suite(TestDataDir, hashclass=hashclass, indexclass=indexclass))
  runner = unittest.TextTestRunner(failfast=True, verbosity=2)
  runner.run(suite)
  ##if False:
  ##  import cProfile
  ##  cProfile.runctx('unittest.main(__name__, None, argv)', globals(), locals())
  ##else:
  ##  unittest.main(__name__, None, argv)
  ##thread_dump()

if __name__ == '__main__':
  selftest(sys.argv)
