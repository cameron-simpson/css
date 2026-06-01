#!/usr/bin/env python3
#

''' Unit tests for cs.packetstream.
'''

from contextlib import contextmanager
import os
import random
import socket
from threading import Thread, enumerate as enumerate_threads, main_thread
from typing import Callable
import unittest

from typeguard import typechecked

from cs.binary_tests import BaseTestBinaryClasses
from cs.context import stackattrs
from cs.debug import thread_dump
from cs.logutils import warning
from cs.pfx import pfx_call
from cs.randutils import rand0
from cs.socketutils import bind_next_port, OpenSocket
from cs.testutils import SetupTeardownMixin
from cs.x import X

from . import packetstream
from .packetstream import Packet, PacketConnection

class TestPacketStreamBinaryClasses(BaseTestBinaryClasses, unittest.TestCase):
  ''' Test for all the `AbstractBinary` subclasses.
  '''
  test_module = packetstream

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

@contextmanager
@typechecked
def connection_pair(
    name: str,
    upstream_rd,
    upstream_wr,
    downstream_rd,
    downstream_wr,
    request_handler: Callable,
):
  ''' Create connection client and server `PacketConnection`s
        and yield the client and server connections for testing.
    '''
  with PacketConnection(
      (downstream_rd, upstream_wr),
      f'{name}-local',  ##trace_log=X,
  ) as local_conn:
    if local_conn.requests_allowed:
      raise RuntimeError
    with PacketConnection(
        (upstream_rd, downstream_wr),
        f'{name}-remote',
        request_handler=request_handler,  ##trace_log=X,
    ) as remote_conn:
      if not remote_conn.requests_allowed:
        raise RuntimeError
      try:
        yield local_conn, remote_conn
      finally:
        # We explicitly send end of requests and end of file
        # because we're running both local and remote.
        # This is supposed to work automaticly if we're only
        # running the local end.
        local_conn.send_erq()
        local_conn.send_eof()
        remote_conn.send_erq()
        remote_conn.send_eof()
    assert remote_conn.closed, "remote_conn %s not closed" % (remote_conn,)
    remote_conn.join()
  assert local_conn.closed, "local_conn %s not closed" % (local_conn,)
  local_conn.join()

class _TestStream(SetupTeardownMixin):
  ''' Base class for stream tests.
  '''

  @contextmanager
  def setupTeardown(
      self,
      upstream_rd,
      upstream_wr,
      downstream_rd,
      downstream_wr,
  ):
    ''' Set up: open the streams.
    '''
    clsname = self.__class__.__name__
    with connection_pair(
        clsname,
        upstream_rd,
        upstream_wr,
        downstream_rd,
        downstream_wr,
        self._request_handler,
    ) as (local_conn, remote_conn):
      with stackattrs(self, local_conn=local_conn, remote_conn=remote_conn):
        yield local_conn, remote_conn

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
      R = self.local_conn.submit(
          1,
          0x55,
          bytes((2, 3)),
          decode_response=self._decode_response,
          channel=0,
      )
      ok, flags, payload = R()
      self.assertTrue(ok, "response status not ok")
      self.assertEqual(flags, 0x11)
      self.assertEqual(payload, bytes((3, 2)))

  def test02full_duplex_random_payloads(self):
    ''' Throw 16 packets up, collect responses after requests queued.
    '''
    rqs = []
    for i in range(1678):
      data = f'forward-{i}'.encode('ascii')
      flags = rand0(65537)
      R = self.local_conn.submit(
          0,  # rq_type
          flags,
          data,
          decode_response=self._decode_response,
          channel=0,
      )
      rqs.append((R, flags, data))
    random.shuffle(rqs)
    for rq in rqs:
      R, flags, data = rq
      ok, flags, payload = R()
      self.assertTrue(ok, "response status not ok")
      self.assertEqual(flags, 0x11)
      self.assertEqual(payload, bytes(reversed(data)))

class TestStreamPipes(_TestStream, unittest.TestCase):
  ''' Test streaming over pipes.
  '''

  @contextmanager
  def setupTeardown(self):
    ''' Set up streams using UNIX pipes.
    '''
    upstream_rd, upstream_wr = os.pipe()
    downstream_rd, downstream_wr = os.pipe()
    try:
      with super().setupTeardown(
          upstream_rd,
          upstream_wr,
          downstream_rd,
          downstream_wr,
      ):
        yield
    finally:
      # workers should be complete
      Ts = enumerate_threads()
      ok = True
      for T in Ts:
        if T is main_thread():
          continue
        if T.name.endswith(('.ticker', '(_ticker)')):
          continue
        ok = False
      if not ok:
        thread_dump()
        ##breakpoint()
      for fd in (
          upstream_rd,
          upstream_wr,
          downstream_rd,
          downstream_wr,
      ):
        try:
          pfx_call(os.close, fd)
        except OSError as e:
          warning("close(fd:%d): %s", fd, e)
          raise

class TestStreamUNIXSockets(_TestStream, unittest.TestCase):
  ''' Test streaming over sockets.
  '''

  @contextmanager
  def setupTeardown(self):
    ''' Set up streams using UNIX sockets.
    '''
    upstream_rd, upstream_wr = socket.socketpair()
    downstream_rd, downstream_wr = socket.socketpair()
    try:
      with super().setupTeardown(
          upstream_rd.fileno(),
          upstream_wr.fileno(),
          downstream_rd.fileno(),
          downstream_wr.fileno(),
      ):
        yield
    finally:
      upstream_rd.close()
      upstream_wr.close()
      downstream_rd.close()
      downstream_wr.close()

# pylint: disable=too-many-instance-attributes
class TestStreamTCP(_TestStream, unittest.TestCase):
  ''' Test streaming over TCP.
  '''

  @contextmanager
  def setupTeardown(self):
    ''' Set up streams using TCP connections.
    '''
    with socket.socket() as listen_sock:
      self.listen_sock = listen_sock
      listen_port = bind_next_port(listen_sock, '127.0.0.1', 9999)
      listen_sock.listen(1)
      # this will be the server side accepted connection socket
      self.service_sock = None
      accept_Thread = Thread(target=self._accept)
      accept_Thread.start()
      with socket.socket() as client_sock:
        client_sock.connect(('127.0.0.1', listen_port))
        accept_Thread.join()
        self.assertIsNotNone(self.service_sock)
        service_sock = self.service_sock
        with service_sock:
          client_fp_rd = OpenSocket(client_sock, False)
          client_fp_wr = OpenSocket(client_sock, True)
          service_fp_rd = OpenSocket(service_sock, False)
          service_fp_wr = OpenSocket(service_sock, True)
          with super().setupTeardown(
              # the channel to the service
              service_fp_rd,
              client_fp_wr,  # the channel from the service
              client_fp_rd,
              service_fp_wr,
          ):
            yield

  def _accept(self):
    self.service_sock, _ = self.listen_sock.accept()

class TestReuse(unittest.TestCase):

  @staticmethod
  def _request_handler(rq_type, flags, payload):
    return 0x11, bytes(reversed(payload))

  @staticmethod
  def _decode_response(flags, payload):
    return payload

  def test01reuse(self):
    ''' Create some pipes and use them for multiple connections in sequence.
    '''
    for _ in range(1):
      upstream_rd, upstream_wr = os.pipe()
      downstream_rd, downstream_wr = os.pipe()
      with connection_pair(
          f'{self.__class__.__name__} pass {_}',
          upstream_rd,
          upstream_wr,
          downstream_rd,
          downstream_wr,
          self._request_handler,
      ) as (local_conn, remote_conn):
        data = f'pass {_}'.encode('ascii')
        R = local_conn.submit(
            0,  # rq_type
            0x11,
            data,
            decode_response=self._decode_response,
            channel=0,
        )
        ok, flags, payload = R()
        self.assertTrue(ok, "response status not ok")
        self.assertEqual(flags, 0x11)
        self.assertEqual(payload, bytes(reversed(data)))
      os.close(upstream_rd)
      os.close(upstream_wr)
      os.close(downstream_rd)
      os.close(downstream_wr)
      ##thread_dump()

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('__main__')
