#!/usr/bin/python
#
# Tests for cs.socketutils.
#   - Cameron Simpson <cs@zip.com.au> 01nov2015
#

import sys
import socket
import unittest
from cs.socketutils import OpenSocket

class TestOpenSocket(unittest.TestCase):

  def setUp(self):
    self.sock1, self.sock2 = socket.socketpair()
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

  def test01msg1(self):
    self.fp1_w.write(b'text1\n')
    self.fp1_w.flush()
    text1 = self.fp2_r.read(6)
    self.assertEqual(text1, b'text1\n')
    self.fp1_w.close()

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  import signal
  def hup(sig, frame):
    thread_dump()
  signal.signal(signal.SIGHUP, hup)
  selftest(sys.argv)
