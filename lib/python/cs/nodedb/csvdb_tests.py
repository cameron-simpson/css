#!/usr/bin/python
#
# Unit tests for cs.nodedb.csvdb.
#       - Cameron Simpson <cs@cskk.id.au>
#

import os
import sys
import unittest
from cs.logutils import D
from . import NodeDB
from .csvdb import Backend_CSVFile
from .node_tests import TestAll as NodeTestAll

class CSVDBTestAll(NodeTestAll):
  ''' Tests for `cs.nodedb.csvdb`.
  '''

  def nodedb(self):
    self.backend = Backend_CSVFile(self.dbpath)
    self.db = NodeDB(backend=self.backend)
    return self.db

  def setUp(self):
    self.dbpath = 'test.csv'
    self.lockpath = self.dbpath + '.lock'
    if os.path.exists(self.dbpath):
      os.remove(self.dbpath)
    if os.path.exists(self.lockpath):
      os.remove(self.lockpath)
    # create empty csv file
    with open(self.dbpath, "w") as fp:
      fp.write("TYPE,NAME,ATTR,VALUE\n")

  def tearDown(self):
    if os.path.exists(self.lockpath):
      D("remove lockfile %s", self.lockpath)
      os.remove(self.lockpath)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
