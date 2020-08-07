#!/usr/bin/python
#
# Unit tests for cs.nodedb.node.
#       - Cameron Simpson <cs@cskk.id.au>
#

import sys
import unittest
from cs.logutils import D
from cs.debug import thread_dump
from cs.timeutils import sleep
from cs.py3 import StringTypes
from . import NodeDB, Node
from .mappingdb import MappingBackend

class TestAll(unittest.TestCase):
  ''' Tests for `cs.nodedb.node`.
  '''

  def nodedb(self):
    self.backend = MappingBackend(self.mapping)
    self.db = NodeDB(backend=self.backend)
    return self.db

  def setUp(self):
    self.mapping = {}

  def tearDown(self):
    self.db = None

  def test01serialise(self):
    with self.nodedb() as db:
      H = db.newNode('HOST', 'foo')
      for value in 1, 'str1', ':str2', '::', H:
        sys.stderr.flush()
        s = db.totext(value)
        sys.stderr.flush()
        self.assertTrue(
            isinstance(s, StringTypes), "s is not stringy: %r" % (s,)
        )
        self.assertTrue(value == db.fromtext(s))

  def test02get(self):
    with self.nodedb() as db:
      H = db.make('HOST:foo')
      self.assertTrue(type(H) is Node)
      self.assertTrue(H.type == 'HOST')
      self.assertTrue(H.name == 'foo')

  def test10newNode(self):
    with self.nodedb() as db:
      H = db.newNode('HOST', 'foo')
      self.assertEqual(len(H.ATTR1s), len(()))
      self.assertRaises(AttributeError, getattr, H, 'ATTR2')
      H2 = db['HOST:foo']
      self.assertTrue(
          H is H2, "made HOST:foo, but retrieving it got a different object"
      )

  def test11setAttrs(self):
    with self.nodedb() as db:
      H = db.newNode('HOST', 'foo')
      H.Xs = [1, 2, 3, 4, 5]

  def test12setAttr(self):
    with self.nodedb() as db:
      H = db.newNode('HOST', 'foo')
      H.Y = 1
      H.Y = 2

  def testAttrXsNotation(self):
    with self.nodedb() as db:
      H = db.newNode('HOST', 'foo')
      NIC0 = db.newNode('NIC', 'eth0')
      NIC0.IPADDR = '1.2.3.4'
      NIC1 = db.newNode('NIC', 'eth1')
      NIC1.IPADDR = '5.6.7.8'
      H.NICs = (NIC0, NIC1)
      ipaddrs = H.NICs.IPADDRs
      self.assertEqual(ipaddrs, ['1.2.3.4', '5.6.7.8'])
      nics = H.NICs
      self.assertRaises(AttributeError, getattr, nics, 'IPADDR')

  def testReverseMap(self):
    with self.nodedb() as db:
      H = db.newNode('HOST', 'foo')
      NIC0 = db.newNode('NIC', 'eth0')
      NIC0.IPADDR = '1.2.3.4'
      NIC1 = db.newNode('NIC', 'eth1')
      NIC1.IPADDR = '5.6.7.8'
      H.NICs = (NIC0, NIC1)
      NIC0refs = list(NIC0.references())
      self.assertTrue(H in [N for N, a, c in NIC0.references()])
      self.assertTrue(H in [N for N, a, c in NIC1.references()])
      self.assertTrue(H not in [N for N, a, c in H.references()])

  def testWhere(self):
    with self.nodedb() as db:
      H = db.newNode('HOST', 'foo')
      NIC0 = db.newNode('NIC', 'eth0')
      NIC0.IPADDR = '1.2.3.4'
      NIC1 = db.newNode('NIC', 'eth1')
      NIC1.IPADDR = '5.6.7.8'
      H.NICs = (NIC0, NIC1)
      subnics = H.NICs.where(IPADDR='1.2.3.4')
      self.assertTrue(subnics == [NIC0])

  def testInTYPE(self):
    with self.nodedb() as db:
      H = db.newNode('HOST', 'foo')
      NIC0 = db.newNode('NIC', 'eth0')
      NIC0.IPADDR = '1.2.3.4'
      NIC1 = db.newNode('NIC', 'eth1')
      NIC1.IPADDR = '5.6.7.8'
      H.NICs = (NIC0, NIC1)
      self.assertTrue(NIC0.inHOST == [H])
      self.assertTrue(NIC0.inNIC == [])

  def testNoNode(self):
    with self.nodedb() as db:
      H = db.newNode('HOST', 'foo')
      self.assertTrue(bool(H), "bool(H) not True: H = %r" % (H,))
      self.assertRaises(AttributeError, getattr, H, 'NOATTR')
      db.useNoNode()
      N = H.NOATTR
      self.assertTrue(N is db._noNode)
      self.assertTrue(not bool(N), "bool(H.NOATTR) not False")
      N2 = N.NOATTR
      self.assertTrue(N2 is db._noNode)
      self.assertTrue(not bool(N2), "bool(H.NOATTR.NOATTR) not False")

  def testTokenisation(self):
    with self.nodedb() as db:
      H = db.newNode('HOST', 'foo')
      NIC0 = db.newNode('NIC', 'eth0')
      NIC0.IPADDR = '1.2.3.4'
      NIC1 = db.newNode('NIC', 'eth1')
      NIC1.IPADDR = '5.6.7.8'
      H.NICs = (NIC0, NIC1)

      for value, attr, expected_token in (
          (1, 'NIC', '1'),
          ("foo", 'NIC', '"foo"'),
          (":foo", 'NIC', '":foo"'),
          ('"foo"', 'NIC', r'"\"foo\""'),
          (NIC0, 'NIC', 'eth0'),
          (H, 'NIC', 'HOST:foo'),
          (H, 'SUBHOST', 'foo'),
      ):
        token = db.totoken(value, H, attr=attr)
        self.assertEqual(
            token, expected_token, "wrong tokenisation, expected %s but got %s"
            % (expected_token, token)
        )
        value2 = db.fromtoken(token, node=H, attr=attr, doCreate=True)
        self.assertEqual(
            value2, value,
            "round trip fails: %s -> %s -> %s" % (value, token, value2)
        )

  def testTYPENode(self):
    with self.nodedb() as db:
      T = db.TESTTYPE
      N1 = T.seqNode()
      N2 = T.seqNode()
      self.assertTrue(int(N1.name) < int(N2.name))

  def testSeqNode(self):
    with self.nodedb() as db:
      N1 = db.seqNode()
      N2 = db.seqNode()
      self.assertTrue(int(N1.name) < int(N2.name))

  def testTemplate(self):
    with self.nodedb() as db:
      N = db.seqNode()
      N.A = 1
      N.Bs = (2, 3, 4)
      ##D("N = %r", N)
      self.assertEqual(N.safe_substitute('tplt 0 {self}'), 'tplt 0 _:0')
      self.assertEqual(
          N.safe_substitute('tplt 0a { self }'), 'tplt 0a { self }'
      )
      self.assertEqual(N.safe_substitute('tplt 1 {self.A}'), 'tplt 1 1')
      self.assertEqual(N.safe_substitute('tplt 2 {self.As}'), 'tplt 2 [1]')
      self.assertEqual(
          N.safe_substitute('tplt 3 {self.Bs}'), 'tplt 3 [2, 3, 4]'
      )
      self.assertEqual(
          N.safe_substitute('tplt 3 {{self.Bs}}'), 'tplt 3 2tplt 3 3tplt 3 4'
      )
      self.assertEqual(N.safe_substitute('tplt 4 {self.Cs}'), 'tplt 4 []')
      self.assertEqual(N.safe_substitute('tplt 5 {self.C}'), 'tplt 5 {self.C}')

  def test22persist(self):
    with self.nodedb() as db:
      N = db.newNode('HOST:foo1')
      N.X = 1
      N2 = db.newNode('SWITCH:sw1')
      N2.Ys = (9, 8, 7)
      dbstate = dict(db)
    with self.nodedb() as db:
      dbstate2 = dict(db)
    self.assertTrue(
        dbstate == dbstate2,
        "db state differs:\n\t%s\n\t%s" % (dbstate, dbstate2)
    )

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
