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
from cs.threads import TimerQueue

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

  def test01timeorder1(self):
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

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
