#!/usr/bin/python
#
# Self tests for cs.threads.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import time
import unittest
if sys.hexversion < 0x03000000:
  from Queue import Queue
else:
  from queue import Queue
from cs.threads import TimerQueue, runTree, RunTreeOp, RUN_TREE_OP_ONE_TO_MANY
from cs.later import Later
##from cs.logutils import D

def D(msg, *a):
  if a:
    msg = msg % a
  with open('/dev/tty', 'a') as tty:
    print >>tty, msg

class TestTimerQueue(unittest.TestCase):

  def setUp(self):
    self.TQ = TimerQueue()
    self.Q = Queue()

  def tearDown(self):
    self.TQ.close()

  def test00now(self):
    t0 = time.time()
    self.TQ.add(time.time(), lambda: self.Q.put(None))
    self.Q.get()
    t1 = time.time()
    self.assert_(t1-t0 < 0.1, "took too long to run a function 'now'")

  def test01later1(self):
    t0 = time.time()
    self.TQ.add(time.time()+1, lambda: self.Q.put(None))
    self.Q.get()
    t1 = time.time()
    self.assert_(t1-t0 >= 1, "ran function earlier than now+1")

  def test02timeorder1(self):
    t0 = time.time()
    self.TQ.add(time.time()+3, lambda: self.Q.put(3))
    self.TQ.add(time.time()+2, lambda: self.Q.put(2))
    self.TQ.add(time.time()+1, lambda: self.Q.put(1))
    x = self.Q.get()
    self.assertEquals(x, 1, "expected 1, got x=%s" % (x,))
    t1 = time.time()
    self.assert_(t1-t0 < 1.1, "took more than 1.1s to get first result")
    y = self.Q.get()
    self.assertEquals(y, 2, "expected 2, got y=%s" % (y,))
    t1 = time.time()
    self.assert_(t1-t0 < 2.1, "took more than 2.1s to get second result")
    z = self.Q.get()
    self.assertEquals(z, 3, "expected 3, got z=%s" % (z,))
    t1 = time.time()
    self.assert_(t1-t0 < 3.1, "took more than 3.1s to get third result")

class TestRuntree(unittest.TestCase):

  def setUp(self):
    self.TQ = TimerQueue()
    self.Q = Queue()

  def tearDown(self):
    self.TQ.close()

  # A many to many identity function.
  @staticmethod
  def f_same(input, state):
    return input
  # A one to (one,) identity function.
  @staticmethod
  def f_same_one2many(input, state):
    return (input,)
  @staticmethod
  def f_incr(items, state):
    return [ n+1 for n in items ]

  def test_00_helpers(self):
    self.assertEquals(self.f_same((1,), None), (1,))
    self.assertEquals(self.f_incr((1,2), None), [2,3])

  def test__01_no_operators(self):
    L = Later(1)
    self.assertEquals(runTree( [1,2,3], [], None, L), [1,2,3])
    L.close()

  def test__01_same(self):
    L = Later(1)
    self.assertEquals(list(runTree( [1,2,3], [ RunTreeOp(self.f_same, False, False, None) ], None, L )), [1,2,3])
    L.close()

  def test__01_same_fork(self):
    L = Later(1)
    self.assertEquals(list(runTree( [1,2,3], [ RunTreeOp(self.f_same_one2many, True, True, RUN_TREE_OP_ONE_TO_MANY) ], None, L)), [1,2,3])
    L.close()

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
