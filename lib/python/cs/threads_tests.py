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
import time
import unittest
from .threads import bg, PriorityLock, PriorityLockSubLock

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

def selftest(argv):
  ''' Run unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
