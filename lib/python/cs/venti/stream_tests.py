#!/usr/bin/python
#
# Self tests for cs.venti.stream.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import sys
import unittest
from cs.logutils import X
from .hash_tests import HashUtilDict, _TestHashCodeUtils
from .store import MappingStore
from .store_tests import _TestStore
from .stream import StreamStore

def make_stream_store():
  upstream_rd, upstream_wr = os.pipe()
  downstream_rd, downstream_wr = os.pipe()
  remote_S = StreamStore( "test_remote_Store",
                          os.fdopen(upstream_rd, 'rb'),
                          os.fdopen(downstream_wr, 'wb'),
                          local_store=MappingStore(HashUtilDict()).open()
                        )
  S = StreamStore( "test_local_Store",
                   os.fdopen(downstream_rd, 'rb'),
                   os.fdopen(upstream_wr, 'wb'),
                 )
  return S, remote_S

class TestStreamStore(_TestStore, unittest.TestCase):

  def _init_Store(self):
    self.S, self.remote_S = make_stream_store()

class TestHashCodeUtilsStreamStore(_TestHashCodeUtils, unittest.TestCase):
  ''' Test HashUtils on a StreamStore on a HashUtilDict.
  '''
  def MAP_FACTORY(self):
    S, remote_S = make_stream_store()
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
