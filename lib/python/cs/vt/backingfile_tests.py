#!/usr/bin/env python3
# - Cameron Simpson <cs@cskk.id.au>

''' BackingFile tests.
'''

import random
import sys
from tempfile import NamedTemporaryFile
import unittest
from cs.randutils import rand0, make_randblock
from .backingfile import RawBackingFile, CompressibleBackingFile
from .hash import HASHCLASS_BY_NAME

RUN_SIZE = 128
MAX_BLOCK_SIZE = 65536  # should exercise 1, 2 and 3 bytes data length prefixes

class TestBackingFile(unittest.TestCase):

  def test_shuffled_randomblocks(self):
    ''' Save RUN_SIZE random blocks, close, retrieve in random order.
    '''
    for cls in RawBackingFile, CompressibleBackingFile:
      for hashclass_name, hashclass in sorted(HASHCLASS_BY_NAME.items()):
        with self.subTest(cls=cls, hashclass=hashclass):
          with NamedTemporaryFile(dir='.', prefix=cls.__name__ + '-') as T:
            blocks = {}
            total_length = 0
            bf = cls(T.name, hashclass=hashclass, index={})
            for n in range(RUN_SIZE):
              data = make_randblock(rand0(MAX_BLOCK_SIZE + 1))
              h = bf.add(data)
              blocks[h] = data
              total_length += len(data)
            # retrieve in random order
            hashcodes = list(blocks.keys())
            random.shuffle(hashcodes)
            for h in hashcodes:
              data = bf[h]
              self.assertEqual(data, blocks[h])

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
