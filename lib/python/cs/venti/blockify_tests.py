#!/usr/bin/python
#
# Self tests for cs.venti.blockify.
#       - Cameron Simpson <cs@zip.com.au>
#

from collections import defaultdict
import os
import os.path
import sys
import time
import unittest
from unittest import skip
from cs.buffer import chunky
from cs.fileutils import read_from
from cs.logutils import D
from cs.randutils import rand0, randblock
from cs.x import X
from .blockify import blockify, blocked_chunks_of, \
                      blocks_of, MIN_BLOCKSIZE, MAX_BLOCKSIZE
from .cache import MemoryCacheStore
from .parsers import scan_text, scan_mp3, scan_mp4

QUICK = len(os.environ.get('QUICK', '')) > 0

def random_blocks(max_size=65536, count=64):
  ''' Generate `count` blocks of random sizes from 1 to `max_size`.
  '''
  for i in range(count):
    size = rand0(max_size) + 1
    yield randblock(size)

class TestAll(unittest.TestCase):

  # load my_code from this test suite
  with open(__file__, 'rb') as myfp:
    mycode = myfp.read()

  X("generate random test data")
  if QUICK:
    random_data = list(random_blocks(max_size=1200, count=12))
  else:
    random_data = list(random_blocks(max_size=12000, count=1280))

  def setUp(self):
    self.fp = open(__file__, "rb")

  def tearDown(self):
    self.fp.close()

  @skip
  def test01parsers(self):
    rand_total = sum(len(chunk) for chunk in random_data)
    for parser in (scan_text, scan_mp3):
      with self.subTest(parser.__name__):
        input_chunks = self.random_data
        if parser is scan_mp3:
          if os.path.exists('TEST.mp3'):
            ##X("mp3 parse: replace input data with chunks from TEST.mp3")
            input_chunks = read_from(open('TEST.mp3', 'rb'))
            input_desc = 'TEST.mp3'
          else:
            ##X("no TEST.mp3 in ".os.getcwd())
            pass
        Q = parser(input_chunks)
        offset = 0
        last_qoffset = 0
        for qitem in Q:
          if isinstance(qitem, int):
            qoffset = qitem
            self.assertTrue(last_qoffset < qoffset, "qoffset %d <= last_qoffset %d" % (qoffset, last_qoffset))
            last_qoffset = qoffset
          else:
            chunk = qitem
            self.assertTrue(len(chunk) > 0)
            offset += len(chunk)
        if input_chunks is self.random_data:
          self.assertEqual(rand_total, offset)

  def test02blocked_chunks_of(self):
    for parser in (
        ##None,
        ##scan_text,
        ##scan_mp3,
        scan_mp4,
      ):
      parser_desc = 'None' if parser is None else parser.__name__
      for input_desc, input_chunks in (
          ##('random data', self.random_data),
          ('100 x ' + __file__, [ self.mycode for _ in range(100) ]),
        ):
        testfile = None
        rfp = None
        if parser is scan_mp3:
          testfile = 'TEST.mp3'
        elif parser is scan_mp4:
          testfile = 'TEST.mp4'
        if testfile is not None:
          if os.path.exists(testfile):
            X("%s: replace input data with chunks from %s", parser, testfile)
            rfp = open(testfile, 'rb')
            input_chunks = read_from(rfp)
            input_desc = testfile
          else:
            X("%s: no %s in %s", parser, testfile, os.getcwd())
        with self.subTest("blocked_chunks_of",
                          parser=parser_desc,
                          source=input_desc):
          if True:
            source_chunks = input_chunks
            src_total = None
          else:
            source_chunks = list(input_chunks)
            src_total = 0
            for chunk in source_chunks:
              src_total += len(chunk)
            X("%d source chunks, %d bytes in total", len(source_chunks), src_total)
          chunk_total = 0
          nchunks = 0
          all_chunks = []
          start_time = time.time()
          offset = 0
          prev_chunk = None
          chunky_parser = chunky(parser) if parser else None
          histogram = defaultdict(int)
          for chunk in blocked_chunks_of(source_chunks, chunky_parser, histogram=histogram):
            nchunks += 1
            chunk_total += len(chunk)
            all_chunks.append(chunk)
            if prev_chunk is not None:
              # this avoids issues with the final block, which may be short
              self.assertTrue(len(prev_chunk) >= MIN_BLOCKSIZE,
                              "len(prev_chunk)=%d < MIN_BLOCKSIZE=%d"
                              % (len(prev_chunk), MIN_BLOCKSIZE))
            offset += len(chunk)
            # the pending.flush operation can return short blocks
            self.assertTrue(len(chunk) <= MAX_BLOCKSIZE,
                            "len(chunk)=%d > MAX_BLOCKSIZE=%d"
                            % (len(chunk), MAX_BLOCKSIZE))
            if src_total is not None:
              self.assertTrue(chunk_total <= src_total,
                              "chunk_total:%d > src_total:%d"
                              % (chunk_total, src_total))
            prev_chunk = chunk
          end_time = time.time()
          X("%s|%s: received %d chunks in %gs, %d bytes at %g B/s",
            input_desc, parser_desc,
            nchunks, end_time-start_time, chunk_total,
            float(chunk_total) / (end_time-start_time))
          X("    %d offsets from parser, %d offsets from hash scan",
            histogram['offsets_from_scanner'],
            histogram['offsets_from_hash_scan'])
          if src_total is not None:
            self.assertEqual(src_total, chunk_total)
            self.assertEqual(b''.join(source_chunks),
                             b''.join(all_chunks))
          if False:
            for hits, size in sorted([ (hits, size) for size, hits in histogram.items() if isinstance(size, int) ]):
              if hits > 1:
                X("size %d: %d", size, hits)
        if rfp is not None:
          rfp.close()
          rfp = None

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
