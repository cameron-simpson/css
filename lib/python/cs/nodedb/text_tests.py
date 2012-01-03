#!/usr/bin/python
#
# Unit tests for cs.nodebd.text.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.nodedb.text import totoken, fromtoken, get_commatext

class TestTokeniser(unittest.TestCase):

  def setUp(self):
    from .node import NodeDB
    self.db = NodeDB(backend=None)

  def test01tokenise(self):
    ''' Test totoken(). '''
    self.assert_(totoken(0) == "0")
    self.assert_(totoken(1) == "1")
    self.assert_(totoken("abc") == "\"abc\"")
    self.assert_(totoken("http://foo.example.com/") == "http://foo.example.com/")

  def test02roundtrip(self):
    ''' Test totoken()/fromtoken() round trip. '''
    for value in 0, 1, "abc", "http://foo.example.com/":
      token = totoken(value)
      value2 = fromtoken(token, self.db)
      self.assert_(value == value2,
                   "round trip %s -> %s -> %s fails"
                   % (repr(value), repr(token), repr(value2)))

  def test03get_commatext(self):
    ''' Test get_commatext word parser. '''
    self.assert_(get_commatext('') == 0)
    self.assert_(get_commatext('abc') == 3)
    self.assert_(get_commatext('abc', 1) == 3)
    self.assert_(get_commatext(' abc') == 0)
    self.assert_(get_commatext(' abc', 1) == 4)
    self.assert_(get_commatext('ab"c d"ef') == 9)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
