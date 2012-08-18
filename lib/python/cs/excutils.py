#!/usr/bin/python -tt
#
# Convenience facilities for exceptions.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import logging
import traceback
from cs.logutils import log, warning, exception, error, D

def return_exc_info(func, *args, **kwargs):
  ''' Run the supplied function and arguments.
      Return:
        func_return, None
      in the case of successful operation and:
        None, exc_info
      in the case of an exception. `exc_info` is a 3-tuple of
      exc_type, exc_value, exc_traceback as returned by sys.exc_info().
      If you need to protect a whole suite and would rather not move it
      into its own function, consider the NoExceptions context manager.
  '''
  try:
    result = func(*args, **kwargs)
  except:
    return None, tuple(sys.exc_info())
  return result, None

def returns_exc_info(func):
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
    return return_exc_info(func, *args, **kwargs)
  return wrapper

def noexc(func):
  ''' Decorator to wrap a function which should never raise an exception.
      Instead, any raised exception is attempted to be logged.
      A significant side effect is of course that if the function raises an
      exception it now return None.
      My primary use case is actually to wrap logging functions,
      which I have had abort otherwise sensible code.
  '''
  def wrapper(*args, **kwargs):
    try:
      return func(*args, **kwargs)
    except Exception as e:
      try:
        exception("exception calling %s(%s, **(%s))", func.__name__, args, kwargs)
      except Exception as e:
        try:
          D("exception calling %s(%s, **(%s)): %s", func.__name__, args, kwargs, e)
        except Exception:
          pass
  return wrapper

class NoExceptions(object):
  ''' A context manager to catch _all_ exceptions and log them.
      Arguably this should be a bare try...except but that's syntacticly
      noisy and separates the catch from the top.
      For simple function calls return_exc_info() is probably better.
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

if __name__ == '__main__':
  import cs.excutils_tests
  cs.excutils_tests.selftest(sys.argv)
