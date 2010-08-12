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
    self.tcdb = tc.HDB()
    self.tcdb.open(dbpath,
                   ( tc.HDBOREADER
                     if readonly
                     else tc.HDBOWRITER | tc.HDBOCREAT
                   ))

  def sync(self):
    raise NotImplementedError

  def close(self):
    self.tcdb.close()

  def _attrtag(self, N, attr):
    return ':'.join( (attr, N.type, N.name) )

  def _preload(self):
    ''' Prepopulate the NodeDB from the database.
    '''
    # load Nodes
    for attrtag, attrtexts in self.tcdb.iteritems():
      attr, t, name = attrtag.split(':', 2)
      try:
        N = self.nodedb[t, name]
      except KeyError:
        N = self.nodedb._makeNode(t, name)
      values = [ self.fromtext(text) for text in attrtexts.split('\0') ]
      N[attr+'s'].extend(values, noBackend=True )

  def newNode(self, N):
    assert not self.nodedb.readonly

  def delNode(self, N):
    assert not self.nodedb.readonly
    for ks in N.keys():
      del self.tcdb[self._attrtag(N, ks[:-1])]

  def extendAttr(self, N, attr, values):
    assert len(values) > 0
    assert not self.nodedb.readonly
    attrtag = self._attrtag(N, attr)
    attrtexts = '\0'.join( self.totext(_) for _ in values )
    if N[attr+'s']:
      self.tcdb.putcat(attrtag, '\0'+attrtexts)
    else:
      self.tcdb.put(attrtag, attrtexts)

  def set1Attr(self, N, attr, value):
    assert not self.nodedb.readonly
    attrtag = self._attrtag(N, attr)
    attrtexts = self.totext(value)
    self.tcdb.put(attrtag, attrtexts)

  def delAttr(self, N, attr):
    assert not self.nodedb.readonly
    attrtag = self._attrtag(N, attr)
    del self.tcdb[attrtag]

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
    self.db.close()
    self.db=NodeDB(backend=Backend_TokyoCabinet(self.dbpath))
    dbstate2 = str(self.db)
    self.assert_(dbstate == dbstate2, "db state differs:\n\t%s\n\t%s" % (dbstate, dbstate2))

  def tearDown(self):
    self.db.close()

if __name__ == '__main__':
  unittest.main()
