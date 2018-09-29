#!/usr/bin/python
#
# TCP and UNIX domain socket stream tests.
# - Cameron Simpson <cs@cskk.id.au>
#

''' TCP and UNIX domain socket stream tests.
'''

import errno
import sys
import unittest
from .hash import HashUtilDict
from .hash_tests import _TestHashCodeUtils
from .socket import TCPStoreServer, TCPClientStore
from .store import MappingStore
from .store_tests import TestStore

BIND_HOST = '127.0.0.1'
BASE_PORT = 9999

def make_tcp_store():
  global BASE_PORT
  base_port = BASE_PORT
  mapping_S = MappingStore("tcp_tests.make_tcp_store.mapping_S", HashUtilDict())
  while True:
    bind_addr = (BIND_HOST, base_port)
    try:
      remote_S = TCPStoreServer(bind_addr, local_store=mapping_S)
    except OSError as e:
      if e.errno == errno.EADDRINUSE:
        base_port += 1
      else:
        raise
    else:
      break
  remote_S.open()
  S = TCPClientStore(None, bind_addr)
  return S, remote_S

class TestTCPStore(TestStore, unittest.TestCase):
  ''' Tests for TCPStoreServer and TCPClientStore.
  '''

  def _init_Store(self):
    self.S, self.remote_S = make_tcp_store()

  def setUp(self):
    self._init_Store()
    TestStore.setUp(self)
    self.remote_S.open()

  def tearDown(self):
    self.remote_S.close()
    TestStore.tearDown(self)

class TestHashCodeUtilsTCPStore(_TestHashCodeUtils, unittest.TestCase):
  ''' Test HashUtils on a TCPStore on a HashUtilDict.
  '''

  def MAP_FACTORY(self):
    ''' Return context manager for this test's resources.
    '''
    S, remote_S = make_tcp_store()
    remote_S.open()
    self.remote_S = remote_S
    return S

  def tearDown(self):
    self.remote_S.close()
    _TestHashCodeUtils.tearDown(self)
    ##debug_object_shell(self, prompt='%s.tearDown> ' % (self._testMethodName,))

def selftest(argv):
  ''' Run the unit tests with `argv`.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
