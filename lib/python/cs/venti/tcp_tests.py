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

BIND_ADDR = ('127.0.0.1', 9999)

class TestTCPStore(_TestStore):

  def _open_Store(self):
    X("OPENSTORE: make MappingStore...")
    self.mapping_S = MappingStore({})
    X("OPENSTORE: setup TCPStoreServer...")
    self.remote_S = TCPStoreServer(BIND_ADDR, self.mapping_S)
    X("OPENSTORE: set up TCPSToreClient...")
    self.S = TCPStoreClient(BIND_ADDR)
    X("OPENSTIORE COMPLETE")

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
