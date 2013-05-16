#!/usr/bin/python
#
# Self tests for cs.lex.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.lex import texthexify, untexthexify
from cs.py3 import makebytes

class TestLex(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def test00untexthexify(self):
    self.assertEqual(b'', untexthexify(''))
    self.assertEqual(b'A', untexthexify('41'))
    self.assertEqual(b'ABC', untexthexify('41[BC]'))
    self.assertRaises(TypeError, untexthexify, 'a')

  def test01texthexify(self):
    self.assertEqual('', texthexify(b''))
    self.assertEqual('00', texthexify(makebytes( (0x00,) )))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
