#!/usr/bin/python
#
# Unit tests for cs.nodedb.sqla.
#       - Cameron Simpson <cs@cskk.id.au>
#

import os
import sys
import unittest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from . import NodeDB
from .node_tests import TestAll as NodeTestAll
from .sqla import Backend_SQLAlchemy

class TestAll(NodeTestAll):

  def nodedb(self):
    self.backend = Backend_SQLAlchemy(self.engine)
    self.db = NodeDB(backend=self.backend)
    return self.db

  def setUp(self):
    self.sql_url = 'sqlite:///:memory:'
    self.engine = create_engine(self.sql_url,
                                poolclass=StaticPool,
                                echo=len(os.environ.get('DEBUG','')) > 0)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
