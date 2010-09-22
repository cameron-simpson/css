#!/usr/bin/python -tt
#
# Convenience facilities for exceptions.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import logging
import traceback
import unittest
from cs.logutils import log, warn, exception, error

def return_excinfo(func, *args, **kwargs):
  ''' Run the supplied function and arguments.
      Return:
        func_return, None
      in the case of successful operation and:
        None, exc_info
      in the case of an exception. `exc_info` is a 3-tuple of
      exc_type, exc_value, exc_traceback as returned by sys.exc_info().
  '''
  try:
    result = func(*args, **kwargs)
  except Exception:
    return None, tuple(sys.exc_info)
  return result, None

def returns_excinfo(func):
  ''' Decorator function to wrap functions whose exceptions should be caught,
      such as inside event loops or worker threads.
      It causes a function to return:
        func_return, None
      in the case of successful operation and:
        None, exc_info
      in the case of an exception. `exc_info` is a 3-tuple of
      exc_type, exc_value, exc_traceback as returned by sys.exc_info().
  '''
  def wrapper(*args, **kwargs):
    return return_excinfo(func, *args, **kwargs)
  return wrapper

class NoExceptions(object):
  ''' A context manager to catch _all_ exceptions and log them.
      Arguably this should be a bare try...except but that's syntacticly
      noisy and separates the catch from the top.
  '''

  def __init__(self, handleException):
    ''' Initialise the NoExceptions context manager.
        The handleException is a callable which
        expects (exc_type, exc_value, traceback)
        and returns True or False for the __exit__
        method of the manager.
        If handleException is None, the __exit__ method
        always returns True, suppressing any exception.
    '''
    self.__handler = handleException

  def __enter__(self):
    pass

  def __exit__(self, exc_type, exc_value, tb):
    if exc_type is not None:
      if self.__handler is not None:
        # user supplied handler
        return self.__handler(exc_type, exc_value, tb)
      # report handled exception
      exception("IGNORE  "+str(exc_type)+": "+str(exc_value))
      for line in traceback.format_tb(tb):
        error("IGNORE> "+line[:-1])
    return True

  def simpleExceptionReport(exc_type, exc_value, traceback, mark=None, loglevel=logging.WARNING):
    ''' Convenience method to log exceptions to standard error.
    '''
    if mark is None:
      mark=cmd
    else:
      mark="%s: %s" % (cmd, mark)
    log("%s: EXCEPTION: %s: %s [%s]" % (mark, exc_type, exc_value, traceback), level=loglevel)
    return True
  # backward compatible static method arrangement
  simpleExceptionReport = staticmethod(simpleExceptionReport)

class TestExcUtils(unittest.TestCase):

  def test00return_excinfo(self):
    def divfunc(a, b):
      return a/b
    retval, exc_info = return_excinfo(divfunc, 4, 2)
    self.assertEquals(retval, 2)
    self.assertTrue(exc_info is None)
    retval, exc_info = return_excinfo(divfunc, 4, 0)
    self.assertTrue(retval is None)
    self.assertTrue(exc_info[0] is ZeroDivisionError)

  def test00returns_excinfo(self):
    @returns_excinfo
    def divfunc(a, b):
      return a/b
    retval, exc_info = divfunc(4, 2)
    self.assertEquals(retval, 2)
    self.assertTrue(exc_info is None)
    retval, exc_info = divfunc(4, 0)
    self.assertTrue(retval is None)
    self.assertTrue(exc_info[0] is ZeroDivisionError)

if __name__ == '__main__':
  unittest.main()
