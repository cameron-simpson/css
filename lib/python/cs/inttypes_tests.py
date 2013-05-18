#!/usr/bin/python
#
# Self tests for cs.inttypes.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.inttypes import BitMask, Enum

class TestInttypes(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def test00bitmask(self):
    B = BitMask('a', 'b', 'c', 'd')
    n = B(9)
    self.assertEqual(str(n), "a|d")
    self.assertTrue(n.a)
    self.assertFalse(n.b)
    self.assertFalse(n.c)
    self.assertTrue(n.d)
    self.assertRaises(AttributeError, getattr, n, 'e')

  def test01Enum(self):
    E = Enum('a', 'b', 'c')
    n = E(2)
    self.assertEqual(n, 2)
    self.assertEqual(str(n), 'c')

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
