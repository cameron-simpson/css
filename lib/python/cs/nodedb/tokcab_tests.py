#!/usr/bin/python
#
# Unit tests for cs.nodedb.tokab.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import print_function
import sys
print("cs.nodedb.tokcab deprecated in favor of KyotoCabinet", file=sys.stderr)
sys.exit(0)

import os
import os.path
import unittest
from cs.seq import seq
from .node_tests import TestAll as NodeTestAll
from . import NodeDB
from .tokcab import Backend_TokyoCabinet

class TestAll(NodeTestAll):

  def setUp(self):
    dbpath = 'test-%d.tch' % (seq(),)
    self.dbpath = dbpath
    if os.path.exists(dbpath):
      os.remove(dbpath)
    with open("/dev/tty","w") as tty:
      tty.write("SETUP test %s\n" % (self,))
    self.backend=Backend_TokyoCabinet(dbpath)
    self.db=NodeDB(backend=self.backend)

  def tearDown(self):
    self.db.close()

  def test22persist(self):
    N = self.db.newNode('HOST:foo1')
    N.X=1
    N2 = self.db.newNode('SWITCH:sw1')
    N2.Ys=(9,8,7)
    dbstate = dict(self.db.backend)
    self.db.backend.close()
    dbstate2 = dict(Backend_TokyoCabinet(self.dbpath))
    self.assertTrue(dbstate == dbstate2, "db state differs:\n\t%s\n\t%s" % (dbstate, dbstate2))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
