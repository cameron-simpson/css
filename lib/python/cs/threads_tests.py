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
import unittest
from .threads import WorkerThreadPool

class TestWorkerThreadPool(unittest.TestCase):
  ''' Unit tests for WorkerThreadPool.
  '''

  def setUp(self):
    ''' Start up the test thread pool.
    '''
    self.pool = WorkerThreadPool()

  def tearDown(self):
    ''' Shut down the test thread pool.
    '''
    self.pool.shutdown()

  @staticmethod
  def _testfunc(*a, **kw):
    ##X("_testfunc: a=%r, kw=%r", a, kw)
    return a, kw

  def test00null(self):
    ''' Null test: setUp and tearDown.
    '''
    pass

  def test01run1(self):
    ''' Dispatch a single function.
    '''
    f = partial(self._testfunc, 1, 2, 3, a=4, b=5)
    def deliver(result):
      ''' Dummy function to receive the pool result.
      '''
      ##X("result = %r", result)
      pass
    self.pool.dispatch(f, deliver=deliver)

  def test01run16(self):
    ''' Dispatch many parallel functions.
    '''
    def deliver(result):
      ''' Dummy function to receive the pool result.
      '''
      ##X("result = %r", result)
      pass
    for n in range(16):
      f = partial(self._testfunc, 1, 2, 3, a=4, b=5, n=n)
      self.pool.dispatch(f, deliver=deliver)

def selftest(argv):
  ''' Run unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
