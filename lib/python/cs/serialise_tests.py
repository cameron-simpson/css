#!/usr/bin/python
#
# Self tests for cs.serialise.
#       - Cameron Simpson <cs@cskk.id.au>
#

from __future__ import absolute_import
import sys
import unittest
from io import BytesIO
from cs.randutils import rand0, randbool, make_randblock
from cs.serialise import get_bs, put_bs, \
                         get_bsdata, put_bsdata, \
                         get_bss, put_bss
from cs.py3 import bytes

if sys.hexversion >= 0x03000000:
  MyBytesIO = BytesIO
else:

  def MyBytesIO(bs):
    return BytesIO(bs.as_str())

class TestSerialise(unittest.TestCase):
  ''' Tests for `cs.serialise`.
  '''

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def _tested_put_bs(self, n):
    data = put_bs(n)
    for i, b in enumerate(data):
      self.assertIs(int, type(b))
      self.assertIs(int, type(data[i]))
      self.assertEqual(b, data[i])
    return data

  def _test_roundtrip_bs(self, n):
    data = self._tested_put_bs(n)
    n2, offset = get_bs(data)
    # check that all encoded bytes consumed
    self.assertEqual(offset, len(data))
    # check that same value decoded as encoded
    self.assertEqual(n, n2)

  def OBSOLETEtest00bs(self):
    self.assertRaises(IndexError, get_bs, bytes(()))
    self.assertEqual(get_bs(bytes((0,))), (0, 1))
    for n in 1, 3, 7, 127, 128, 255, 256, 16383, 16384:
      with self.subTest(n=n):
        self._test_roundtrip_bs(n)

  def _test_roundtrip_bsdata(self, chunk):
    data = put_bsdata(chunk)
    chunk2, offset = get_bsdata(data)
    # check that all encoded bytes consumed
    self.assertEqual(offset, len(data))
    # check that same chunk decoded as encoded
    self.assertEqual(chunk, chunk2)

  def OBSOLETEtest01bsdata(self):
    self.assertEqual(get_bsdata(bytes((0,))), (bytes(()), 1))
    self.assertEqual(get_bsdata(bytes((2, 0, 0))), (bytes((0, 0)), 3))
    for n in 1, 3, 7, 127, 128, 255, 256, 16383, 16384:
      chunk = make_randblock(n)
      with self.subTest(n=n, chunk=chunk):
        if type(chunk) is not bytes:
          raise RuntimeError("type(chunk)=%s" % (type(chunk),))
        self._test_roundtrip_bsdata(chunk)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
