#!/usr/bin/python
#
# Self tests for cs.seq.
#       - Cameron Simpson <cs@cskk.id.au>
#

import sys
import unittest
from cs.seq import imerge, onetoone, onetomany, Seq

class TestSeq(unittest.TestCase):
  ''' Tests for `cs.seq.Seq`.
  '''

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def test_imerge(self):
    self.assertEqual(
        list(imerge([1, 2, 3], [1, 4, 7], [2, 5, 6])),
        [1, 1, 2, 2, 3, 4, 5, 6, 7]
    )

  def test_onetoone(self):

    class C(list):

      @onetoone
      def lower(item):
        return item.lower()

    L = C(['Abc', 'Def'])
    I2 = L.lower()
    self.assertEqual(list(I2), ['abc', 'def'])

  def test_onetomany(self):

    class C(list):

      @onetomany
      def angles(item):
        return ["<%s>" % (i,) for i in item]

    L = C(['Abc', 'Def'])
    I2 = L.angles()
    self.assertEqual(list(I2), ['<A>', '<b>', '<c>', '<D>', '<e>', '<f>'])

  def test_Seq(self):
    a = []
    s = Seq()
    for i in range(16):
      a.append(s.next())
    self.assertEqual(a, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
