#!/usr/bin/python
#
# Self tests for cs.connect.
#       - Cameron Simpson <cs@cskk.id.au> 06oct2012
#

import sys
import unittest
from cs.connect import _BufferFile, DataQueue

class TestConnect(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def test_00_BufferFile_00(self):
    BF = _BufferFile(0)
    self.assertEqual(b'', BF.read(0, 0))
    # read before the begiing of the file
    self.assertRaises(ValueError, BF.read, -1, 0)
    # read after the end of the file
    self.assertRaises(ValueError, BF.read, 1, 0)
    # read more data than is in the file
    self.assertRaises(ValueError, BF.read, 0, 1)
    # read less than 0 bytes of data
    self.assertRaises(ValueError, BF.read, 0, -1)

  def test_01_DataQueue_00empty(self):
    DQ = DataQueue()
    self.assertRaises(ValueError, DQ.upto, -1)
    self.assertRaises(ValueError, DQ.upto, 1)
    DQ.close()

  def test_01_DataQueue_01small(self):
    DQ = DataQueue()
    DQ.write(b"abc")
    DQ.write(b"def")
    DQ.write(b"")        # need to sync with _writer thread
    self.assertEqual(DQ.size, 6)
    self.assertEqual(DQ.read(2), b"ab")
    self.assertEqual(DQ.read(3), b"abc")
    self.assertEqual(DQ.read(4), b"abcd")
    DQ.upto(3)
    self.assertEqual(DQ.size, 3)
    self.assertEqual(DQ.read(2), b"de")
    self.assertEqual(DQ.read(3), b"def")
    self.assertEqual(DQ.read(4), b"def")
    DQ.upto(6)
    self.assertEqual(DQ.size, 0)
    DQ.close()

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
