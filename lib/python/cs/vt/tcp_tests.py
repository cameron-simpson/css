#!/usr/bin/python
#
# Self tests for cs.vt.tcp.
#       - Cameron Simpson <cs@cskk.id.au>
#

import os
import errno
import random
import time
import sys
import unittest
from cs.debug import thread_dump, debug_object_shell
from cs.logutils import X
from .hash import HashUtilDict
from .hash_tests import _TestHashCodeUtils
from .store import MappingStore
from .store_tests import TestStore
from .tcp import TCPStoreServer, TCPStoreClient

BIND_HOST = '127.0.0.1'
_base_port = 9999

def make_tcp_store():
  global _base_port
  mapping_S = MappingStore("tcp_tests.make_tcp_store.mapping_S", HashUtilDict())
  while True:
    bind_addr = (BIND_HOST, _base_port)
    try:
      remote_S = TCPStoreServer(bind_addr, mapping_S)
    except OSError as e:
      if e.errno == errno.EADDRINUSE:
        _base_port += 1
      else:
        raise
    else:
      break
  ##X("BIND ADDRESS = %r", bind_addr)
  remote_S.open()
  S = TCPStoreClient(bind_addr)
  return S, remote_S

class TestTCPStore(TestStore, unittest.TestCase):

  def _init_Store(self):
    self.S, self.remote_S = make_tcp_store()

  def setUp(self):
    TestStore.setUp(self)
    self.remote_S.open()

  def tearDown(self):
    self.remote_S.close()
    TestStore.tearDown(self)

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
    ##debug_object_shell(self, prompt='%s.tearDown> ' % (self._testMethodName,))

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('__main__')
