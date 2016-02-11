#!/usr/bin/python
#
# Self tests for cs.later.
#       - Cameron Simpson <cs@zip.com.au>
#

from functools import partial
import sys
import time
import unittest
from cs.logutils import D, setup_logging
from cs.timeutils import sleep
from cs.asynchron import report
from cs.later import Later, FUNC_ONE_TO_ONE, FUNC_ONE_TO_MANY, FUNC_MANY_TO_MANY, FUNC_SELECTOR

class TestLater(unittest.TestCase):

  @staticmethod
  def _f(x):
    return x*2
  @staticmethod
  def _delay(n):
    sleep(n)
    return n
  class _Bang(Exception):
    pass
  @staticmethod
  def _bang():
    raise TestLater._Bang()

  def setUp(self):
    self.L = Later(2)
    self.L.logTo("/dev/tty")
    self.L.open()

  def tearDown(self):
    self.L.close()

  def test00one(self):
    # compute 3*2
    L = self.L
    F = partial(self._f, 3)
    LF = L.defer(F)
    x = LF()
    self.assertEqual(x, 6)

  def test01two(self):
    # two sleep(2) in parallel
    L = self.L
    F = partial(self._delay, 2)
    LF1 = L.defer(F)
    LF2 = L.defer(F)
    now = time.time()
    x = LF1()
    y = LF2()
    again = time.time()
    elapsed = again - now
    self.assertTrue(elapsed < 3, "elapsed (%s) >= 3, now = %s, again = %s" % (elapsed, now, again))

  def test02three(self):
    # three sleep(2), two in parallel, one delayed
    L = self.L
    F = partial(self._delay, 2)
    LF1 = L.defer(F)
    LF2 = L.defer(F)
    LF3 = L.defer(F)
    now = time.time()
    x = LF1()
    y = LF2()
    z = LF3()
    elapsed = time.time() - now
    self.assertTrue(elapsed >= 4, "elapsed (%s) < 4" % (elapsed,))

  def test03calltwice(self):
    # compute once, get result twice
    L = self.L
    F = partial(self._f, 5)
    LF = L.defer(F)
    x = LF()
    self.assertEqual(x, 10)
    y = LF()
    self.assertEqual(y, 10)

  def test04raise(self):
    # raise exception
    LF = self.L.defer(self._bang)
    self.assertRaises(TestLater._Bang, LF)

  def test05raiseTwice(self):
    # raise exception again
    LF = self.L.defer(self._bang)
    self.assertRaises(TestLater._Bang, LF)
    self.assertRaises(TestLater._Bang, LF)

  def test06defer_with_args(self):
    # compute 7*2 using .defer_with_args()
    LF = self.L.defer(self._f, 7)
    x = LF()
    self.assertEqual(x, 14)

  def test07report(self):
    with Later(3) as L3:
      LF1 = L3.defer(self._delay, 3)
      LF2 = L3.defer(self._delay, 2)
      LF3 = L3.defer(self._delay, 1)
      results = [ LF() for LF in report( (LF1, LF2, LF3) ) ]
      self.assertEqual(results, [1, 2, 3])

  def test09pipeline_00noop(self):
    with Later(1) as L:
      items = ['a', 'b', 'c', 'g', 'f', 'e']
      P = L.pipeline([ (FUNC_ONE_TO_ONE, lambda x:x) ], items)
      outQ = P.outQ
      result = list(P.outQ)
      self.assertEqual( items, result )

  def test09pipeline_01idenitity(self):
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']
    def func(x):
      yield x
    P = L.pipeline([ (FUNC_ONE_TO_MANY, func) ], items)
    self.assertIsNot(P.outQ, items)
    result = list(P.outQ)
    self.assertEqual( items, result )

  def test09pipeline_02double(self):
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']
    expected = ['a', 'a', 'b', 'b', 'c', 'c', 'g', 'g', 'f', 'f', 'e', 'e']
    def func(x):
      yield x
      yield x
    P = L.pipeline([ (FUNC_ONE_TO_MANY, func) ], items)
    self.assertIsNot(P.outQ, items)
    result = list(P.outQ)
    # values may be interleaved due to parallelism
    self.assertEqual( len(result), len(expected) )
    self.assertEqual( sorted(result), sorted(expected) )

  def test09pipeline_03a_sort(self):
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']
    expected = ['a', 'b', 'c', 'e', 'f', 'g']
    def func(x):
      return sorted(x)
    P = L.pipeline([ (FUNC_MANY_TO_MANY, func) ], items)
    self.assertIsNot(P.outQ, items)
    result = list(P.outQ)
    self.assertEqual( result, sorted(items) )

  def test09pipeline_03b_set(self):
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']
    expected = ['a', 'b', 'c', 'e', 'f', 'g']
    def func(x):
      return set(x)
    P = L.pipeline([ (FUNC_MANY_TO_MANY, func) ], items)
    self.assertIsNot(P.outQ, items)
    result = set(P.outQ)
    self.assertEqual( result, set(items) )

  def test09pipeline_04select(self):
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']
    want = ('a', 'f', 'c')
    expected = ['a', 'c', 'f']
    def wanted(x):
      return x in want
    P = L.pipeline([ (FUNC_SELECTOR, wanted) ], items)
    self.assertIsNot(P.outQ, items)
    result = list(P.outQ)
    self.assertEqual( result, expected )

  def test09pipeline_05two_by_two_by_sort(self):
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']
    expected = [ 'a', 'a', 'a', 'a',
                 'b', 'b', 'b', 'b',
                 'c', 'c', 'c', 'c',
                 'e', 'e', 'e', 'e',
                 'f', 'f', 'f', 'f',
                 'g', 'g', 'g', 'g',
               ]
    def double(x):
      yield x
      yield x
    P = L.pipeline([ (FUNC_ONE_TO_MANY, double),
                     (FUNC_ONE_TO_MANY, double),
                     (FUNC_MANY_TO_MANY, sorted) ], items)
    self.assertIsNot(P.outQ, items)
    result = list(P.outQ)
    self.assertEqual( result, expected )

def selftest(argv):
  setup_logging()
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
