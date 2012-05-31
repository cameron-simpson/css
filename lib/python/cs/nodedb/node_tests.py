#!/usr/bin/python
#
# Unit tests for cs.nodedb.node.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from . import NodeDB, Node
from .mappingdb import MappingBackend

class TestAll(unittest.TestCase):

  def setUp(self):
    self.db = NodeDB(backend=MappingBackend({}))

  def test01serialise(self):
    H = self.db.newNode('HOST', 'foo')
    for value in 1, 'str1', ':str2', '::', H:
      sys.stderr.flush()
      s = self.db.totext(value)
      sys.stderr.flush()
      assert type(s) is str
      self.assert_(value == self.db.fromtext(s))

  def test02get(self):
    H = self.db.make('HOST:foo')
    self.assert_(type(H) is Node)
    self.assert_(H.type == 'HOST')
    self.assert_(H.name == 'foo')

  def test10newNode(self):
    H = self.db.newNode('HOST', 'foo')
    self.assertEqual(len(H.ATTR1s), len(()) )
    self.assertRaises(AttributeError, getattr, H, 'ATTR2')
    H2 = self.db['HOST:foo']
    self.assert_(H is H2, "made HOST:foo, but retrieving it got a different object")

  def test11setAttrs(self):
    H = self.db.newNode('HOST', 'foo')
    H.Xs = [1,2,3,4,5]

  def test12setAttr(self):
    H = self.db.newNode('HOST', 'foo')
    H.Y = 1
    H.Y = 2

  def testAttrXsNotation(self):
    H = self.db.newNode('HOST', 'foo')
    NIC0 = self.db.newNode('NIC', 'eth0')
    NIC0.IPADDR = '1.2.3.4'
    NIC1 = self.db.newNode('NIC', 'eth1')
    NIC1.IPADDR = '5.6.7.8'
    H.NICs = (NIC0, NIC1)
    ipaddrs = H.NICs.IPADDRs
    self.assertEqual(ipaddrs, ['1.2.3.4', '5.6.7.8'])
    nics = H.NICs
    self.assertRaises(AttributeError, getattr, nics, 'IPADDR')

  def testReverseMap(self):
    H = self.db.newNode('HOST', 'foo')
    NIC0 = self.db.newNode('NIC', 'eth0')
    NIC0.IPADDR = '1.2.3.4'
    NIC1 = self.db.newNode('NIC', 'eth1')
    NIC1.IPADDR = '5.6.7.8'
    H.NICs = (NIC0, NIC1)
    NIC0refs = list(NIC0.references())
    self.assert_(H in [ N for N, a, c in NIC0.references() ])
    self.assert_(H in [ N for N, a, c in NIC1.references() ])
    self.assert_(H not in [ N for N, a, c in H.references() ])

  def testWhere(self):
    H = self.db.newNode('HOST', 'foo')
    NIC0 = self.db.newNode('NIC', 'eth0')
    NIC0.IPADDR = '1.2.3.4'
    NIC1 = self.db.newNode('NIC', 'eth1')
    NIC1.IPADDR = '5.6.7.8'
    H.NICs = (NIC0, NIC1)
    subnics = H.NICs.where(IPADDR='1.2.3.4')
    self.assert_(subnics == [NIC0])

  def testInTYPE(self):
    H = self.db.newNode('HOST', 'foo')
    NIC0 = self.db.newNode('NIC', 'eth0')
    NIC0.IPADDR = '1.2.3.4'
    NIC1 = self.db.newNode('NIC', 'eth1')
    NIC1.IPADDR = '5.6.7.8'
    H.NICs = (NIC0, NIC1)
    self.assert_(NIC0.inHOST == [H])
    self.assert_(NIC0.inNIC == [])

  def testNoNode(self):
    H = self.db.newNode('HOST', 'foo')
    self.assert_(bool(H), "bool(H) not True")
    self.assertRaises(AttributeError, getattr, H, 'NOATTR')
    self.db.useNoNode()
    N = H.NOATTR
    self.assert_(N is self.db._noNode)
    self.assert_(not bool(N), "bool(H.NOATTR) not False")
    N2 = N.NOATTR
    self.assert_(N2 is self.db._noNode)
    self.assert_(not bool(N2), "bool(H.NOATTR.NOATTR) not False")

  def testTokenisation(self):
    H = self.db.newNode('HOST', 'foo')
    NIC0 = self.db.newNode('NIC', 'eth0')
    NIC0.IPADDR = '1.2.3.4'
    NIC1 = self.db.newNode('NIC', 'eth1')
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
      token = self.db.totoken(value, H, attr=attr)
      self.assertEquals(token, expected_token, "wrong tokenisation, expected %s but got %s" % (expected_token, token))
      value2 = self.db.fromtoken(token, node=H, attr=attr, doCreate=True)
      self.assertEquals(value2, value, "round trip fails: %s -> %s -> %s" % (value, token, value2))

  def testTYPENode(self):
    T = self.db.TESTTYPE
    N1 = T.seqNode()
    N2 = T.seqNode()
    self.assert_(int(N1.name) < int(N2.name))

  def testSeqNode(self):
    N1 = self.db.seqNode()
    N2 = self.db.seqNode()
    self.assert_(int(N1.name) < int(N2.name))

  def testTemplate(self):
    N = self.db.seqNode()
    N.A = 1
    N.Bs = (2,3,4)
    self.assertEquals(N.safe_substitute('tplt 0 {self}'), 'tplt 0 _:1')
    self.assertEquals(N.safe_substitute('tplt 0a { self }'), 'tplt 0a { self }')
    self.assertEquals(N.safe_substitute('tplt 1 {self.A}'), 'tplt 1 1')
    self.assertEquals(N.safe_substitute('tplt 2 {self.As}'), 'tplt 2 [1]')
    self.assertEquals(N.safe_substitute('tplt 3 {self.Bs}'), 'tplt 3 [2, 3, 4]')
    self.assertEquals(N.safe_substitute('tplt 3 {{self.Bs}}'), 'tplt 3 2tplt 3 3tplt 3 4')
    self.assertEquals(N.safe_substitute('tplt 4 {self.Cs}'), 'tplt 4 []')
    self.assertEquals(N.safe_substitute('tplt 5 {self.C}'), 'tplt 5 {self.C}')

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
