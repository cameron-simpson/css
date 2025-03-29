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
from .datafile import DataRecord, DataFile, DataFilePushable

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
      yield

  # TODO: tests:
  #   scan datafile

  def test_shuffled_randomblocks(self):
    ''' Save RUN_SIZE random blocks, close, retrieve in random order.
    '''
    # save random blocks to a file
    blocks_by_offset = {}
    with DataFile(self.pathname) as DF:
      added = DF.extend(
          make_randblock(rand0(MAX_BLOCK_SIZE + 1)) for _ in range(RUN_SIZE)
      )
      prev_offset = None
      prev_length = None
      for DR, offset, length in added:
        assert offset >= 0
        assert length > 0
        if prev_offset is not None:
          self.assertEqual(offset, prev_offset + prev_length)
        blocks_by_offset[offset] = DR.data
        prev_offset = offset
        prev_length = length
    # shuffle the block offsets
    offsets = list(blocks_by_offset.keys())
    random.shuffle(offsets)
    # retrieve the blocks in random order, check for correct content
    for offset in offsets:
      DR = DF[offset]
      self.assertEqual(DR.data, blocks_by_offset[offset])

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
