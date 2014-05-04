#!/usr/bin/python
#
# Self tests for cs.asynchron.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import print_function
import sys
import time
import unittest
from cs.asynchron import Result

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

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
