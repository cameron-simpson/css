#!/usr/bin/python
#
# Unit tests for cs.range.
#       - Cameron Simpson <cs@cskk.id.au>
#

import sys
import unittest
from random import randint
from cs.range import Range, overlap, spans, Span

class TestAll(unittest.TestCase):
  ''' Tests for `cs.range`.
  '''

  def setUp(self):
    self.items1 = [1, 2, 3, 7, 8, 11, 5]
    self.spans1 = [Span(1, 4), Span(5, 6), Span(7, 9), Span(11, 12)]
    self.items2 = [3, 5, 6, 8, 9, 10, 15, 16, 19]
    self.spans2 = [
        Span(3, 4),
        Span(5, 7),
        Span(8, 11),
        Span(15, 17),
        Span(19, 20)
    ]
    self.items1plus2 = [1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 15, 16, 19]
    self.spans1plus2 = [Span(1, 4), Span(5, 12), Span(15, 17), Span(19, 20)]
    self.items1minus2 = [1, 2, 7, 11]
    self.spans1minus2 = [Span(1, 3), Span(7, 8), Span(11, 12)]
    self.items1xor2 = [1, 2, 6, 7, 9, 10, 11, 15, 16, 19]
    self.spans1xor2 = [
        Span(1, 3),
        Span(6, 8),
        Span(9, 12),
        Span(15, 17),
        Span(19, 20)
    ]

  def test00spans(self):
    self.assertNotEqual(list(spans(self.items1)), self.spans1)
    self.assertEqual(list(spans(sorted(self.items1))), self.spans1)
    self.assertEqual(list(spans(self.items2)), self.spans2)

  def test01overlap(self):
    self.assertEqual(overlap([1, 2], [3, 4]), [3, 3])

  def test10init(self):
    R0 = Range()
    R0._check()
    self.assertEqual(list(R0.spans()), [])
    R0.update(self.items1)
    R0._check()
    self.assertEqual(list(R0.spans()), self.spans1)
    R1 = Range(self.items1)
    R1._check()
    self.assertEqual(list(R1.spans()), self.spans1)
    self.assertEqual(R0, R1)
    R2 = Range(self.items2)
    R2._check()
    self.assertEqual(list(R2.spans()), self.spans2)

  def test11equals(self):
    R1 = Range(self.items1)
    self.assertEqual(R1, R1)
    self.assertEqual(list(iter(R1)), sorted(self.items1))

  def test12copy(self):
    R1 = Range(self.items1)
    R2 = R1.copy()
    R2._check()
    self.assertTrue(R1 is not R2, "R1 is R2")
    self.assertEqual(R1, R2)
    self.assertEqual(R1._spans, R2._spans)
    self.assertEqual(list(R1.spans()), list(R2.spans()))

  def test13update00fromItems(self):
    R1 = Range(self.items1)
    R1._check()
    R1.update(self.items2)
    R1._check()
    self.assertEqual(list(R1), self.items1plus2)
    self.assertEqual(list(R1.spans()), self.spans1plus2)

  def test13update01fromSpans(self):
    R1 = Range(self.items1)
    R1._check()
    for span in self.spans2:
      R1.add_span(span[0], span[1])
      R1._check()
    self.assertEqual(list(R1), self.items1plus2)
    self.assertEqual(list(R1.spans()), self.spans1plus2)

  def test13update02fromRange(self):
    R1 = Range(self.items1)
    R1._check()
    R2 = Range(self.items2)
    R2._check()
    R1.update(R2)
    R1._check()
    self.assertEqual(list(R1), self.items1plus2)
    self.assertEqual(list(R1.spans()), self.spans1plus2)

  def test14union(self):
    R1 = Range(self.items1)
    R1._check()
    R2 = Range(self.items2)
    R2._check()
    R3 = R1.union(R2)
    R3._check()
    self.assertEqual(list(R3), self.items1plus2)
    self.assertEqual(list(list(R3.spans())), self.spans1plus2)

  def test15discard(self):
    R1 = Range(self.items1)
    R1._check()
    R2 = Range(self.items2)
    R2._check()
    ##X("R1 = %s", R1)
    ##X("R2 = %s", R2)
    R1.discard(R2)
    R1._check()
    ##X("post discard, R1 = %s", R1)
    ##X("items1minus2 = %s", self.items1minus2)
    self.assertEqual(list(R1), self.items1minus2)
    self.assertEqual(list(list(R1.spans())), self.spans1minus2)

  def test16remove(self):
    R1 = Range(self.items1)
    R1._check()
    R1.remove(3)
    R1._check()
    self.assertRaises(KeyError, R1.remove, 3)
    R1._check()

  def test17difference_subset_superset(self):
    R1 = Range(self.items1)
    R1._check()
    R2 = Range(self.items2)
    R2._check()
    R3 = R1.difference(R2)
    R3._check()
    self.assertEqual(list(R3), self.items1minus2)
    self.assertEqual(list(list(R3.spans())), self.spans1minus2)
    self.assertTrue(R1.issuperset(R3))
    self.assertTrue(R3.issubset(R1))
    R4 = R1 - R2
    R4._check()
    self.assertEqual(list(R4), self.items1minus2)
    self.assertEqual(list(list(R4.spans())), self.spans1minus2)
    self.assertTrue(R1.issuperset(R4))
    self.assertTrue(R4.issubset(R1))

  def test17symmetric_difference(self):
    R1 = Range(self.items1)
    R1._check()
    R2 = Range(self.items2)
    R2._check()
    R3 = R1.symmetric_difference(R2)
    R3._check()
    self.assertEqual(list(R3), self.items1xor2)
    self.assertEqual(list(list(R3.spans())), self.spans1xor2)
    R4 = R1 ^ R2
    R4._check()
    self.assertEqual(list(R4), self.items1xor2)
    self.assertEqual(list(list(R4.spans())), self.spans1xor2)
    self.assertEqual(R4, R3)
    self.assertTrue(R4 is not R3, "R4 is R3")

  def test30random_set_equivalence(self):
    R1 = Range()
    S = set()
    self.assertEqual(S, set(R1))
    for i in range(100):
      n = randint(0, 99)
      if randint(0, 1):
        ##X("add %d (new=%s)", n, n in S)
        R1.add(n)
        S.add(n)
      else:
        ##X("discard %d (extant=%s)", n, n in S)
        R1.discard(n)
        S.discard(n)
      ##X("S = %s", S)
      ##X("R1 = %s", R1)
      self.assertEqual(S, set(R1))  ## "set:%s vs Range:%s" % (S, R1))

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
