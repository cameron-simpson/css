#!/usr/bin/python
#
# Self tests for cs.stream.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import absolute_import
from functools import partial
import sys
import os
import random
import unittest
from cs.randutils import rand0, randblock
from cs.serialise import get_bs
from cs.stream import PacketConnection
from cs.logutils import X

class TestStream(unittest.TestCase):

  def setUp(self):
    self.upstream_rd, self.upstream_wr = os.pipe()
    self.downstream_rd, self.downstream_wr = os.pipe()
    self.local_conn = PacketConnection(os.fdopen(self.downstream_rd, 'rb'),
                                       os.fdopen(self.upstream_wr, 'wb'),
                                       name="local")
    self.remote_conn = PacketConnection(os.fdopen(self.upstream_rd, 'rb'),
                                        os.fdopen(self.downstream_wr, 'wb'),
                                        request_handler=self._request_handler,
                                        name="remote")

  def tearDown(self):
    self.local_conn.shutdown()
    self.remote_conn.shutdown()

  @staticmethod
  def _decode_response(flags, payload):
    return flags, payload

  @staticmethod
  def _request_handler(rq_type, flags, payload):
    return 0x11, bytes(reversed(payload))

  def test00immediate_close(self):
    pass

  def test01half_duplex(self):
    # throw the same packet up and back repeatedly
    for _ in range(16):
      R = self.local_conn.request(1, 0x55, bytes((2,3)), self._decode_response, 0)
      flags, payload = R()
      self.assertEqual(flags, 0x11)
      self.assertEqual(payload, bytes((3,2)))

  def test02full_duplex_random_payloads(self):
    # throw 16 packets up, collect responses after requests queued
    rqs = []
    for _ in range(16):
      size = rand0(16384)
      data = randblock(size)
      flags = rand0(65536)
      R = self.local_conn.request(0, flags, data, self._decode_response, 0)
      rqs.append( (R, flags, data) )
    random.shuffle(rqs)
    for rq in rqs:
      R, flags, data = rq
      flags, payload = R()
      self.assertEqual(flags, 0x11)
      self.assertEqual(payload, bytes(reversed(data)))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
