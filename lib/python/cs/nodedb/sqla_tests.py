#!/usr/bin/python
#
# Unit tests for cs.nodedb.sqla.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from . import NodeDB
from .node import TestAll as NodeTestAll
from .sqla import Backend_SQLAlchemy

class TestAll(NodeTestAll):

  def setUp(self):
    self.backend=Backend_SQLAlchemy('sqlite:///:memory:')
    self.db=NodeDB(backend=self.backend)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
