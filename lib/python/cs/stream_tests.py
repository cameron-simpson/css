#!/usr/bin/python
#
# Self tests for cs.stream.
#       - Cameron Simpson <cs@cskk.id.au>
#

from __future__ import absolute_import
from functools import partial
import sys
import os
import random
import socket
from threading import Thread
import unittest
from cs.py3 import bytes
from cs.randutils import rand0, randblock
from cs.serialise import get_bs
from cs.socketutils import bind_next_port, OpenSocket
from cs.x import X
from .stream import PacketConnection

class _TestStream(object):

  def setUp(self):
    self._open_Streams()

  def _open_Streams(self):
    raise unittest.SkipTest("base test")

  def tearDown(self):
    self.local_conn.shutdown()
    self.remote_conn.shutdown()
    self._close_Streams()

  def _close_Streams(self):
    pass

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
      ok, response = R()
      self.assertTrue(ok, "response status not ok")
      flags, payload = response
      self.assertEqual(flags, 0x11)
      self.assertEqual(payload, bytes((3,2)))

  def test02full_duplex_random_payloads(self):
    # throw 16 packets up, collect responses after requests queued
    rqs = []
    for _ in range(16):
      size = rand0(16385)
      data = randblock(size)
      flags = rand0(65537)
      R = self.local_conn.request(0, flags, data, self._decode_response, 0)
      rqs.append( (R, flags, data) )
    random.shuffle(rqs)
    for rq in rqs:
      R, flags, data = rq
      ok, response = R()
      self.assertTrue(ok, "response status not ok")
      flags, payload = response
      self.assertEqual(flags, 0x11)
      self.assertEqual(payload, bytes(reversed(data)))

class TestStreamPipes(_TestStream, unittest.TestCase):

  def _open_Streams(self):
    self.upstream_rd, self.upstream_wr = os.pipe()
    self.downstream_rd, self.downstream_wr = os.pipe()
    self.local_conn = PacketConnection(os.fdopen(self.downstream_rd, 'rb'),
                                       os.fdopen(self.upstream_wr, 'wb'),
                                       name="local-pipes")
    self.remote_conn = PacketConnection(os.fdopen(self.upstream_rd, 'rb'),
                                        os.fdopen(self.downstream_wr, 'wb'),
                                        request_handler=self._request_handler,
                                        name="remote-pipes")

class TestStreamUNIXSockets(_TestStream, unittest.TestCase):

  def _open_Streams(self):
    self.upstream_rd, self.upstream_wr = socket.socketpair()
    self.downstream_rd, self.downstream_wr = socket.socketpair()
    self.local_conn = PacketConnection(OpenSocket(self.downstream_rd, False),
                                       OpenSocket(self.upstream_wr, True),
                                       name="local-socketpair")
    self.remote_conn = PacketConnection(OpenSocket(self.upstream_rd, False),
                                        OpenSocket(self.downstream_wr, True),
                                        request_handler=self._request_handler,
                                        name="remote-socketpair")

  def _close_Streams(self):
    self.upstream_rd.close()
    self.upstream_wr.close()
    self.downstream_rd.close()
    self.downstream_wr.close()

class TestStreamTCP(_TestStream, unittest.TestCase):

  def _open_Streams(self):
    self.listen_sock = socket.socket()
    self.listen_port = bind_next_port(self.listen_sock, '127.0.0.1', 9999)
    self.listen_sock.listen(1)
    self.downstream_sock = None
    accept_Thread = Thread(target=self._accept)
    accept_Thread.start()
    self.upstream_sock = socket.socket()
    self.upstream_sock.connect( ('127.0.0.1', self.listen_port) )
    accept_Thread.join()
    self.assertIsNot(self.downstream_sock, None)
    self.upstream_fp_rd = OpenSocket(self.upstream_sock, False)
    self.upstream_fp_wr = OpenSocket(self.upstream_sock, True)
    self.local_conn = PacketConnection(self.upstream_fp_rd,
                                       self.upstream_fp_wr,
                                       name="local-tcp")
    self.downstream_fp_rd = OpenSocket(self.downstream_sock, False)
    self.downstream_fp_wr = OpenSocket(self.downstream_sock, True)
    self.remote_conn = PacketConnection(self.downstream_fp_rd,
                                        self.downstream_fp_wr,
                                        request_handler=self._request_handler,
                                        name="remote-tcp")

  def _accept(self):
    self.downstream_sock, raddr = self.listen_sock.accept()
    self.listen_sock.close()

  def _close_Streams(self):
    self.upstream_sock.close()
    self.downstream_sock.close()

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('__main__')
