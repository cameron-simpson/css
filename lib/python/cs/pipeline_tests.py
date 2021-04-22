#!/usr/bin/python
#

''' Unit tests for the cs.pipeline module.
    - Cameron Simpson <cs@cskk.id.au>
'''

from functools import partial
import sys
import time
import unittest
from cs.timeutils import sleep
from cs.later import Later
from cs.pipeline import (
    pipeline, FUNC_ONE_TO_ONE, FUNC_ONE_TO_MANY, FUNC_MANY_TO_MANY,
    FUNC_SELECTOR
)
from cs.result import report

class TestPipeline(unittest.TestCase):
  ''' Unit tests for pipelines.
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
    raise TestPipeline._Bang()

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

  def test09pipeline_00noop(self):
    ''' Run a single stage one to one no-op pipeline.
    '''
    with Later(1) as L:
      items = ['a', 'b', 'c', 'g', 'f', 'e']
      P = pipeline(L, [(FUNC_ONE_TO_ONE, lambda x: x)], items)
      result = list(P.outQ)
      self.assertEqual(items, result)

  def test09pipeline_01idenitity(self):
    ''' Run a single stage one to many no-op pipeline.
    '''
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']

    def func(x):
      yield x

    P = pipeline(L, [(FUNC_ONE_TO_MANY, func)], items)
    self.assertIsNot(P.outQ, items)
    result = list(P.outQ)
    self.assertEqual(items, result)

  def test09pipeline_02double(self):
    ''' Run a single stage one to many pipeline.
    '''
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']
    expected = ['a', 'a', 'b', 'b', 'c', 'c', 'g', 'g', 'f', 'f', 'e', 'e']

    def func(x):
      yield x
      yield x

    P = pipeline(L, [(FUNC_ONE_TO_MANY, func)], items)
    self.assertIsNot(P.outQ, items)
    result = list(P.outQ)
    # values may be interleaved due to parallelism
    self.assertEqual(len(result), len(expected))
    self.assertEqual(sorted(result), sorted(expected))

  def test09pipeline_03a_sort(self):
    ''' Run a single stage many to many pipeline doing a sort.
    '''
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']
    expected = ['a', 'b', 'c', 'e', 'f', 'g']

    def func(x):
      return sorted(x)

    P = pipeline(L, [(FUNC_MANY_TO_MANY, func)], items)
    self.assertIsNot(P.outQ, items)
    result = list(P.outQ)
    self.assertEqual(result, expected)

  def test09pipeline_03b_set(self):
    ''' Run a single stage man to many pipeline.
    '''
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']
    expected = ['a', 'b', 'c', 'e', 'f', 'g']

    def func(x):
      return set(x)

    P = pipeline(L, [(FUNC_MANY_TO_MANY, func)], items)
    self.assertIsNot(P.outQ, items)
    result = set(P.outQ)
    self.assertEqual(result, set(items))

  def test09pipeline_04select(self):
    ''' Run a single stage selection pipeline.
    '''
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']
    want = ('a', 'f', 'c')
    expected = ['a', 'c', 'f']

    def wanted(x):
      return x in want

    P = pipeline(L, [(FUNC_SELECTOR, wanted)], items)
    self.assertIsNot(P.outQ, items)
    result = list(P.outQ)
    self.assertEqual(result, expected)

  def test09pipeline_05two_by_two_by_sort(self):
    ''' Run a 3 stage pipeline with some fan out.
    '''
    L = self.L
    items = ['a', 'b', 'c', 'g', 'f', 'e']
    expected = [
        'a',
        'a',
        'a',
        'a',
        'b',
        'b',
        'b',
        'b',
        'c',
        'c',
        'c',
        'c',
        'e',
        'e',
        'e',
        'e',
        'f',
        'f',
        'f',
        'f',
        'g',
        'g',
        'g',
        'g',
    ]

    def double(x):
      yield x
      yield x

    P = pipeline(
        L, [
            (FUNC_ONE_TO_MANY, double), (FUNC_ONE_TO_MANY, double),
            (FUNC_MANY_TO_MANY, sorted)
        ], items
    )
    self.assertIsNot(P.outQ, items)
    result = list(P.outQ)
    self.assertEqual(result, expected)

def selftest(argv):
  ''' Run unit tests for cs.pipeline.
  '''
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
