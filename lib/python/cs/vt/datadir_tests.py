#!/usr/bin/python
#
# Datadir tests.
# - Cameron Simpson <cs@cskk.id.au>
#

import os
from os.path import abspath
import random
import shutil
import sys
import tempfile
import unittest
from .randutils import rand0, randblock
from .datadir import DataDir, RawDataDir
from .hash import HASHCLASS_BY_NAME
from .index import class_names as indexclass_names, class_by_name as indexclass_by_name
from cs.logutils import setup_logging
from cs.x import X
import cs.x
cs.x.X_via_tty = True

MAX_BLOCK_SIZE = 16383
RUN_SIZE = 100

def mktmpdir(flavour=None):
  ''' Create a temporary scratch directory.
  '''
  prefix = "datadir-test"
  if flavour is not None:
    prefix += '-' + flavour
  return abspath(
      tempfile.mkdtemp(prefix="datadir-test", suffix=".dir", dir='.')
  )

class TestDataDir(unittest.TestCase):

  ##MAP_FACTORY = lambda self: DataDir(mktmpdir(), mktmpdir(), self.hashclass, self.indexclass)

  def __init__(self, *a, **kw):
    a = list(a)
    method_name = a.pop()
    if a:
      raise ValueError("unexpected arguments: %r" % (a,))
    self.datadirclass = None
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
    self.datadir = self._open_default_datadir()
    self.datadir.open()
    random.seed()

  def _open_default_datadir(self):
    return self.datadirclass(
        self.indexdirpath,
        self.hashclass,
        indexclass=self.indexclass,
        rollover=self.rollover
    )

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
    index_entry_class = self.datadirclass.index_entry_class
    for _ in range(RUN_SIZE):
      rand_n = random.randint(0, 65536)
      rand_offset = random.randint(0, 65536)
      entry = self.datadir.index_entry(rand_n, rand_offset, 0)
      self.assertEqual(entry.filenum, rand_n)
      self.assertEqual(entry.offset, rand_offset)
      encoded = entry.encode()
      self.assertIsInstance(encoded, bytes)
      entry2 = index_entry_class.from_bytes(encoded)
      self.assertEqual(entry, entry2)
      self.assertEqual(entry2.filenum, rand_n)
      self.assertEqual(entry2.offset, rand_offset)

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
    # explicitly close the DataDir and reopen
    # this is because the test framework normally does the outermost open/close
    # and therefore the datadir index lock is still sitting aroung
    D.close()
    D = self.datadir = self._open_default_datadir()
    D.open()
    # reopen the DataDir
    with D:
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
    for indexname in sorted(indexclass_names()):
      indexclass = indexclass_by_name(indexname)
      for datadirclass in DataDir, RawDataDir:
        suite.addTest(
            multitest_suite(
                TestDataDir,
                datadirclass=datadirclass,
                hashclass=hashclass,
                indexclass=indexclass
            )
        )
  runner = unittest.TextTestRunner(failfast=True, verbosity=2)
  runner.run(suite)
  ##if False:
  ##  import cProfile
  ##  cProfile.runctx('unittest.main(__name__, None, argv)', globals(), locals())
  ##else:
  ##  unittest.main(__name__, None, argv)
  ##thread_dump()

if __name__ == '__main__':
  setup_logging(sys.argv[0])
  selftest(sys.argv)
