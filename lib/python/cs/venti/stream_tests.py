#!/usr/bin/python
#
# Self tests for cs.venti.stream.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import sys
import unittest
from cs.logutils import X
from .store import MappingStore
from .store_tests import _TestStore
from .stream import StreamStore

class TestStreamStore(_TestStore):

  def _open_Store(self):
    self.upstream_rd, self.upstream_wr = os.pipe()
    self.downstream_rd, self.downstream_wr = os.pipe()
    self.remote_S = StreamStore( "test_remote_Store",
                                 os.fdopen(self.upstream_rd, 'rb'),
                                 os.fdopen(self.downstream_wr, 'wb'),
                                 local_store=MappingStore({}).open()
                               )
    self.S = StreamStore( "test_local_Store",
                          os.fdopen(self.downstream_rd, 'rb'),
                          os.fdopen(self.upstream_wr, 'wb'),
                        )
    self.S.open()

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
