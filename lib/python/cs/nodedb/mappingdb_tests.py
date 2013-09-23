#!/usr/bin/python
#
# Unit tests for cs.nodedb.mappingdb.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import sys
import unittest
from cs.logutils import D
from . import NodeDB
from .mappingdb import MappingBackend
from .node_tests import TestAll as NodeTestAll

class TestAll(NodeTestAll):

  def setUp(self):
    self.mapping = {}
    self.backend = MappingBackend(self.mapping)
    self.db = NodeDB(backend=self.backend)

  def test22persist(self):
    N = self.db.newNode('HOST:foo1')
    N.X = 1
    N2 = self.db.newNode('SWITCH:sw1')
    N2.Ys = (9,8,7)
    dbstate = dict(self.db)
    self.db.close()
    self.db = NodeDB(backend=MappingBackend(self.mapping))
    dbstate2 = dict(self.db)
    self.assertTrue(dbstate == dbstate2, "db state differs:\n\t%s\n\t%s" % (dbstate, dbstate2))

  def tearDown(self):
    D("TD1: close db...")
    self.db.close()
    D("TD3")

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
