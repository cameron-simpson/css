#!/usr/bin/python
#
# Self tests for cs.venti.blockify.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from unittest import skip
from cs.logutils import D, X
from cs.randutils import rand0, randblock
from .blockify import blockify, rolling_hash_parser, blocked_chunks_of, \
                      blocks_of, MIN_BLOCKSIZE, MAX_BLOCKSIZE
from .cache import MemoryCacheStore

def random_blocks(max_size=65536, count=64):
  ''' Generate `count` blocks of random sizes from 1 to `max_size`.
  '''
  global rand_total
  for i in range(count):
    size = rand0(max_size) + 1
    yield randblock(size)
    rand_total += size

class TestAll(unittest.TestCase):

  def setUp(self):
    self.fp = open(__file__, "rb")

  def tearDown(self):
    self.fp.close()

  def test01parsers(self):
    global rand_total
    for parser in rolling_hash_parser,:
      with self.subTest(parser.__name__):
        rand_total = 0
        offsetQ = parser(random_blocks())
        chunkQ = next(offsetQ)
        offset = 0
        last_qoffset = 0
        for qoffset in offsetQ:
          self.assertTrue(last_qoffset < qoffset, "qoffset %d <= last_qoffset %d" % (qoffset, last_qoffset))
          while offset < qoffset:
            chunk = next(chunkQ)
            self.assertTrue(len(chunk) > 0)
            offset += len(chunk)
        self.assertEqual(rand_total, offset)
        self.assertRaises(StopIteration, next, chunkQ)

  def test02blocked_chunks_of(self):
    global rand_total
    for parser in rolling_hash_parser,:
      with self.subTest(parser.__name__):
        rand_total = 0
        chunk_total = 0
        for chunk in blocked_chunks_of(random_blocks(), parser):
          chunk_total += len(chunk)
          self.assertTrue(len(chunk) >= MIN_BLOCKSIZE,
                          "len(chunk)=%d < MIN_BLOCKSIZE=%d"
                          % (len(chunk), MIN_BLOCKSIZE))
          self.assertTrue(len(chunk) <= MAX_BLOCKSIZE,
                          "len(chunk)=%d > MAX_BLOCKSIZE=%d"
                          % (len(chunk), MAX_BLOCKSIZE))
        self.assertEqual(rand_total, chunk_total)

  def test03blockifyAndRetrieve(self):
    with MemoryCacheStore("TestAll.test00blockifyAndRetrieve"):
      data = self.fp.read()
      blocks = list(blockify([data]))
      ##X("blocks=%r", blocks)
      data2 = b''.join( b.data for b in blocks )
      self.assertEqual(len(data), len(data2), "data mismatch: len(data)=%d, len(data2)=%d" % (len(data), len(data2)))
      self.assertEqual(data, data2, "data mismatch: data and data2 same length but contents differ")
      ##for b in blocks: print("[", b.data, "]")

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
