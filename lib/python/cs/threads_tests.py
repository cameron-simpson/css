#!/usr/bin/python
#
# Self tests for cs.threads.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import print_function
import sys
from functools import partial
import time
import unittest
from cs.debug import thread_dump
from cs.logutils import X
from .threads import WorkerThreadPool

class TestWorkerThreadPool(unittest.TestCase):

  def setUp(self):
    self.pool = WorkerThreadPool()

  def tearDown(self):
    self.pool.shutdown()

  def _testfunc(self, *a, **kw):
    ##X("_testfunc: a=%r, kw=%r", a, kw)
    return a, kw

  def test00null(self):
    pass

  def test01run1(self):
    f = partial(self._testfunc, 1,2,3, a=4, b=5)
    def deliver(result):
      ##X("result = %r", result)
      pass
    self.pool.dispatch(f, deliver=deliver)

  def test01run16(self):
    def deliver(result):
      ##X("result = %r", result)
      pass
    for n in range(16):
      f = partial(self._testfunc, 1,2,3, a=4, b=5, n=n)
      self.pool.dispatch(f, deliver=deliver)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
