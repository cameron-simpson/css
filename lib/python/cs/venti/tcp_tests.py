#!/usr/bin/python
#
# Self tests for cs.venti.tcp.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import random
import sys
import unittest
from cs.logutils import X
from cs.randutils import rand0, randblock
from .hash import HashUtilDict
from .hash_tests import _TestHashCodeUtils
from .store import MappingStore
from .store_tests import _TestStore
from .tcp import TCPStoreServer, TCPStoreClient

BIND_ADDR = '127.0.0.1'
_start_port = 19999

def make_tcp_store():
  global _start_port
  bind_addr = (BIND_ADDR, _start_port)
  _start_port += 1
  mapping_S = MappingStore(HashUtilDict())
  remote_S = TCPStoreServer(bind_addr, mapping_S)
  remote_S.open()
  S = TCPStoreClient(bind_addr)
  return S, remote_S

class TestTCPStore(_TestStore, unittest.TestCase):

  def _init_Store(self):
    self.S, self.remote_S = make_tcp_store()

  def setUp(self):
    _TestStore.setUp(self)
    self.remote_S.open()

  def tearDown(self):
    self.remote_S.close()
    _TestStore.tearDown(self)

class TestHashCodeUtilsTCPStore(_TestHashCodeUtils, unittest.TestCase):
  ''' Test HashUtils on a TCPStore on a HashUtilDict.
  '''

  def MAP_FACTORY(self):
    S, remote_S = make_tcp_store()
    remote_S.open()
    self.remote_S = remote_S
    return S

  def tearDown(self):
    self.remote_S.close()
    _TestHashCodeUtils.tearDown(self)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
