#!/usr/bin/python
#
# Self tests for cs.asynchron.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import print_function
import sys
import threading
import time
import unittest
from cs.asynchron import Result, after
from cs.logutils import X

def D(msg, *a):
  if a:
    msg = msg % a
  with open('/dev/tty', 'a') as tty:
    print(msg, file=tty)

class TestResult(unittest.TestCase):

  def setUp(self):
    self.R = Result()

  def test00result(self):
    R = self.R
    self.assertFalse(R.ready)
    countery = [0]
    self.assertEqual(countery[0], 0)
    def count(innerR):
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
    R = self.R
    self.assertFalse(R.ready)
    R2 = Result()
    self.assertFalse(R2.ready)
    def add_R_R2():
      X("ADD_R_R2...")
      value = R.result + R2.result
      X("ADD_R_R2 added")
      return value
    A = after([R, R2], None, add_R_R2)
    X("after ==> A=%s", A)
    self.assertFalse(A.ready)
    self.assertFalse(R.ready)
    self.assertFalse(R2.ready)
    def delayed_completion():
      X("delayed: sleep 2...")
      time.sleep(2)
      X("delayed: R.result=1")
      R.result = 1
      X("delayed: sleep 2...")
      time.sleep(2)
      X("delayed: R2.result=2")
      R2.result = 2
      X("delayed: complete")
    X("dispatch delayed")
    threading.Thread(target=delayed_completion).start()
    X("A.get...")
    Aresult = A.get()
    X("A done")
    self.assertEqual(Aresult, 3)
    self.assertTrue(A.ready)
    self.assertTrue(R.ready)
    self.assertTrue(R2.ready)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
