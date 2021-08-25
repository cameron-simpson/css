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
from cs.buffer import CornuCopyBuffer
##from cs.debug import thread_dump
from cs.randutils import rand0, make_randblock
from . import datafile
from .datafile import DataRecord, DataFilePushable
# from .hash_tests import _TestHashCodeUtils
# TODO: run _TestHashCodeUtils on DataDirs as separate test suite?

# arbitrary limit
MAX_BLOCK_SIZE = 16383
RUN_SIZE = 100

class TestDataFilePacketFields(_TestPacketFields, unittest.TestCase):
  ''' Test the `PacketField`s.
  '''

  def setUp(self):
    self.module = datafile

class TestDataFile(unittest.TestCase):
  ''' Tests for `DataFile`.
  '''

  def setUp(self):
    random.seed()
    tfd, pathname = tempfile.mkstemp(
        prefix="datafile-test", suffix=".vtd", dir='.'
    )
    os.close(tfd)
    self.pathname = pathname
    self.rdatafile = DataFilePushable(pathname)

  def tearDown(self):
    os.remove(self.pathname)

  # TODO: tests:
  #   scan datafile

  def test_shuffled_randomblocks(self):
    ''' Save RUN_SIZE random blocks, close, retrieve in random order.
    '''
    # save random blocks to a file
    blocks = {}
    with open(self.pathname, 'wb') as f:
      for n in range(RUN_SIZE):
        with self.subTest(put_block_n=n):
          data = make_randblock(rand0(MAX_BLOCK_SIZE + 1))
          dr = DataRecord(data)
          offset = f.tell()
          blocks[offset] = data
          f.write(bytes(dr))
    # shuffle the block offsets
    offsets = list(blocks.keys())
    random.shuffle(offsets)
    # retrieve the blocks in random order, check for correct content
    with open(self.pathname, 'rb') as f:
      for n, offset in enumerate(offsets):
        with self.subTest(shuffled_offsets_n=n, offset=offset):
          f.seek(offset)
          bfr = CornuCopyBuffer.from_file(f)
          dr = DataRecord.parse(bfr)
          data = dr.data
          self.assertTrue(data == blocks[offset])

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
