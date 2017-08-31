#!/usr/bin/python
#
# Self tests for cs.vt.stream.
#       - Cameron Simpson <cs@cskk.id.au>
#

import os
import sys
import unittest
from cs.logutils import X
from .hash import DEFAULT_HASHCLASS
from .hash_tests import HashUtilDict, _TestHashCodeUtils
from .store import MappingStore
from .store_tests import TestStore
from .stream import StreamStore

def make_stream_store(hashclass, addif):
  upstream_rd, upstream_wr = os.pipe()
  downstream_rd, downstream_wr = os.pipe()
  remote_S = StreamStore( "stream_tests.make_stream_store.remote_S",
                          os.fdopen(upstream_rd, 'rb'),
                          os.fdopen(downstream_wr, 'wb'),
                          local_store=MappingStore("stream_tests.make_stream_store.remote_S.local_store", HashUtilDict()).open(),
                          addif=addif,
                          hashclass=hashclass
                        )
  S = StreamStore( "stream_tests.make_stream_store.S",
                   os.fdopen(downstream_rd, 'rb'),
                   os.fdopen(upstream_wr, 'wb'),
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
  ''' Test HashUtils on a StreamStore on a HashUtilDict.
  '''
  ADDIF_MODE = True

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
