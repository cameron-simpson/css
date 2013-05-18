#!/usr/bin/python
#
# Self tests for cs.inttypes.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.logutils import D
from cs.inttypes import BitMask, Enum

class TestInttypes(unittest.TestCase):

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

  def test01Enum(self):
    E = Enum('a', 'b', 'c')
    n = E(2)
    self.assertEqual(n, 2)
    self.assertEqual(str(n), 'c')

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
