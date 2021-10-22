#!/usr/bin/python
#
# Tests for cs.socketutils.
#   - Cameron Simpson <cs@cskk.id.au> 01nov2015
#

import sys
import socket
from threading import Thread
import unittest
from cs.socketutils import OpenSocket, bind_next_port

class _TestOpenSocket(object):
  ''' Base class for socket tests.
  '''

  def setUp(self):
    self._setUp_sock12()
    self.fp1_w = OpenSocket(self.sock1, True)
    self.fp1_r = OpenSocket(self.sock1, False)
    self.fp2_w = OpenSocket(self.sock2, True)
    self.fp2_r = OpenSocket(self.sock2, False)

  def tearDown(self):
    if self.fp1_w:
      self.fp1_w.close()
    if self.fp1_r:
      self.fp1_r.close()
    if self.fp2_w:
      self.fp2_w.close()
    if self.fp2_r:
      self.fp2_r.close()
    self.sock1.close()
    self.sock2.close()

  def test00null(self):
    pass

  def test01msg_fromfp1_tofp2(self):
    self.fp1_w.write(b'text1\n')
    self.fp1_w.flush()
    text1 = self.fp2_r.read(6)
    self.assertEqual(text1, b'text1\n')
    self.fp1_w.close()

  def test02msg_fromfp1_tofp2_preclose_unused(self):
    self.fp1_r.close()
    self.fp2_w.close()
    self.fp1_w.write(b'text1\n')
    self.fp1_w.flush()
    text1 = self.fp2_r.read(6)
    self.assertEqual(text1, b'text1\n')
    self.fp1_w.close()

  def test03msg_fromfp2_tofp1_preclose_unused(self):
    self.fp2_r.close()
    self.fp1_w.close()
    self.fp2_w.write(b'text1\n')
    self.fp2_w.flush()
    text1 = self.fp1_r.read(6)
    self.assertEqual(text1, b'text1\n')
    self.fp2_w.close()

  def test04msg_fromfp1_tofp2_preclose_unused_then_eof(self):
    self.fp1_r.close()
    self.fp2_w.close()
    self.fp1_w.write(b'text1\n')
    self.fp1_w.flush()
    text1 = self.fp2_r.read(6)
    self.assertEqual(text1, b'text1\n')
    self.fp1_w.close()
    text2 = self.fp2_r.read(6)
    self.assertEqual(text2, b'')
    text3 = self.fp2_r.read(6)
    self.assertEqual(text3, b'')
    self.fp1_w.close()

class TestOpenSocket_socketpair(_TestOpenSocket, unittest.TestCase):
  ''' Tests for a socket pair.
  '''

  def _setUp_sock12(self):
    self.sock1, self.sock2 = socket.socketpair()

class TestOpenSocket_TCP(_TestOpenSocket, unittest.TestCase):
  ''' Tests for TCP sockets.
  '''

  def _setUp_sock12(self):
    self.sock0 = socket.socket()
    self.sock0_port = bind_next_port(self.sock0, '127.0.0.1', 10000)
    T = Thread(
        name='%s:server:listen(%d)' % (self._testMethodName, self.sock0_port),
        target=self.accept_once
    )
    self.sock0.listen(1)
    T.start()
    self.sock1 = socket.socket()
    self.sock1.connect(('127.0.0.1', self.sock0_port))
    T.join()

  def tearDown(self):
    self.sock0.close()
    _TestOpenSocket.tearDown(self)

  def accept_once(self):
    self.sock2, peer = self.sock0.accept()

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('__main__')
