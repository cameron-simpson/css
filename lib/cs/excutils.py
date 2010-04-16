#!/usr/bin/python -tt
#
# Convenience facilities for exceptions.
#       - Cameron Simpson <cs@zip.com.au>
#

import logging
from cs.logutils import log, warn, exception

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
      exception("IGNORE "+str(exc_type)+": "+str(exc_value)+`tb`)
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
