#!/usr/bin/python
#
# Scanner tests. - Cameron Simpson <cs@cskk.id.au>
#

''' Unit tests for cs.vt.scan.
'''

import os
import os.path
import sys
import unittest
from cs.buffer import CornuCopyBuffer
from cs.randutils import randomish_chunks
from .scan import py_scanbuf2, scanbuf2, scan, MIN_BLOCKSIZE, MAX_BLOCKSIZE

QUICK = len(os.environ.get('QUICK', '')) > 0

SCAN_DATA = [
    __file__,
    list(
        randomish_chunks(1200, limit=12)
        if QUICK else randomish_chunks(12000, limit=1280)
    ),
    'TEST.mp3',
    'TEST.mp4',
]

class TestScanBuf(unittest.TestCase):
  ''' The various test methods for a scanner.
  '''

  # load my_code from this test suite
  with open(__file__, 'rb') as myfp:
    mycode = myfp.read()

  def _generate_test_permutations(self):
    ''' Generator yielding test dictionaries.
    '''
    for scan_chunk in py_scanbuf2, scanbuf2:
      # catch "no C implementation"
      if scanbuf2 is py_scanbuf2:
        continue
      for min_block, max_block in (8, 1024 * 1024), (MIN_BLOCKSIZE,
                                                     MAX_BLOCKSIZE):
        self.assertGreater(min_block, 0)
        self.assertLess(min_block, max_block)
        for data_spec in SCAN_DATA:
          yield dict(
              scan_chunk=scan_chunk,
              min_block=min_block,
              max_block=max_block,
              data_spec=data_spec,
          )

  @staticmethod
  def _test_chunks(data_spec):
    ''' Return an iterable of chunks from a data spec (filename or list-of-bytes).
    '''
    # obtain the test data
    if isinstance(data_spec, str):
      chunks = CornuCopyBuffer.from_filename(data_spec)
    elif isinstance(data_spec, (list, tuple)):
      chunks = data_spec
    else:
      raise RuntimeError("unexpected data_spec of type %s", type(data_spec))
    return chunks

  def test01scanbuf2(self):
    ''' Run the scanbuf2 functions against various data.
    '''
    for test_combo in self._generate_test_permutations():
      with self.subTest(**test_combo):
        scan_chunk = test_combo['scan_chunk']
        min_block = test_combo['min_block']
        max_block = test_combo['max_block']
        data_spec = test_combo['data_spec']
        chunks = self._test_chunks(data_spec)
        # scan the test data
        sofar = 0
        hash_value = 0
        total_from_chunks = 0
        total_from_blocks = 0
        for chunk in chunks:
          total_from_chunks += len(chunk)
          with self.subTest(chunk=chunk):
            self.assertGreaterEqual(hash_value, 0)
            self.assertGreaterEqual(sofar, 0)
            hash_value2, chunk_offsets = scan_chunk(
                chunk, hash_value, sofar, min_block, max_block
            )
            self.assertIsInstance(hash_value2, int)
            self.assertGreaterEqual(hash_value2, 0)
            self.assertIsInstance(chunk_offsets, list)
            # check the returned offsets
            last_chunk_offset = -sofar
            for chunk_offset in chunk_offsets:
              # check that the offset falls within the chunk
              self.assertGreaterEqual(chunk_offset, 0)
              self.assertLess(chunk_offset, len(chunk))
              # check that the offsets are strictly monotonic increasing
              self.assertTrue(
                  last_chunk_offset is None or last_chunk_offset < chunk_offset
              )
              # check the size of the block
              block_size = chunk_offset - last_chunk_offset
              total_from_blocks += block_size
              self.assertLess(total_from_blocks, total_from_chunks)
              self.assertGreaterEqual(block_size, min_block)
              self.assertLessEqual(block_size, max_block)
              last_chunk_offset = chunk_offset
            # advance for next chunk
            sofar = len(chunk) - last_chunk_offset
            self.assertGreaterEqual(sofar, 0)
            hash_value = hash_value2
            # check we haven't skipped too far
            self.assertLessEqual(sofar, max_block)
        # end of input
        # tally last unparsed section
        total_from_blocks += sofar
        self.assertEqual(total_from_chunks, total_from_blocks)

  def test02scan(self):
    ''' Test the scan() wrapper for scanbuf2.
    '''
    for test_combo in self._generate_test_permutations():
      with self.subTest(**test_combo):
        scan_chunk = test_combo['scan_chunk']
        min_block = test_combo['min_block']
        max_block = test_combo['max_block']
        data_spec = test_combo['data_spec']
        chunks = self._test_chunks(data_spec)
        last_offset = None
        for offset in scan(chunks):
          self.assertGreater(offset, 0)
          self.assertTrue(last_offset is None or last_offset < offset)

  @unittest.skipIf(
      py_scanbuf2 is scanbuf2, "no C implementation: py_scanbuf2 is scanbuf2"
  )
  def test03scanbuf2_py_vs_c(self):
    ''' Test `py_scanbuf2` vs C `scanbuf2`.
    '''
    for test_combo in self._generate_test_permutations():
      with self.subTest(**test_combo):
        scan_chunk = test_combo['scan_chunk']
        min_block = test_combo['min_block']
        max_block = test_combo['max_block']
        data_spec = test_combo['data_spec']
        chunks = self._test_chunks(data_spec)
        py_offsets = list(scan(chunks, scan_buffer=py_scanbuf2))
        chunks = self._test_chunks(data_spec)
        c_offsets = list(scan(chunks, scan_buffer=scanbuf2))
        self.assertEqual(py_offsets, c_offsets)

def selftest(argv):
  ''' Run the unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
