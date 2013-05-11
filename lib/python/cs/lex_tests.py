#!/usr/bin/python
#
# Self tests for cs.lex.
#       - Cameron Simpson <cs@zip.com.au>
#

import unittest
from cs.lex import untexthexify

class TestLex(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def test00untexthexify(self):
    self.assertEqual(b'', untexthexify(''))
    self.assertRaises(TypeError, untexthexify, 'a')

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
