#!/usr/bin/python
#
# CSV file backend.
#       - Cameron Simpson <cs@zip.com.au> 02may2010
#

import os
import os.path
from types import StringTypes
import unittest
import sys
from cs.logutils import error
from . import NodeDB, Backend
from .node import TestAll as NodeTestAll

class Backend_CSVFile(Backend):

  def __init__(self, csvpath, readonly=False):
    self.readonly = readonly
    self.csvpath = csvpath

  def close(self):
    if not self.nodedb.readonly:
      with open(self.csvpath, "wb") as fp:
        self.nodedb.dump(fp, fmt='csv')

  def _preload(self):
    ''' Prepopulate the NodeDB from the database.
    '''
    with open(self.csvpath, "rb") as fp:
      self.nodedb.load(fp, fmt='csv')

  def _attrtag(self, N, attr):
    return ':'.join( (attr, N.type, N.name) )

  def newNode(self, N):
    assert not self.nodedb.readonly

  def delNode(self, N):
    assert not self.nodedb.readonly

  def extendAttr(self, N, attr, values):
    assert len(values) > 0
    assert not self.nodedb.readonly

  def set1Attr(self, N, attr, value):
    assert not self.nodedb.readonly

  def delAttr(self, N, attr):
    assert not self.nodedb.readonly

class TestAll(NodeTestAll):

  def setUp(self):
    dbpath = 'test.csv'
    self.dbpath = dbpath
    if os.path.exists(dbpath):
      os.remove(dbpath)
    with open(dbpath, "wb") as fp:
      fp.write("TYPE,NAME,ATTR,VALUE\n")
    self.backend=Backend_CSVFile(dbpath)
    self.db=NodeDB(backend=self.backend)

  def test22persist(self):
    N = self.db.newNode('HOST:foo1')
    N.X=1
    N2 = self.db.newNode('SWITCH:sw1')
    N2.Ys=(9,8,7)
    dbstate = str(self.db)
    self.db.close()
    self.db=NodeDB(backend=Backend_CSVFile(self.dbpath))
    dbstate2 = str(self.db)
    self.assert_(dbstate == dbstate2, "db state differs:\n\t%s\n\t%s" % (dbstate, dbstate2))

  def tearDown(self):
    self.db.close()

if __name__ == '__main__':
  unittest.main()
