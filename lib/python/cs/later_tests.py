#!/usr/bin/python
#
# Self tests for cs.later.
#       - Cameron Simpson <cs@zip.com.au>
#

from functools import partial
import sys
import time
import unittest
from cs.logutils import D
from cs.queues import QueueIterator
from cs.timeutils import sleep
from cs.threads import report
from cs.later import Later

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
    self.assertTrue(elapsed >= 4)

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

  def test08delay(self):
    with Later(3) as L3:
      LF1 = L3

  def test09pipeline_00noop(self):
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']
    outQ = L.pipeline([], items)
    self.assertIs(outQ, items)
    result = list(outQ)
    self.assertEquals( items, result )

  def test09pipeline_01idenitity(self):
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']
    def func(x):
      yield x
    outQ = L.pipeline([ func ], items)
    self.assertIsNot(outQ, items)
    self.assertIsInstance(outQ, QueueIterator)
    result = list(outQ)
    self.assertEquals( items, result )

  def test09pipeline_02double(self):
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']
    expected = ['a', 'a', 'b', 'b', 'c', 'c', 'g', 'g', 'f', 'f', 'e', 'e']
    def func(x):
      yield x
      yield x
    outQ = L.pipeline([ func ], items)
    self.assertIsNot(outQ, items)
    self.assertIsInstance(outQ, QueueIterator)
    result = list(outQ)
    # values may be interleaved due to parallelism
    self.assertEquals( len(result), len(expected) )
    self.assertEquals( sorted(result), sorted(expected) )

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
