#!/usr/bin/python
#
# Unit tests for cs.nodebd.text.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from .text import totoken, fromtoken, get_commatext
from .mappingdb import MappingBackend

class TestTokeniser(unittest.TestCase):

  def setUp(self):
    from .node import NodeDB
    self.db = NodeDB(backend=MappingBackend({}))

  def test01tokenise(self):
    ''' Test totoken(). '''
    self.assertTrue(totoken(0) == "0")
    self.assertTrue(totoken(1) == "1")
    self.assertTrue(totoken("abc") == "\"abc\"")
    self.assertTrue(totoken("http://foo.example.com/") == "http://foo.example.com/")

  def test02roundtrip(self):
    ''' Test totoken()/fromtoken() round trip. '''
    for value in 0, 1, "abc", "http://foo.example.com/":
      token = totoken(value)
      value2 = fromtoken(token, self.db)
      self.assertTrue(value == value2,
                   "round trip %s -> %s -> %s fails"
                   % (repr(value), repr(token), repr(value2)))

  def test03get_commatext(self):
    ''' Test get_commatext word parser. '''
    self.assertTrue(get_commatext('') == 0)
    self.assertTrue(get_commatext('abc') == 3)
    self.assertTrue(get_commatext('abc', 1) == 3)
    self.assertTrue(get_commatext(' abc') == 0)
    self.assertTrue(get_commatext(' abc', 1) == 4)
    self.assertTrue(get_commatext('ab"c d"ef') == 9)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
