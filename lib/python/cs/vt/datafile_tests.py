#!/usr/bin/env python3
#
# Datafile tests.
# - Cameron Simpson <cs@cskk.id.au>
#

from contextlib import contextmanager
import os
import random
import sys
from tempfile import NamedTemporaryFile
import unittest

try:
  import kyotocabinet
except ImportError:
  kyotocabinet = None

from cs.binary_tests import BaseTestBinaryClasses
from cs.buffer import CornuCopyBuffer
##from cs.debug import thread_dump
from cs.randutils import rand0, make_randblock
from cs.testutils import SetupTeardownMixin

from . import datafile
from .datafile import DataRecord, DataFilePushable

# from .hash_tests import _TestHashCodeUtils
# TODO: run _TestHashCodeUtils on DataDirs as separate test suite?

# arbitrary limit
MAX_BLOCK_SIZE = 16383
RUN_SIZE = 100

class TestDataFileBinaryClasses(BaseTestBinaryClasses, unittest.TestCase):
  ''' Test the `AbstractBinary` subclasses.
  '''
  test_module = datafile

class TestDataFile(SetupTeardownMixin, unittest.TestCase):
  ''' Tests for `DataFile`.
  '''

  @contextmanager
  def setupTeardown(self):
    random.seed()
    with NamedTemporaryFile(prefix="datafile-test", suffix=".vtd",
                            dir='.') as T:
      self.pathname = T.name
      self.rdatafile = DataFilePushable(self.pathname)
      yield

  # TODO: tests:
  #   scan datafile

  def test_shuffled_randomblocks(self):
    ''' Save RUN_SIZE random blocks, close, retrieve in random order.
    '''
    # save random blocks to a file
    blocks_by_offset = {}
    with open(self.pathname, 'wb') as f:
      for n in range(RUN_SIZE):
        with self.subTest(put_block_n=n):
          data = make_randblock(rand0(MAX_BLOCK_SIZE + 1))
          dr = DataRecord(data)
          offset = f.tell()
          blocks_by_offset[offset] = data
          f.write(bytes(dr))
    # shuffle the block offsets
    offsets = list(blocks_by_offset.keys())
    random.shuffle(offsets)
    # retrieve the blocks in random order, check for correct content
    with open(self.pathname, 'rb') as f:
      for n, offset in enumerate(offsets):
        with self.subTest(shuffled_offsets_n=n, offset=offset):
          f.seek(offset)
          bfr = CornuCopyBuffer.from_file(f)
          dr = DataRecord.parse(bfr)
          data = dr.data
          self.assertTrue(data == blocks_by_offset[offset])

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
