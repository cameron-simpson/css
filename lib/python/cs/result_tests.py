#!/usr/bin/python

''' Self tests for cs.result.
    - Cameron Simpson <cs@cskk.id.au>
'''

import sys
import threading
import time
import unittest

from cs.result import CancellationError, Result, after, bg

class TestResult(unittest.TestCase):
  ''' Tests for `cs.result`.
  '''

  def setUp(self):
    '''Prepare a `Result` for testing.'''
    self.R = Result()

  def test00result(self):
    '''Exercise a `Result` in basic ways.'''
    R = self.R
    self.assertFalse(R.ready)
    countery = [0]
    self.assertEqual(countery[0], 0)

    def count(_):
      '''Bump a counter.'''
      countery[0] += 1

    count(None)
    self.assertEqual(countery[0], 1)
    R.notify(count)
    self.assertFalse(R.ready)
    self.assertEqual(countery[0], 1)
    R.put(9)
    self.assertTrue(R.ready)
    self.assertEqual(R.get(), 9)
    self.assertEqual(countery[0], 2)
    R.notify(count)
    self.assertTrue(R.ready)
    self.assertEqual(countery[0], 3)

  def test01after(self):
    '''Exercise `cs.result.after`.'''
    R = self.R
    self.assertFalse(R.ready)
    R2 = Result()
    self.assertFalse(R2.ready)

    def add_R_R2():
      '''Add `R.result + R2.result`.'''
      value = R.result + R2.result
      return value

    A = after([R, R2], None, add_R_R2)
    self.assertFalse(A.ready)
    self.assertFalse(R.ready)
    self.assertFalse(R2.ready)

    def delayed_completion():
      '''Complete `Result`s after delays.'''
      time.sleep(0.2)
      R.result = 1
      time.sleep(0.2)
      R2.result = 2

    threading.Thread(target=delayed_completion).start()
    Aresult = A.get()
    self.assertEqual(Aresult, 3)
    self.assertTrue(A.ready)
    self.assertTrue(R.ready)
    self.assertTrue(R2.ready)

  def test02bg(self):
    '''Exercise `Result.bg`.'''
    R = self.R
    self.assertFalse(R.ready)

    def f(n):
      '''Return `n` after a brief delay.'''
      time.sleep(0.1)
      return n

    T = R.bg(f, 3)
    self.assertIsInstance(T, threading.Thread)
    self.assertFalse(R.ready)
    time.sleep(0.2)
    self.assertTrue(R.ready)
    self.assertEqual(R.result, 3)

  def test02bg2(self):
    '''Exercise `cs.result.bg`.'''

    def f(n):
      '''Return `n` after a brief delay.'''
      time.sleep(0.1)
      return n

    R = bg(f, 3)
    self.assertFalse(R.ready)
    time.sleep(0.2)
    self.assertTrue(R.ready)
    self.assertEqual(R.result, 3)

  def test03cancel(self):
    '''Cancel a `Result`.'''
    R = Result()
    R.cancel()
    self.assertRaises(CancellationError, R)

  def test04cancel_running(self):
    '''Cancel a `Result` while function running.'''

    def f(n):
      time.sleep(2)
      return n

    R = Result()
    R.bg(f, 3, _daemon=True)
    time.sleep(0.2)
    R.cancel()
    self.assertRaises(CancellationError, R)

  def test00join(self):
    '''Exercise `Result.join`.'''
    R = self.R
    R.result = 1
    R.join()
    R.join()

  def test01post_notify(self):
    '''Exercise `Result.post_notify`.'''
    R = self.R
    R2 = R.post_notify(lambda _: None)
    R.result = 1
    time.sleep(0.1)
    self.assertTrue(R2.ready)

def selftest(argv):
  '''RUn the unit tests.'''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
