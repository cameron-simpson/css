#!/usr/bin/python
#
# Stream tests.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Tests for StreamStores.
'''

import os
import sys
import unittest
from cs.x import X
from .hash import DEFAULT_HASHCLASS
from .hash_tests import HashUtilDict
from .store import MappingStore
from .store_tests import TestStore
from .stream import StreamStore

def make_stream_store(hashclass, addif):
  ''' Contruct an in-memory remote Store attached with pipes.
  '''
  upstream_rd, upstream_wr = os.pipe()
  downstream_rd, downstream_wr = os.pipe()
  remote_S = StreamStore(
      "stream_tests.make_stream_store.remote_S",
      upstream_rd,
      downstream_wr,
      local_store=MappingStore(
          "stream_tests.make_stream_store.remote_S.local_store",
          HashUtilDict()).open(),
      addif=addif,
      hashclass=hashclass
  )
  S = StreamStore(
      "stream_tests.make_stream_store.S",
      downstream_rd,
      upstream_wr,
      addif=addif,
      hashclass=hashclass
  )
  return S, remote_S

class TestStreamStore(TestStore, unittest.TestCase):
  ''' Test a stream in non-addif mode.
  '''

  def _init_Store(self):
    self.S, self.remote_S = make_stream_store(self.hashclass, addif=False)

  def setUp(self):
    self._init_Store()
    TestStore.setUp(self)
    self.remote_S.open()

  def tearDown(self):
    self.remote_S.close()
    TestStore.tearDown(self)

class TestStreamStoreAddIf(TestStore, unittest.TestCase):
  ''' test a stream in addif mode.
  '''

  def _init_Store(self):
    self.S, self.remote_S = make_stream_store(self.hashclass, addif=True)

  def setUp(self):
    self._init_Store()
    TestStore.setUp(self)
    self.remote_S.open()

  def tearDown(self):
    self.remote_S.close()
    TestStore.tearDown(self)

def selftest(argv):
  ''' Run the unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
