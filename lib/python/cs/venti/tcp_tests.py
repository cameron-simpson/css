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
from .store import MappingStore
from .store_tests import _TestStore
from .tcp import TCPStoreServer, TCPStoreClient

BIND_ADDR = '127.0.0.1'
_start_port = 19999

class TestTCPStore(_TestStore):

  def _init_Store(self):
    global _start_port
    bind_addr = (BIND_ADDR, _start_port)
    _start_port += 1
    self.mapping_S = MappingStore({})
    self.remote_S = TCPStoreServer(bind_addr, self.mapping_S)
    self.S = TCPStoreClient(bind_addr)

  def setUp(self):
    _TestStore.setUp(self)
    self.remote_S.open()

  def tearDown(self):
    self.remote_S.close()
    _TestStore.tearDown(self)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
