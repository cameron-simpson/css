#!/usr/bin/python
#
# Self tests for cs.venti.blockify.
#       - Cameron Simpson <cs@zip.com.au>
#

import os.path
import sys
import time
import unittest
from unittest import skip
from cs.logutils import D, X
from cs.mp3 import parse_mp3
from cs.randutils import rand0, randblock
from .blockify import blockify, blocked_chunks_of, \
                      blocks_of, MIN_BLOCKSIZE, MAX_BLOCKSIZE
from .cache import MemoryCacheStore
from .parsers import parse_text

def random_blocks(max_size=65536, count=64):
  ''' Generate `count` blocks of random sizes from 1 to `max_size`.
  '''
  for i in range(count):
    size = rand0(max_size) + 1
    yield randblock(size)

class TestAll(unittest.TestCase):

  def setUp(self):
    self.fp = open(__file__, "rb")

  def tearDown(self):
    self.fp.close()

  def test01parsers(self):
    random_data = list(random_blocks())
    rand_total = sum(len(chunk) for chunk in random_data)
    for parser in (parse_text, parse_mp3):
      with self.subTest(parser.__name__):
        Q = parser(random_data)
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
        self.assertEqual(rand_total, offset)

  def test02blocked_chunks_of(self):
    global rand_total
    with open(__file__, 'rb') as myfp:
      mycode = myfp.read()
    random_data = list(random_blocks(max_size=12000, count=1280))
    for parser in (
        None,
        parse_text,
        parse_mp3,
      ):
      parser_desc = 'None' if parser is None else parser.__name__
      for input_desc, input_chunks in (
          ('random data', random_data),
          (__file__, [ mycode for _ in range(100) ]),
        ):
        if parser is parse_mp3:
          if os.path.exists('TEST.mp3'):
            X("mp3 parse: replace input data with chunks from TEST.mp3")
            def read_input_chunks():
              with open('TEST.mp3', 'rb') as mp3fp:
                while True:
                  chunk = mp3fp.read(1024)
                  if chunk:
                    yield chunk
                  else:
                    break
            input_chunks = read_input_chunks()
            input_desc = 'TEST.mp3'
          else:
            X("no TEST.mp3 in ".os.getcwd())
        with self.subTest("blocked_chunks_of",
                          parser=parser_desc, input_chunks=input_desc):
          X("test parser %s vs %s...", parser, input_desc)
          src_total = 0
          source_chunks = list(input_chunks)
          for chunk in source_chunks:
            src_total += len(chunk)
          X("scan and block...")
          chunk_total = 0
          nchunks = 0
          all_chunks = []
          start_time = time.time()
          offset = 0
          prev_chunk = None
          for chunk in blocked_chunks_of(source_chunks, parser):
            if prev_chunk is not None:
              # this avoids issues with the final block, which may be short
              self.assertTrue(len(prev_chunk) >= MIN_BLOCKSIZE,
                              "len(prev_chunk)=%d < MIN_BLOCKSIZE=%d"
                              % (len(prev_chunk), MIN_BLOCKSIZE))
            if parser is parse_text:
              ##X("BLOCKED_CHUNK offset=%d len=%d: %r", offset, len(chunk), chunk)
              pass
            else:
              ##X("BLOCKED_CHUNK offset=%d len=%d", offset, len(chunk))
              pass
            offset += len(chunk)
            ##if parser is not None:
            ##  X("  CHUNK=%r", chunk)
            nchunks += 1
            chunk_total += len(chunk)
            all_chunks.append(chunk)
            # the pending.flush operation can return short blocks
            self.assertTrue(len(chunk) <= MAX_BLOCKSIZE,
                            "len(chunk)=%d > MAX_BLOCKSIZE=%d"
                            % (len(chunk), MAX_BLOCKSIZE))
            self.assertTrue(chunk_total <= src_total,
                            "chunk_total:%d > src_total:%d"
                            % (chunk_total, src_total))
            prev_chunk = chunk
          end_time = time.time()
          X("%s|%s: %d chunks in %gs, %d bytes at %g B/s",
            input_desc, parser_desc,
            nchunks, end_time-start_time, chunk_total,
            float(chunk_total) / (end_time-start_time))
          self.assertEqual(src_total, chunk_total)
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
