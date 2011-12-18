#!/usr/bin/python
#
# Self tests for cs.curlytplt.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.curlytplt import curly_substitute

class TestAll(unittest.TestCase):

  @staticmethod
  def _mapfn(x):
    return { 'foo': [1,2],
             'bah': [3,4],
             'zit': ['one'],
             'zot': []
           }[x]

  def test01curly(self):
    self.assertEquals(curly_substitute( 'text', TestAll._mapfn ), 'text')
    self.assertEquals(curly_substitute( 'a {foo} b', TestAll._mapfn ), 'a [1, 2] b')
    self.assertEquals(curly_substitute( 'a {bah} b', TestAll._mapfn ), 'a [3, 4] b')
    self.assertEquals(curly_substitute( 'a {foo}x{bah} b', TestAll._mapfn ), 'a [1, 2]x[3, 4] b')
    self.assertRaises(KeyError, curly_substitute, 'a {ZZZ} b', TestAll._mapfn)
    self.assertEquals(curly_substitute( 'a {ZZZ} b', TestAll._mapfn, safe=True), 'a {ZZZ} b')

  def test01curlycurly(self):
    self.assertEquals(curly_substitute( 'text', TestAll._mapfn, permute=True ), 'text')
    self.assertEquals(curly_substitute( 'a {foo} b', TestAll._mapfn, permute=True ), 'a [1, 2] b')
    self.assertEquals(curly_substitute( 'a {{foo}} b', TestAll._mapfn, permute=True ), 'a 1 ba 2 b')
    self.assertEquals(curly_substitute( 'a {bah} b', TestAll._mapfn, permute=True ), 'a [3, 4] b')
    self.assertEquals(curly_substitute( 'a {foo}x{bah} b', TestAll._mapfn, permute=True ), 'a [1, 2]x[3, 4] b')
    self.assertEquals(curly_substitute( 'a {{foo}}x{{bah}} b', TestAll._mapfn, permute=True ), 'a 1x3 ba 1x4 ba 2x3 ba 2x4 b')
    self.assertEquals(curly_substitute( 'a {{foo}}x{{zit}}x{{bah}} b', TestAll._mapfn, permute=True ), 'a 1xonex3 ba 1xonex4 ba 2xonex3 ba 2xonex4 b')
    self.assertRaises(KeyError, curly_substitute, 'a {ZZZ} b', TestAll._mapfn, permute=True)
    self.assertRaises(KeyError, curly_substitute, 'a {{ZZZ}} b', TestAll._mapfn, permute=True)
    self.assertEquals(curly_substitute( 'a {ZZZ} b', TestAll._mapfn, permute=True, safe=True), 'a {ZZZ} b')
    self.assertEquals(curly_substitute( 'a {{ZZZ}} b', TestAll._mapfn, permute=True, safe=True), 'a {{ZZZ}} b')

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
