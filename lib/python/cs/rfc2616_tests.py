#!/usr/bin/python
#
# Self tests for cs.rfc2616.
#       - Cameron Simpson <cs@cskk.id.au> 28dec2014
#

import sys
from functools import partial
import unittest
from cs.rfc2616 import get_lws, parse_chunk_line1

class TestRFC2616(unittest.TestCase):
  ''' Tests for `cs.rfc2616`.
  '''

  def test00get_lws(self):
    match, offset = get_lws('\r\n   ')
    self.assertEqual(match, '\r\n   ')
    self.assertEqual(offset, 5)
    self.assertRaises(ValueError, get_lws, 'x')
    self.assertRaises(ValueError, get_lws, '\r')
    self.assertRaises(ValueError, get_lws, '\r\n')
    self.assertRaises(ValueError, get_lws, '\r\nx')

  def _test_parse_chunk_line1(self, bline, result):
    self.assertEqual(parse_chunk_line1(bline), result)

  def test00parse_chunk_line1(self):
    self._test_parse_chunk_line1(b'1\r\n', (1, []))
    self._test_parse_chunk_line1(b'2;x=y\r\n', (2, [('x', 'y')]))
    self._test_parse_chunk_line1(
        b'3;x=y;z="qstr"\r\n', (3, [('x', 'y'), ('z', 'qstr')])
    )

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
