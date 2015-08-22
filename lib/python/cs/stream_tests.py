#!/usr/bin/python
#
# Self tests for cs.stream.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import absolute_import
import random
import sys
import os
import unittest
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
                                        name="remote")

  def tearDown(self):
    self.local_conn.shutdown()
    self.remote_conn.shutdown()

  @staticmethod
  def _decode_response_payload(flags, payload):
    X("_decode_response_payload(flags=%r, payload=%r)", flags, payload)

  def test00immediate_close(self):
    pass

  def test01single_request(self):
    R = self.local_conn.request(0, bytes(()), self._decode_response_payload, 0)
    X("Result from request = %s", R)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
