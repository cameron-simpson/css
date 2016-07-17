#!/usr/bin/python
#
# Self tests for cs.venti.stream.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import sys
import unittest
from cs.logutils import X
from .hash import DEFAULT_HASHCLASS
from .hash_tests import HashUtilDict, _TestHashCodeUtils
from .store import MappingStore
from .store_tests import _TestStore
from .stream import StreamStore

def make_stream_store(hashclass, addif):
  upstream_rd, upstream_wr = os.pipe()
  downstream_rd, downstream_wr = os.pipe()
  remote_S = StreamStore( os.fdopen(upstream_rd, 'rb'),
                          os.fdopen(downstream_wr, 'wb'),
                          name="test_remote_Store",
                          local_store=MappingStore(HashUtilDict()).open(),
                          addif=addif,
                          hashclass=hashclass
                        )
  S = StreamStore( os.fdopen(downstream_rd, 'rb'),
                   os.fdopen(upstream_wr, 'wb'),
                   name="test_local_Store",
                   addif=addif,
                   hashclass=hashclass
                 )
  return S, remote_S

class TestStreamStore(_TestStore, unittest.TestCase):
  def _init_Store(self):
    self.S, self.remote_S = make_stream_store(self.hashclass, addif=False)

class TestStreamStoreAddIf(_TestStore, unittest.TestCase):
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
