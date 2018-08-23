#!/usr/bin/python
#
# Stream tests.
#       - Cameron Simpson <cs@cskk.id.au>
#

import os
import sys
import unittest
from .hash import DEFAULT_HASHCLASS
from .hash_tests import HashUtilDict, _TestHashCodeUtils
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
  def _init_Store(self):
    self.S, self.remote_S = make_stream_store(self.hashclass, addif=False)

class TestStreamStoreAddIf(TestStore, unittest.TestCase):
  def _init_Store(self):
    self.S, self.remote_S = make_stream_store(self.hashclass, addif=True)

##@unittest.skip("too noisy")
class TestHashCodeUtilsStreamStore(_TestHashCodeUtils, unittest.TestCase):
  ''' Test HashUtils on a StreamStore on a HashUtilDict.
  '''
  ADDIF_MODE = False
  hashclass = DEFAULT_HASHCLASS
  def MAP_FACTORY(self):
    S, remote_S = make_stream_store(self.hashclass, self.ADDIF_MODE)
    remote_S.open()
    self.remote_S = remote_S
    return S

  def tearDown(self):
    self.remote_S.close()
    _TestHashCodeUtils.tearDown(self)

class TestHashCodeUtilsStreamStoreAddIf(TestHashCodeUtilsStreamStore):
  ''' Test HashUtils on a StreamStore on a HashUtilDict with addif=True.
  '''
  ADDIF_MODE = True

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
