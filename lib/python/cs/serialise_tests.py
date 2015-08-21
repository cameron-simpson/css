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
from cs.serialise import get_bs, read_bs, put_bs, \
                         get_bsdata, read_bsdata, put_bsdata, \
                         Packet, get_Packet
from cs.py3 import bytes

def rand0(maxn):
  return random.randint(0, maxn)

def randblock(size):
  ''' Generate a pseudorandom chunk of bytes of the specified size.
  '''
  chunk = bytes( rand0(255) for x in range(size) )
  if type(chunk) is not bytes:
    raise RuntimeError("BANG2")
  return chunk

def randPacket(channel=None, tag=None, is_request=None, flags=None, size=None):
  if channel is None:
    channel = rand0(16384)
  if tag is None:
    tag = rand0(16384)
  if is_request is None:
    is_request = True if rand0(1) else False
  if flags is None:
    flags = rand0(65536)
  if size is None:
    size = rand0(16384)
  return Packet(channel, tag, is_request, flags, randblock(size))

if sys.hexversion >= 0x03000000:
  MyBytesIO = BytesIO
else:
  def MyBytesIO(bs):
    return BytesIO(bs.as_str())

class TestSerialise(unittest.TestCase):

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
    fp = MyBytesIO(data)
    n2 = read_bs(fp)
    self.assertEqual(n, n2, "incorrect value read back from BytesIO(put_bs(%d)): %d" % (n, n2))
    tail = fp.read()
    self.assertEqual(len(tail), 0, "%d unparsed bytes from BytesIO(put_bs(%d))" % (len(tail), n))

  def test00bs(self):
    self.assertRaises(IndexError, get_bs, bytes(()))
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
    fp = MyBytesIO(data)
    chunk2 = read_bsdata(fp)
    self.assertEqual(len(chunk), len(chunk2),
                     "incorrect value read back from MyBytesIO(put_bsdata(%d bytes)): %d bytes" % (len(chunk), len(chunk2)))
    self.assertEqual(chunk, chunk2, "incorrect value read back from MyBytesIO(put_bsdata(%r)): %r" % (chunk, chunk2))
    tail = fp.read()
    self.assertEqual(len(tail), 0, "%d unparsed bytes from MyBytesIO(put_bs(%d bytes))" % (len(tail), len(chunk)))

  def test01bsdata(self):
    self.assertEqual(get_bsdata(bytes( (0,) )), (bytes(()), 1))
    self.assertEqual(get_bsdata(bytes( (2, 0, 0) )), (bytes((0, 0)), 3))
    for n in 1, 3, 7, 127, 128, 255, 256, 16383, 16384:
      chunk = randblock(n)
      if type(chunk) is not bytes:
        raise RuntimeError("type(chunk)=%s" % (type(chunk),))
      self._test_roundtrip_bsdata(chunk)

  def _test_roundtrip_Packet(self, P):
    data = P.serialise()
    P2, offset = get_Packet(data)
    self.assertEqual(offset, len(data), "get_Packet(P.serialise(%s)): %d unparsed bytes: %r" % (P, len(data) - offset, data[offset:]))
    self.assertEqual(P, P2, "get_Packet(P.serialise(%s)) round trip fails" % (P,))

  def test02Packet(self):
    ok = True
    for channel in 0, 1, 5, 3021:
      for tag in 0, 1, 7, 9, 5021:
        for is_request in False, True:
          for flags in 0, 1, 5, 911:
            for payload_length in 0, 1, 255, 127, 131, 1023:
              payload = randblock(payload_length)
              P = Packet(channel=channel, tag=tag, is_request=is_request, flags=flags, payload=payload)
              self._test_roundtrip_Packet(P)
    # now test some randomly generated packets
    for _ in range(16):
      self._test_roundtrip_Packet(randPacket())

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
