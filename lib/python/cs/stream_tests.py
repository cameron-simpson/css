#!/usr/bin/python
#
# Self tests for cs.stream.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import absolute_import
from functools import partial
import sys
import os
import unittest
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
    print("RSP: flags=%r, payload=%r" % (flags, payload))

  @staticmethod
  def _request_handler(rq_type, flags, payload):
    print("RQ: type=%d, flags=0x%02x, data=%r" % (rq_type, flags, payload))

  def test00immediate_close(self):
    pass

  def test01single_request(self):
    R = self.local_conn.request(0, bytes((2,3)), self._decode_response, 0)
    R()

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
