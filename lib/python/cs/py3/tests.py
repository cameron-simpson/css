#!/usr/bin/python
#
# Self tests for cs.py3.
#       - Cameron Simpson <cs@cskk.id.au>
#

import sys
import unittest
from . import bytes

class TestBytes(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def test00bytes(self):
    b = bytes(10)
    self.assertIsInstance(b, bytes)
    self.assertEqual(len(b), 10)
    self.assertEqual(b[0], 0)
    self.assertEqual(b[9], 0)
    self.assertTrue( all( b[i] == 0 for i in range(len(b)) ) )
    self.assertEqual(b[3:3], bytes( () ))
    self.assertEqual(b[3:4], bytes( (0,) ))
    self.assertEqual(b[3:5], bytes( (0, 0) ))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
