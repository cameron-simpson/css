#!/usr/bin/python
#
# Self tests for cs.rfc2616.
#       - Cameron Simpson <cs@zip.com.au> 28dec2014
#

import sys
from functools import partial
import unittest
from cs.rfc2616 import get_lws

class TestRFC2616(unittest.TestCase):

  def test00get_lws(self):
    match, offset = get_lws('\r\n   ')
    self.assertEqual(match, '\r\n   ')
    self.assertEqual(offset, 5)
    self.assertRaises(ValueError, get_lws, 'x')
    self.assertRaises(ValueError, get_lws, '\r')
    self.assertRaises(ValueError, get_lws, '\r\n')
    self.assertRaises(ValueError, get_lws, '\r\nx')

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
