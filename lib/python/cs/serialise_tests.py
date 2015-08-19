#!/usr/bin/python
#
# Self tests for cs.seq.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import absolute_import
import random
import sys
import unittest
from io import BytesIO
from cs.logutils import X
from cs.serialise import get_bs, read_bs, put_bs, \
                         get_bsdata, read_bsdata, put_bsdata, \
                         Packet, get_Packet
from cs.py3 import bytes

def randblock(size):
  return bytes( random.randint(0, 255) for x in range(size) )

class TestSerialise(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def _test_roundtrip_bs(self, n):
    data = put_bs(n)
    n2, offset = get_bs(data)
    # check that all encoded bytes consumed
    self.assertEqual(offset, len(data))
    # check that same value decoded as encoded
    self.assertEqual(n, n2)
    fp = BytesIO(data)
    n2 = read_bs(fp)
    self.assertEqual(n, n2, "incorrect value read back from BytesIO(put_bs(%d)): %d" % (n, n2))
    tail = fp.read()
    self.assertEqual(len(tail), 0, "%d unparsed bytes from BytesIO(put_bs(%d))" % (len(tail), n))

  def test00bs(self):
    self.assertRaises(IndexError, get_bs, '')
    self.assertEqual(get_bs(bytes((0,))), (0, 1))
    for n in 1, 3, 7, 127, 128, 255, 256, 16383, 16384:
      self._test_roundtrip_bs(n)

  def _test_roundtrip_bsdata(self, chunk):
    data = put_bsdata(chunk)
    chunk2, offset = get_bsdata(data)
    # check that all encoded bytes consumed
    self.assertEqual(offset, len(data))
    # check that same chunk decoded as encoded
    self.assertEqual(chunk, chunk2)
    fp = BytesIO(data)
    chunk2 = read_bsdata(fp)
    self.assertEqual(len(chunk), len(chunk2),
                     "incorrect value read back from BytesIO(put_bsdata(%d bytes)): %d bytes" % (len(chunk), len(chunk2)))
    self.assertEqual(chunk, chunk2, "incorrect value read back from BytesIO(put_bsdata(%r)): %r" % (chunk, chunk2))
    tail = fp.read()
    self.assertEqual(len(tail), 0, "%d unparsed bytes from BytesIO(put_bs(%d bytes))" % (len(tail), len(chunk)))

  def test01bsdata(self):
    self.assertEqual(get_bsdata(bytes( (0,) )), (bytes(()), 1))
    self.assertEqual(get_bsdata(bytes( (2, 0, 0) )), (bytes((0, 0)), 3))
    for n in 1, 3, 7, 127, 128, 255, 256, 16383, 16384:
      self._test_roundtrip_bsdata(randblock(n))

  def test02Packet(self):
    ok = True
    for channel in 0, 1, 5, 3021:
      for tag in 0, 1, 7, 9, 5021:
        for is_request in False, True:
          for flags in 0, 1, 5, 911:
            for payload_length in 0, 1, 255, 127, 131, 1023:
              payload = randblock(payload_length)
              P = Packet(channel=channel, tag=tag, is_request=is_request, flags=flags, payload=payload)
              data = P.serialise()
              ##X("P.serialise(%s): %r", P, data)
              P2, offset = get_Packet(data)
              self.assertEqual(offset, len(data), "get_Packet(P.serialise(%s)): %d unparsed bytes: %r" % (P, len(data) - offset, data[offset:]))
              self.assertEqual(P, P2, "get_Packet(P.serialise(%s)) round trip fails" % (P,))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
