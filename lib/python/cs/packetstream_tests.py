#!/usr/bin/env python3
#

''' Unit tests for cs.packetstream.
'''

import os
import random
import socket
from threading import Thread
import unittest
from cs.binary_tests import _TestPacketFields
from cs.randutils import rand0, make_randblock
from cs.socketutils import bind_next_port, OpenSocket
from . import packetstream
from .packetstream import Packet, PacketConnection

class TestPacketStreamPacketFields(_TestPacketFields, unittest.TestCase):
  ''' Test for all the `PacketField`s.
  '''

  def setUp(self):
    self.module = packetstream

class TestPacket(unittest.TestCase):
  ''' Test various trivial packets.
  '''

  def test00round_trip(self):
    ''' Test construction/serialise/deserialise for various basic packets.
    '''
    for is_request in False, True:
      for channel in 0, 1, 13, 17, 257:
        for tag in 0, 1, 13, 17, 257:
          for flags in 0, 1, 15, 137:
            for payload in b'', b'123':
              with self.subTest(is_request=is_request, channel=channel,
                                tag=tag, flags=flags, payload=payload):
                P = Packet(
                    is_request=is_request,
                    channel=channel,
                    tag=tag,
                    flags=flags,
                    rq_type=0 if is_request else None,
                    payload=payload,
                )
                bs = bytes(P)
                P2, offset = Packet.parse_bytes(bs)
                self.assertEqual(offset, len(bs))
                self.assertEqual(P, P2)

class _TestStream(unittest.TestCase):
  ''' Base class for stream tests.
  '''

  def setUp(self):
    ''' Set up: open the streams.
    '''
    self._open_Streams()

  # pylint: disable=no-self-use
  def _open_Streams(self):
    raise unittest.SkipTest("base test")

  def tearDown(self):
    ''' Tear down: shutdown and close the streams.
    '''
    self.local_conn.shutdown()
    self.remote_conn.shutdown()
    self._close_Streams()

  def _close_Streams(self):
    pass

  @staticmethod
  def _decode_response(flags, payload):
    return payload

  @staticmethod
  def _request_handler(rq_type, flags, payload):
    return 0x11, bytes(reversed(payload))

  def test00immediate_close(self):
    ''' Trite test: do nothing with the streams.
    '''

  def test01half_duplex(self):
    ''' Half duplex test to throw the same packet up and back repeatedly.
    '''
    for _ in range(16):
      R = self.local_conn.request(
          1, 0x55, bytes((2, 3)), self._decode_response, 0
      )
      ok, flags, payload = R()
      self.assertTrue(ok, "response status not ok")
      self.assertEqual(flags, 0x11)
      self.assertEqual(payload, bytes((3, 2)))

  def test02full_duplex_random_payloads(self):
    ''' Throw 16 packets up, collect responses after requests queued.
    '''
    rqs = []
    for _ in range(16):
      size = rand0(16385)
      data = make_randblock(size)
      flags = rand0(65537)
      R = self.local_conn.request(0, flags, data, self._decode_response, 0)
      rqs.append((R, flags, data))
    random.shuffle(rqs)
    for rq in rqs:
      R, flags, data = rq
      ok, flags, payload = R()
      self.assertTrue(ok, "response status not ok")
      self.assertEqual(flags, 0x11)
      self.assertEqual(payload, bytes(reversed(data)))

class TestStreamPipes(_TestStream):
  ''' Test streaming over pipes.
  '''

  def _open_Streams(self):
    ''' Set up streams using UNIX pipes.
    '''
    self.upstream_rd, self.upstream_wr = os.pipe()
    self.downstream_rd, self.downstream_wr = os.pipe()
    self.local_conn = PacketConnection(
        self.downstream_rd, self.upstream_wr, name="local-pipes"
    )
    self.remote_conn = PacketConnection(
        self.upstream_rd,
        self.downstream_wr,
        request_handler=self._request_handler,
        name="remote-pipes"
    )

  def _close_Streams(self):
    os.close(self.upstream_rd)
    os.close(self.upstream_wr)
    os.close(self.downstream_rd)
    os.close(self.downstream_wr)

class TestStreamUNIXSockets(_TestStream):
  ''' Test streaming over sockets.
  '''

  def _open_Streams(self):
    ''' Set up streams using UNIX sockets.
    '''
    self.upstream_rd, self.upstream_wr = socket.socketpair()
    self.downstream_rd, self.downstream_wr = socket.socketpair()
    self.local_conn = PacketConnection(
        OpenSocket(self.downstream_rd, False),
        OpenSocket(self.upstream_wr, True),
        name="local-socketpair"
    )
    self.remote_conn = PacketConnection(
        OpenSocket(self.upstream_rd, False),
        OpenSocket(self.downstream_wr, True),
        request_handler=self._request_handler,
        name="remote-socketpair"
    )

  def _close_Streams(self):
    self.upstream_rd.close()
    self.upstream_wr.close()
    self.downstream_rd.close()
    self.downstream_wr.close()

# pylint: disable=too-many-instance-attributes
class TestStreamTCP(_TestStream, unittest.TestCase):
  ''' Test streaming over TCP.
  '''

  def _open_Streams(self):
    ''' Set up strreams using TCP connections.
    '''
    self.listen_sock = socket.socket()
    self.listen_port = bind_next_port(self.listen_sock, '127.0.0.1', 9999)
    self.listen_sock.listen(1)
    self.downstream_sock = None
    accept_Thread = Thread(target=self._accept)
    accept_Thread.start()
    self.upstream_sock = socket.socket()
    self.upstream_sock.connect(('127.0.0.1', self.listen_port))
    accept_Thread.join()
    self.assertIsNot(self.downstream_sock, None)
    self.upstream_fp_rd = OpenSocket(self.upstream_sock, False)
    self.upstream_fp_wr = OpenSocket(self.upstream_sock, True)
    self.local_conn = PacketConnection(
        self.upstream_fp_rd, self.upstream_fp_wr, name="local-tcp"
    )
    self.downstream_fp_rd = OpenSocket(self.downstream_sock, False)
    self.downstream_fp_wr = OpenSocket(self.downstream_sock, True)
    self.remote_conn = PacketConnection(
        self.downstream_fp_rd,
        self.downstream_fp_wr,
        request_handler=self._request_handler,
        name="remote-tcp"
    )

  def _accept(self):
    self.downstream_sock, _ = self.listen_sock.accept()
    self.listen_sock.close()

  def _close_Streams(self):
    self.upstream_sock.close()
    self.downstream_sock.close()

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('__main__')
