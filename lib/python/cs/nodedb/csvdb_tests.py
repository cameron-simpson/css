#!/usr/bin/python
#
# Unit tests for cs.nodedb.csvdb.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import sys
import unittest
from . import NodeDB
from .csvdb import Backend_CSVFile
from .node_tests import TestAll as NodeTestAll

class TestAll(NodeTestAll):

  def setUp(self):
    dbpath = 'test.csv'
    self.dbpath = dbpath
    if os.path.exists(dbpath):
      os.remove(dbpath)
    with open(dbpath, "wb") as fp:
      fp.write("TYPE,NAME,ATTR,VALUE\n")
    self.backend = Backend_CSVFile(dbpath)
    self.db = NodeDB(backend=self.backend)

  def test22persist(self):
    N = self.db.newNode('HOST:foo1')
    N.X = 1
    N2 = self.db.newNode('SWITCH:sw1')
    N2.Ys = (9,8,7)
    dbstate = dict(self.db)
    self.db.close()
    self.db = NodeDB(backend=Backend_CSVFile(self.dbpath))
    dbstate2 = dict(self.db)
    self.assert_(dbstate == dbstate2, "db state differs:\n\t%s\n\t%s" % (dbstate, dbstate2))

  def tearDown(self):
    self.db.close()

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
