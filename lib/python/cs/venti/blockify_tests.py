#!/usr/bin/python
#
# Self tests for cs.venti.blockify.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import time
import unittest
from unittest import skip
from cs.logutils import D, X
from cs.randutils import rand0, randblock
from .blockify import blockify, blocked_chunks_of, \
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
    for parser in ():
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
    for parser in (None,):
      with self.subTest('None' if parser is None else parser.__name__):
        rand_total = 0
        chunk_total = 0
        nchunks = 0
        X("prepare random input blocks")
        source_chunks = list(random_blocks(max_size=12000, count=1280))
        all_chunks = []
        X("scan and block...")
        start_time = time.time()
        for chunk in blocked_chunks_of(source_chunks, parser):
          ##X("BLOCKED_CHUNK len=%d", len(chunk))
          nchunks += 1
          chunk_total += len(chunk)
          all_chunks.append(chunk)
          # the pending.flush operation can return short blocks
          ##self.assertTrue(len(chunk) >= MIN_BLOCKSIZE,
          ##                "len(chunk)=%d < MIN_BLOCKSIZE=%d"
          ##                % (len(chunk), MIN_BLOCKSIZE))
          self.assertTrue(len(chunk) <= MAX_BLOCKSIZE,
                          "len(chunk)=%d > MAX_BLOCKSIZE=%d"
                          % (len(chunk), MAX_BLOCKSIZE))
          self.assertTrue(chunk_total <= rand_total,
                          "chunk_total:%d > rand_total:%d"
                          % (chunk_total, rand_total))
        end_time = time.time()
        X("%d chunks in %gs, %d bytes at %g B/s",
          nchunks, end_time-start_time, chunk_total,
          float(chunk_total) / (end_time-start_time))
        self.assertEqual(rand_total, chunk_total)
        self.assertEqual(b''.join(source_chunks),
                         b''.join(all_chunks))

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
