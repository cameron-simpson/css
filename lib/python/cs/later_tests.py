#!/usr/bin/python
#
# Self tests for cs.later.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Unit tests for the cs.later module.
'''

from functools import partial
import sys
import time
import unittest
from cs.timeutils import sleep
from cs.later import Later
from cs.result import report

class TestLater(unittest.TestCase):
  ''' Unit tests for the Later class.
  '''

  @staticmethod
  def _f(x):
    return x * 2

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
    ''' Set up a Later, log to the terminal.
    '''
    self.L = Later(2)
    self.L.open()
    self.L.logTo("/dev/tty")

  def tearDown(self):
    ''' Close the Later.
    '''
    self.L.close()

  def test00one(self):
    ''' Compute 3*2.
    '''
    L = self.L
    F = partial(self._f, 3)
    LF = L.defer(F)
    x = LF()
    self.assertEqual(x, 6)

  def test01two(self):
    ''' Run two sleep(2) in parallel.
    '''
    L = self.L
    F = partial(self._delay, 2)
    LF1 = L.defer(F)
    LF2 = L.defer(F)
    now = time.time()
    LF1()
    LF2()
    again = time.time()
    elapsed = again - now
    self.assertTrue(
        elapsed < 3,
        "elapsed (%s) >= 3, now = %s, again = %s" % (elapsed, now, again)
    )

  def test02three(self):
    ''' Three sleep(2), two in parallel, one delayed.
    '''
    L = self.L
    F = partial(self._delay, 2)
    LF1 = L.defer(F)
    LF2 = L.defer(F)
    LF3 = L.defer(F)
    now = time.time()
    LF1()
    LF2()
    LF3()
    elapsed = time.time() - now
    self.assertTrue(elapsed >= 4, "elapsed (%s) < 4" % (elapsed,))

  def test03calltwice(self):
    ''' Run a LateFunction once, get results twice.
    '''
    L = self.L
    F = partial(self._f, 5)
    LF = L.defer(F)
    x = LF()
    self.assertEqual(x, 10)
    y = LF()
    self.assertEqual(y, 10)

  def test04raise(self):
    ''' A LateFunction which raises an exception.
    '''
    LF = self.L.defer(self._bang)
    self.assertRaises(TestLater._Bang, LF)

  def test05raiseTwice(self):
    ''' A LateFunction which raises an exception, called twice.
    '''
    LF = self.L.defer(self._bang)
    self.assertRaises(TestLater._Bang, LF)
    self.assertRaises(TestLater._Bang, LF)

  def test06defer_with_args(self):
    ''' Compute 7*2 using .defer_with_args().
    '''
    LF = self.L.defer(self._f, 7)
    x = LF()
    self.assertEqual(x, 14)

  def test07report(self):
    ''' Report LateFunctions in order of completion.
    '''
    with Later(3) as L3:
      LF1 = L3.defer(self._delay, 3)
      LF2 = L3.defer(self._delay, 2)
      LF3 = L3.defer(self._delay, 1)
      results = [LF() for LF in report((LF1, LF2, LF3))]
      self.assertEqual(results, [1, 2, 3])

def selftest(argv):
  ''' Run unit tests for cs.later.
  '''
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
