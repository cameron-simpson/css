#!/usr/bin/python
#
# TokyoCabinet backend.
#       - Cameron Simpson <cs@zip.com.au> 02may2010
#

import os
import os.path
from types import StringTypes
import unittest
import sys
import tc
from cs.logutils import error
from . import NodeDB, Backend
from .node import TestAll as NodeTestAll

class Backend_TokyoCabinet(Backend):

  def __init__(self, dbpath, readonly=False):
    self.readonly = readonly
    self.dbpath = dbpath
    self.hdb = tc.HDB()
    self.hdb.open(dbpath,
                  (tc.HDBOREADER if readonly else tc.HDBOWRITER | tc.HDBOCREAT))

  def _attrtag(self, N, attr):
    return ':'.join( (attr, N.type, N.name) )

  def _preload(self):
    ''' Prepopulate the NodeDB from the database.
    '''
    # load Nodes
    for attrtag, attrvalue in self.hdb.iteritems():
      attr, t, name = attrtag.split(':', 2)
      try:
        N = self.nodedb[t, name]
      except KeyError:
        N = self.nodedb._makeNode(t, name)
      values = [ self.deserialise(value) for value in attrvalue.split('\0') ]
      N[attr+'s'].extend(values, noBackend=True )

  def close(self):
    self.hdb.close()

  def newNode(self, N):
    assert not self.nodedb.readonly

  def delNode(self, N):
    assert not self.nodedb.readonly
    for ks in N.keys():
      del self.hdb[self._attrtag(N, ks[:-1])]

  def extendAttr(self, N, attr, values):
    assert len(values) > 0
    assert not self.nodedb.readonly
    attrtag = self._attrtag(N, attr)
    attrvalue = '\0'.join( self.serialise(_) for _ in values )
    if N[attr+'s']:
      self.hdb.putcat(attrtag, '\0'+attrvalue)
    else:
      self.hdb.put(attrtag, attrvalue)

  def delAttr(self, N, attr):
    assert not self.nodedb.readonly
    attrtag = self._attrtag(N, attr)
    del self.hdb[attrtag]

class TestAll(NodeTestAll):

  def setUp(self):
    dbpath = 'test.tch'
    self.dbpath = dbpath
    if os.path.exists(dbpath):
      os.remove(dbpath)
    self.backend=Backend_TokyoCabinet(dbpath)
    self.db=NodeDB(backend=self.backend)

  def test22persist(self):
    N = self.db.newNode('HOST:foo1')
    N.X=1
    N2 = self.db.newNode('SWITCH:sw1')
    N2.Ys=(9,8,7)
    dbstate = str(self.db)
    print >>sys.stderr, "test22 1:", dbstate
    self.db.close()
    self.db=NodeDB(backend=Backend_TokyoCabinet(self.dbpath))
    dbstate2 = str(self.db)
    print >>sys.stderr, "test22 2:", dbstate2
    self.assert_(dbstate == dbstate2, "db state differs:\n\t%s\n\t%s" % (dbstate, dbstate2))

  def tearDown(self):
    self.db.close()

if __name__ == '__main__':
  unittest.main()
