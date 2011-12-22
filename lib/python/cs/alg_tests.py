#!/usr/bin/python
#
# Self tests for cs.alg module.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.alg import collate

class TestAlg(unittest.TestCase):

  class Klass(object):
    def __init__(self, a, b):
      self.a = a
      self.b = b

  def test00nothing(self):
    self.assertEqual(collate( (), 'foo' ), {})

  def test01badAttr(self):
    O = self.Klass(1,2)
    self.assertRaises(AttributeError, collate, (O,), 'foo' )

  def test02basic(self):
    O1 = self.Klass(1,2)
    O2 = self.Klass(3,4)
    self.assertEqual( collate((O1, O2), 'a'), {1: [O1], 3: [O2]} )

  def test02basic(self):
    O1 = self.Klass(1,2)
    O2 = self.Klass(3,4)
    self.assertEqual( collate((O1, O2), 'a'), {1: [O1], 3: [O2]} )

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
