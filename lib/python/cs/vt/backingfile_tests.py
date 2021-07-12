#!/usr/bin/env python3
# - Cameron Simpson <cs@cskk.id.au>

''' BackingFile tests.
'''

import random
import sys
from tempfile import TemporaryDirectory, NamedTemporaryFile
import unittest
from cs.randutils import rand0, make_randblock
from .backingfile import (
    RawBackingFile, CompressibleBackingFile, VTDStore, BinaryHashCodeIndex,
    BackingFileIndexEntry
)
from .hash import HASHCLASS_BY_NAME

RUN_SIZE = 128
MAX_BLOCK_SIZE = 65536  # should exercise 1, 2 and 3 bytes data length prefixes

class TestBackingFile(unittest.TestCase):
  ''' Unit tests for backing files.
  '''

  def test_shuffled_randomblocks(self):
    ''' Save RUN_SIZE random blocks, close, retrieve in random order.
    '''
    for cls in RawBackingFile, CompressibleBackingFile:
      for _, hashclass in sorted(HASHCLASS_BY_NAME.items()):
        with self.subTest(cls=cls, hashclass=hashclass):
          with NamedTemporaryFile(dir='.', prefix=cls.__name__ + '-') as T:
            blocks = {}
            index = BinaryHashCodeIndex(
                hashclass=hashclass,
                binary_index={},
                index_entry_class=BackingFileIndexEntry
            )
            total_length = 0
            # open and save data
            with cls(T.name, hashclass=hashclass, index=index) as bf:
              for _ in range(RUN_SIZE):
                data = make_randblock(rand0(MAX_BLOCK_SIZE + 1))
                h = bf.add(data)
                blocks[h] = data
                total_length += len(data)
            # reopen and retrieve
            with cls(T.name, hashclass=hashclass, index=index) as bf:
              # retrieve in random order
              hashcodes = list(blocks.keys())
              random.shuffle(hashcodes)
              for h in hashcodes:
                data = bf[h]
                self.assertEqual(data, blocks[h])

  def test_shuffled_randomblocks_vtd(self):
    ''' Like test_shuffled_randomblocks but using a .vtd file and binary index file:
        save RUN_SIZE random blocks, close, retrieve in random order.
    '''
    for _, hashclass in sorted(HASHCLASS_BY_NAME.items()):
      with self.subTest(hashclass=hashclass):
        with TemporaryDirectory(dir='.') as TDname:
          with NamedTemporaryFile(dir=TDname, prefix='VTDStore-',
                                  suffix='.vtd') as T:
            blocks = {}
            total_length = 0
            # open and save data
            with VTDStore(T.name, T.name, hashclass=hashclass) as S:
              for _ in range(RUN_SIZE):
                data = make_randblock(rand0(MAX_BLOCK_SIZE + 1))
                h = S.add(data)
                blocks[h] = data
                total_length += len(data)
            # reopen and retrieve
            with VTDStore(T.name, T.name, hashclass=hashclass) as S:
              # retrieve in random order
              hashcodes = list(blocks.keys())
              random.shuffle(hashcodes)
              for h in hashcodes:
                data = S[h]
                self.assertEqual(data, blocks[h])

def selftest(argv):
  ''' Run unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
