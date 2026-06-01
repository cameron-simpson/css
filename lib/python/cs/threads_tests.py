#!/usr/bin/python
#
# Self tests for cs.threads.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Tests for cs.threads.
'''

from __future__ import print_function
import sys
from functools import partial
import os
from threading import Semaphore
import time
import unittest

from .threads import bg, pmap, PriorityLock, PriorityLockSubLock

class TestPriorityLock(unittest.TestCase):
  ''' Unit tests for `PriorityLock`.
  '''

  def setUp(self):
    self.lock = PriorityLock()

  def test00default1(self):
    self.assertEqual(self.lock.default_priority, 0)

  def test00default2(self):
    lock2 = PriorityLock(2)
    self.assertEqual(lock2.default_priority, 2)

  def test01acquire1(self):
    lock = self.lock
    for test_priority in lock.default_priority, 2, 0, 4:
      with self.subTest(priority=test_priority):
        my_lock = lock.acquire(test_priority)
        self.assertIsInstance(my_lock, PriorityLockSubLock)
        self.assertEqual(my_lock.priority, test_priority)
        self.assertIs(my_lock.priority_lock, lock)
        lock.release()

  def test01content1(self):
    with self.lock as L:
      self.assertIsInstance(L, PriorityLockSubLock)

  def test02mutex(self):
    lock = self.lock

    def sleeper(n):
      with lock as L:
        time.sleep(n)

    now = time.time()
    T1 = bg(sleeper, args=(0.1,), daemon=True)
    T2 = bg(sleeper, args=(0.1,), daemon=True)
    T1.join()
    T2.join()
    elapsed = time.time() - now
    self.assertGreaterEqual(elapsed, 0.2)

  def test03priority(self):
    lock = self.lock
    pri_list = []

    def sleeper(n, pri):
      with lock.priority(pri) as L:
        pri_list.append(pri)
        time.sleep(n)

    now = time.time()
    Ts = []
    with lock as L:
      for pri in 0, 1, 5, 7, 2, -5, 5, 5, 0:
        T = bg(sleeper, args=(0.1, pri), daemon=True)
        Ts.append(T)
    for T in Ts:
      T.join()
    self.assertEqual(pri_list, sorted(pri_list))

class TestPMap(unittest.TestCase):

  def test_variations(self):
    ''' All the flag combinations.

        Note the setting of `FAST_MODES` below to `True`.
        Running all the combinations takes a long time in series.
        For typical use we use pmap() itself to run them in parallel.
        Provided pmap's working, that's great.
        When being thorough or looking for problems in pmap, choose `False`
        to use map() to run each combination in series.
    '''
    # True runs the combinations in parallel _using pmap() itself_,
    # good for when things are working. False runs the combinations
    # using map(). Good for expected regressions.
    FAST_MODES = (os.environ.get("FAST", ""),)

    def genmodes(**fixed_modes):
      for n in 0, 1, 5, 17:
        for concurrent in 1, 3, 20, None, Semaphore(4):
          for unordered in False, True:
            for indexed in False, True:
              for with_exceptions in False, True:
                modes = dict(
                    n=n,
                    concurrent=concurrent,
                    unordered=unordered,
                    indexed=indexed,
                    with_exceptions=with_exceptions,
                )
                for fixed, value in fixed_modes.items():
                  if modes[fixed] != value:
                    print("skip", modes)
                    break
                else:
                  n = modes.pop('n')
                  yield n, modes

    def test_pmap(n_modes):

      def sleep_n(n):
        delay = 0.1 * n
        time.sleep(delay)
        return n

      n, modes = n_modes
      with self.subTest(n=n, **modes):
        count_down = range(n, 0, -1)
        start = time.time()
        results = list(pmap(sleep_n, iter(count_down), **modes))
        elapsed = time.time() - start
        self.assertEqual(len(results), n)
        concurrent = modes['concurrent']
        indexed = modes['indexed']
        unordered = modes['unordered']
        with_exceptions = modes['with_exceptions']
        if (concurrent is None
            or (isinstance(concurrent, int) and concurrent >= n)
            or (isinstance(concurrent, Semaphore) and concurrent._value >= n)):
          # all the sleeps should run concurrently
          self.assertLess(elapsed, (n + 1) * 0.15)
          if not fast: print("  elapsed OK")
        if concurrent == 1 or not unordered:
          # all the results should arrive in count_down order
          expected_results = [
              (
                  ((i, (n, None)) if indexed else
                   (n, None)) if with_exceptions else
                  ((i, n) if indexed else n)
              ) for i, n in enumerate(count_down)
          ]
          self.assertEqual(results, expected_results)
          if not fast: print("  order ok")
      if not fast: print("tested", n, modes)
      return n, modes

    for fast in FAST_MODES:
      test_modes = genmodes(
          ## n=5,
          ## concurrent=None,
          ## indexed=False,
          ## with_exceptions=False,
          ## unordered=True,
      )
      if fast:
        tested = pmap(test_pmap, test_modes)
      else:
        tested = map(test_pmap, test_modes)
      ##for n, modes in tested:
      for n, modes in tested:
        pass

def selftest(argv):
  ''' Run unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
