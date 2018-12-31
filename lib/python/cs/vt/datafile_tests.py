#!/usr/bin/python
#
# Datafile tests.
# - Cameron Simpson <cs@cskk.id.au>
#

import os
import sys
import random
import tempfile
import unittest
try:
  import kyotocabinet
except ImportError:
  kyotocabinet = None
from cs.binary_tests import _TestPacketFields
##from cs.debug import thread_dump
from .randutils import rand0, randblock
from . import datafile
from .datafile import DataFileReader, DataFileWriter
# from .hash_tests import _TestHashCodeUtils
# TODO: run _TestHashCodeUtils on DataDirs as separate test suite?

# arbitrary limit
MAX_BLOCK_SIZE = 16383
RUN_SIZE = 100

class TestDataFilePacketFields(_TestPacketFields, unittest.TestCase):

  def setUp(self):
    self.module = datafile

class TestDataFile(unittest.TestCase):

  def setUp(self):
    random.seed()
    tfd, pathname = tempfile.mkstemp(prefix="datafile-test", suffix=".vtd", dir='.')
    os.close(tfd)
    self.pathname = pathname
    self.wdatafile = DataFileWriter(pathname)
    self.wdatafile.open()
    self.rdatafile = DataFileReader(pathname)
    self.rdatafile.open()

  def tearDown(self):
    self.wdatafile.close()
    self.rdatafile.close()
    os.remove(self.pathname)

  # TODO: tests:
  #   scan datafile

  def test00store1(self):
    ''' Save a single block.
    '''
    self.wdatafile.add(randblock(rand0(MAX_BLOCK_SIZE + 1)))

  def test01fetch1(self):
    ''' Save and the retrieve a single block.
    '''
    data = randblock(rand0(MAX_BLOCK_SIZE + 1))
    self.wdatafile.add(data)
    data2 = self.rdatafile.fetch(0)
    self.assertEqual(data, data2)

  def test02randomblocks(self):
    ''' Save RUN_SIZE random blocks, close, retrieve in random order.
    '''
    blocks = {}
    for n in range(RUN_SIZE):
      with self.subTest(put_block_n=n):
        data = randblock(rand0(MAX_BLOCK_SIZE + 1))
        offset, offset2 = self.wdatafile.add(data)
        blocks[offset] = data
    offsets = list(blocks.keys())
    random.shuffle(offsets)
    for n, offset in enumerate(offsets):
      with self.subTest(shuffled_offsets_n=n, offset=offset):
        data = self.rdatafile.fetch(offset)
        self.assertTrue(data == blocks[offset])

def multitest_suite(testcase_class, *a, **kw):
  suite = unittest.TestSuite()
  for method_name in dir(testcase_class):
    if method_name.startswith('test'):
      ta = list(a) + [method_name]
      suite.addTest(testcase_class(*ta, **kw))
  return suite

def selftest(argv):
  unittest.main(__name__, None, argv)
  ##suite = unittest.TestSuite()
  ##suite.addTest(multitest_suite(TestDataFile))
  ##suite.addTest(TestDataFilePacketFields())
  ##runner = unittest.TextTestRunner(failfast=True, verbosity=2)
  ##runner.run(suite)
  ##if False:
  ##  import cProfile
  ##  cProfile.runctx('unittest.main(__name__, None, argv)', globals(), locals())
  ##else:
  ##  unittest.main(__name__, None, argv)
  ##thread_dump()

if __name__ == '__main__':
  selftest(sys.argv)
