#!/usr/bin/python
#
# Blockify tests.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Unit tests for cs.vt.blockify.
'''

from collections import defaultdict
from itertools import chain
import os
import os.path
import sys
import time
import unittest
from cs.buffer import chunky, CornuCopyBuffer
from cs.fileutils import read_from
from cs.randutils import randomish_chunks
from .blockify import blockify, blocked_chunks_of, \
                      MAX_BLOCKSIZE, DEFAULT_SCAN_SIZE
from .parsers import scan_text, scan_mp3, scan_mp4
from .store import MappingStore

from cs.x import X
import cs.x
cs.x.X_via_tty = True

QUICK = len(os.environ.get('QUICK', '')) > 0

SCANNERS = scan_text, scan_mp3, scan_mp4
SCAN_TESTFILES = {
    scan_text: ('CS_VT_BLOCKIFY_TESTS__TESTFILE_TEXT', __file__),
    scan_mp3: ('CS_VT_BLOCKIFY_TESTS__TESTFILE_MP3', 'TEST.mp3'),
    scan_mp4: ('CS_VT_BLOCKIFY_TESTS__TESTFILE_MP4', 'TEST.mp4'),
}

def scanner_testfile(scanner):
  ''' Return the filename to scan for a specified `scanner`, or `None`.
  '''
  try:
    envvar, default_filename = SCAN_TESTFILES[scanner]
  except KeyError:
    return None
  return os.environ.get(envvar, default_filename)

class TestAll(unittest.TestCase):
  ''' All the unit tests.
  '''

  # load my_code from this test suite
  with open(__file__, 'rb') as myfp:
    mycode = myfp.read()

  # generate some random data
  if QUICK:
    random_data = list(randomish_chunks(1200, limit=12))
  else:
    random_data = list(randomish_chunks(12000, limit=1280))

  def test01scanners(self):
    ''' Test some domain specific data parsers.
    '''
    for scanner in SCANNERS:
      with self.subTest(scanner.__name__):
        f = None
        testfilename = scanner_testfile(scanner)
        if testfilename is None:
          input_chunks = self.random_data
        else:
          self.assertIsNotNone(testfilename)
          f = open(testfilename, 'rb')
          input_chunks = read_from(f)
        Q = scanner(CornuCopyBuffer(iter(input_chunks)))
        last_qoffset = 0
        for qoffset in Q:
          self.assertIsInstance(
              qoffset, int, 'scanner must yield only ints, received %s:%r' %
              (type(qoffset), qoffset)
          )
          self.assertTrue(
              last_qoffset <= qoffset,
              "qoffset %d <= last_qoffset %d" % (qoffset, last_qoffset)
          )
          last_qoffset = qoffset
        if f is not None:
          f.close()
          f = None

  def test02blocked_chunks_of(self):
    ''' Blockify some input sources.
    '''
    for scanner in [None] + list(SCANNERS):
      testfilename = None if scanner is None else scanner_testfile(scanner)
      if testfilename is None:
        self._test_blocked_chunks_of(
            scanner, '100 x ' + __file__, [self.mycode for _ in range(100)]
        )
        self._test_blocked_chunks_of(scanner, 'random data', self.random_data)
      else:
        with open(testfilename, 'rb') as f:
          input_chunks = read_from(f, DEFAULT_SCAN_SIZE)
          self._test_blocked_chunks_of(scanner, testfilename, input_chunks)

  def _test_blocked_chunks_of(self, scanner, input_desc, input_chunks):
    with self.subTest("blocked_chunks_of", scanner=scanner, source=input_desc):
      source_chunks = list(input_chunks)
      src_total = sum(map(len, source_chunks))
      chunk_total = 0
      nchunks = 0
      all_chunks = []
      start_time = time.time()
      offset = 0
      chunky_scanner = chunky(scanner) if scanner else None
      histogram = defaultdict(int)
      for chunk in blocked_chunks_of(source_chunks, chunky_scanner,
                                     histogram=histogram):
        nchunks += 1
        chunk_total += len(chunk)
        all_chunks.append(chunk)
        offset += len(chunk)
        # the pending.flush operation can return short blocks
        ##self.assertTrue(len(chunk) >= MIN_BLOCKSIZE)
        self.assertTrue(
            len(chunk) <= MAX_BLOCKSIZE,
            "len(chunk)=%d > MAX_BLOCKSIZE=%d" % (len(chunk), MAX_BLOCKSIZE)
        )
        if src_total is not None:
          self.assertTrue(
              chunk_total <= src_total,
              "chunk_total:%d > src_total:%d" % (chunk_total, src_total)
          )
      end_time = time.time()
      if src_total is not None:
        self.assertEqual(src_total, chunk_total)
        self.assertEqual(b''.join(source_chunks), b''.join(all_chunks))
      # TODO: reenable the histogram stuff so that we can check on scanner behaviour
      if False:
        X(
            "%s|%s: received %d chunks in %gs, %d bytes at %g B/s", input_desc,
            scanner, nchunks, end_time - start_time, chunk_total,
            float(chunk_total) / (end_time - start_time)
        )
        X(
            "    %d offsets from scanner, %d offsets from hash scan",
            histogram['offsets_from_scanner'],
            histogram['offsets_from_hash_scan']
        )
        for hits, size in sorted([(hits, size)
                                  for size, hits in histogram.items()
                                  if isinstance(size, int)]):
          if hits > 1:
            X("size %d: %d", size, hits)

  def test03blockifyAndRetrieve(self):
    ''' Blockify some data and ensure that the blocks match the data.
    '''
    with MappingStore("TestAll.test00blockifyAndRetrieve", {}):
      with open(__file__, 'rb') as f:
        data = f.read()
      blocks = list(blockify([data]))
      data2 = b''.join(chain(*blocks))
      self.assertEqual(
          len(data), len(data2), "data mismatch: len(data)=%d, len(data2)=%d" %
          (len(data), len(data2))
      )
      self.assertEqual(
          data, data2,
          "data mismatch: data and data2 same length but contents differ"
      )

def selftest(argv):
  ''' Run the unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
