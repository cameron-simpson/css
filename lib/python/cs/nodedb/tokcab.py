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
from threading import Lock
from tokyocabinet.hash import Hash as TCHash, HDBOREADER, HDBOWRITER, HDBOCREAT
from cs.misc import seq
from cs.logutils import error, Pfx
from . import NodeDB, Backend
from .node import nodekey

class Backend_TokyoCabinet(Backend):

  def __init__(self, dbpath, readonly=False):
    self.readonly = readonly
    self.dbpath = dbpath
    self.tcdb = TCHash()
    with open("/dev/tty","w") as tty:
      tty.write("OPEN TC %s\n" % (self.tcdb,))
    self.tcdb.open(dbpath,
                   ( HDBOREADER
                     if readonly
                     else HDBOWRITER | HDBOCREAT
                   ))
    self.tclock = Lock()

  @unimplemented
  def sync(self):
    pass

  def close(self):
    if self.tcdb is None:
      raise ValueError, "%s.tcdb is None, .close() already called" % (self,)
    with open("/dev/tty","w") as tty:
      tty.write("CLOSE TC %s\n" % (self.tcdb,))
    with self.tclock:
      self.tcdb.close()
      self.tcdb = None

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
      except ValueError as e:
        raise ValueError("attrtag = %r: %s" % (attrtag, e))
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
    with Pfx("tc __getitem__(%r)" % (key,)):
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

  def delAttr(self, type, name, attr):
    assert not self.nodedb.readonly
    attrtag = self._attrtag(type, name, attr)
    with self.tclock:
      del self.tcdb[attrtag]

if __name__ == '__main__':
  import cs.nodedb.node_tests
  cs.nodedb.node_tests.selftest(sys.argv)
