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
import thread
from thread import allocate_lock
from tokyocabinet.hash import Hash as TCHash, HDBOREADER, HDBOWRITER, HDBOCREAT
from cs.misc import seq
from cs.logutils import error, Pfx
from . import NodeDB, Backend
from .node import TestAll as NodeTestAll, nodekey

class Backend_TokyoCabinet(Backend):

  def __init__(self, dbpath, readonly=False):
    self.readonly = readonly
    self.dbpath = dbpath
    self.tcdb = TCHash()
    self.tcdb.open(dbpath,
                   ( HDBOREADER
                     if readonly
                     else HDBOWRITER | HDBOCREAT
                   ))
    self.tclock = allocate_lock()

  def sync(self):
    raise NotImplementedError

  def close(self):
    with open("/dev/tty","w") as tty:
      tty.write("CLOSE TC\n")
    with self.tclock:
      self.tcdb.close()

  def _attrtag(self, type, name, attr):
    return ':'.join( (attr, type, name) )

  def _attrtags(self, key):
    ''' Return the attribute record keys for the specified node key.
    '''
    k = nodekey(key)
    try:
      t, name = k
    except:
      raise
    with self.tclock:
      return self.tcdb.fwmkeys(':'.join( (t, name) ))

  def attrtags(self):
    with self.tclock:
      allattrtags = self.tcdb.fwmkeys('')
    return allattrtags

  def iterkeys(self):
    seen = set()
    for attrtag in self.attrtags():
      try:
        t, name, attr = attrtag.split(':', 2)
      except ValueError, e:
        raise ValueError("attrtag = %s: %s" % (`attrtag`,e))
      if (t, name) in seen:
        continue
      yield t, name
      seen.add( (t, name) )

  def __delitem__(self, key):
    ''' Remove all records for the specified node key.
    '''
    db = self.tcdb
    attrtags = self._attrtags(key)
    with self.tclock:
      for attrtag in attrtags:
        del db[attrtag]

  def __getitem__(self, key):
    with Pfx("tc __getitem__(%s)" % (`key`,)):
      d = {}
      db = self.tcdb
      with self.tclock:
        attrtags = self._attrtags(key)
        for attrtag in self._attrtags(key):
          t, name, attr = attrtag.split(':', 2)
          d[attr] = [ self.fromtext(_) for _ in db[attrtag].split('\0') ]
      return d

  def __setitem__(self, key, N):
    db = self.tcdb
    with self.tclock:
      for attr, values in N.items():
        attrtag = self._attrtag(type, name, attr)
        attrtexts = '\0'.join( self.totext(_) for _ in values )
        db.put( attrtag, attrtexts )

  def extendAttr(self, type, name, attr, values):
    assert len(values) > 0
    assert not self.nodedb.readonly
    attrtag = self._attrtag(type, name, attr)
    attrtexts = '\0'.join( self.totext(_) for _ in values )
    db = self.tcdb
    with self.tclock:
      if attrtag in db:
        self.tcdb.putcat(attrtag, '\0'+attrtexts)
      else:
        self.tcdb.put(attrtag, attrtexts)

  def set1Attr(self, type, name, attr, value):
    assert not self.nodedb.readonly
    attrtag = self._attrtag(type, name, attr)
    attrtexts = self.totext(value)
    with self.tclock:
      self.tcdb.put(attrtag, attrtexts)

  def delAttr(self, type, name, attr):
    assert not self.nodedb.readonly
    attrtag = self._attrtag(type, name, attr)
    with self.tclock:
      del self.tcdb[attrtag]

class TestAll(NodeTestAll):

  def setUp(self):
    dbpath = 'test-%d.tch' % (seq(),)
    self.dbpath = dbpath
    if os.path.exists(dbpath):
      os.remove(dbpath)
    self.backend=Backend_TokyoCabinet(dbpath)
    self.db=NodeDB(backend=self.backend)

  def tearDown(self):
    self.db.close()

  def test22persist(self):
    N = self.db.newNode('HOST:foo1')
    N.X=1
    N2 = self.db.newNode('SWITCH:sw1')
    N2.Ys=(9,8,7)
    dbstate = dict(self.db._backend)
    self.db._backend.close()
    dbstate2 = dict(Backend_TokyoCabinet(self.dbpath))
    self.assert_(dbstate == dbstate2, "db state differs:\n\t%s\n\t%s" % (dbstate, dbstate2))

if __name__ == '__main__':
  unittest.main()
