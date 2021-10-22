#!/usr/bin/python
#

''' Self tests for cs.excutils.
    - Cameron Simpson <cs@cskk.id.au>
'''

import sys
import unittest
from cs.excutils import (
    return_exc_info, returns_exc_info,
    noexc, LogExceptions, logexc,
)

def _try_LogExceptions(e, conceal):
  # optionally fire off an exception, used in testing
  with LogExceptions(conceal=conceal):
    if e:
      raise e

def _try_logexc(e):
  # optionally fire off an exception, used in testing
  @logexc
  def f(e):
    if e:
      raise e

  f(e)

class TestExcUtils(unittest.TestCase):
  ''' Test suite for cs.excutils.
  '''

  def test_return_exc_info(self):
    ''' Test the return_exc_info function.
    '''

    def divfunc(a, b):
      return a // b

    retval, exc_info = return_exc_info(divfunc, 4, 2)
    self.assertEqual(retval, 2)
    self.assertTrue(exc_info is None)
    retval, exc_info = return_exc_info(divfunc, 4, 0)
    self.assertTrue(retval is None)
    self.assertTrue(exc_info[0] is ZeroDivisionError)

  def test_returns_exc_info(self):
    ''' Test the @returns_exc_info decorator.
    '''

    @returns_exc_info
    def divfunc(a, b):
      return a // b

    retval, exc_info = divfunc(4, 2)
    self.assertEqual(retval, 2)
    self.assertTrue(exc_info is None)
    retval, exc_info = divfunc(4, 0)
    self.assertTrue(retval is None)
    self.assertTrue(exc_info[0] is ZeroDivisionError)

  def test_noexc(self):
    ''' Test the @noexc decorator.
    '''

    def f(to_raise=None):
      if to_raise is not None:
        raise to_raise()
      return True

    self.assertIs(f(), True)
    self.assertRaises(Exception, f, Exception)
    f2 = noexc(f)
    self.assertIs(f2(), True)
    self.assertIs(f2(Exception), None)

  def test_LogExceptions(self):
    ''' Test the LogExceptions context manager.
    '''
    from cs.logutils import setup_logging
    setup_logging("test_LogExceptions")
    bang = RuntimeError("bang! testing LogException")
    _try_LogExceptions(None, conceal=True)
    _try_LogExceptions(None, conceal=False)
    _try_LogExceptions(bang, conceal=True)
    self.assertRaises(RuntimeError, _try_LogExceptions, bang, conceal=False)
    self.assertRaises(Exception, _try_LogExceptions, bang, conceal=False)

  def test_logexc(self):
    ''' Test the @logexc decorator.
    '''
    from cs.logutils import setup_logging
    setup_logging("test_logexc")
    bang = RuntimeError("bang! testing @logexc")
    _try_logexc(None)
    self.assertRaises(RuntimeError, _try_logexc, bang)
    self.assertRaises(Exception, _try_logexc, bang)

def selftest(argv):
  ''' Run unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
