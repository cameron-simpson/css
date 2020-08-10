#!/usr/bin/python
#
# Self tests for cs.inttypes.
#       - Cameron Simpson <cs@cskk.id.au>
#

import sys
import unittest
from cs.logutils import D
from cs.inttypes import BitMask, Enum, Flags

class TestInttypes(unittest.TestCase):
  ''' Test `cs.inttypes`.
  '''

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def test00bitmask(self):
    # basic bitmaskness and attribute access
    B = BitMask('a', 'b', 'c', 'd')
    n = B(9)
    self.assertEqual(str(n), "a|d")
    self.assertTrue(n.a)
    self.assertFalse(n.b)
    self.assertFalse(n.c)
    self.assertTrue(n.d)
    self.assertRaises(AttributeError, getattr, n, 'e')
    self.assertRaises(AttributeError, setattr, n, 'a', 1)

    # now check that another BitMask is indeed a distinct class
    B2 = BitMask('A', 'B', 'C', 'D', 'E')
    n2 = B2(7)
    self.assertEqual(str(n2), "A|B|C")
    self.assertTrue(n2.A)
    self.assertTrue(n2.B)
    self.assertTrue(n2.C)
    self.assertFalse(n2.D)
    self.assertFalse(n2.E)
    self.assertRaises(AttributeError, getattr, n2, 'F')
    self.assertRaises(AttributeError, getattr, n2, 'a')
    self.assertRaises(AttributeError, setattr, n2, 'A', 1)

  def test02flags(self):
    # basic attribute access
    F = Flags('a', 'b', 'c', 'd')
    n = F(9)
    self.assertEqual(str(n), "a,d")
    self.assertTrue(n.a)
    self.assertFalse(n.b)
    self.assertFalse(n.c)
    self.assertTrue(n.d)
    self.assertRaises(AttributeError, getattr, n, 'e')

    # now check that another Flags is indeed a distinct class
    F2 = Flags('A', 'B', 'C', 'D', 'E')
    n2 = F2(7)
    self.assertEqual(str(n2), "A,B,C")
    self.assertTrue(n2.A)
    self.assertTrue(n2.B)
    self.assertTrue(n2.C)
    self.assertFalse(n2.D)
    self.assertFalse(n2.E)
    self.assertRaises(AttributeError, getattr, n2, 'F')
    self.assertRaises(AttributeError, getattr, n2, 'a')

    n2.D = True
    self.assertEqual(int(n2), 15)
    n2.B = False
    self.assertEqual(int(n2), 13)

  def test03enum(self):
    E = Enum('a', 'b', 'c')
    n = E(2)
    self.assertEqual(n, 2)
    self.assertEqual(str(n), 'c')
    n2 = E(4)
    self.assertEqual(n2, 4)
    self.assertEqual(str(n2), '4')

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
