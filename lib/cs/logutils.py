#!/usr/bin/python
#
# Convenience routines for logging.
#       - Cameron Simpson <cs@zip.com.au> 29aug2009
#

from __future__ import with_statement
import logging
import sys
import time
import threading
import cs.misc

class NullHandler(logging.Handler):
  def emit(self, record):
    pass

''' Convenience do-nothing logging handler as suggested by:
      http://docs.python.org/library/logging.html#configuring-logging-for-a-library
'''
nullHandler = NullHandler()

''' Top level logger for the cs library.
'''
logger = logging.getLogger("cs")
logger.addHandler(nullHandler)

''' A mixin class to add logging convenience methods.
'''
class LoggingMixin(object):
  def __init__(self):
    self._logger = cs.logutils.logger
  def info(self, *args, **kwargs):
    self._logger.warning(*args, **kwargs)
  def warning(self, *args, **kwargs):
    self._logger.warning(*args, **kwargs)
  warn = warning
  def error(self, *args, **kwargs):
    self._logger.error(*args, **kwargs)
  def critical(self, *args, **kwargs):
    self._logger.critical(*args, **kwargs)

class _PrefixState(threading.local):
  def __init__(self):
    self.current = cs.misc.cmd
    self.raise_prefix = None
    self.prior = []
    self.logging_handler = None
_prefix = _PrefixState()

def current_prefix():
  ''' Return the current prefix value as used by the Pfx class.
  '''
  global _prefix
  return _prefix.current

class _PrefixLoggingHandler(logging.Handler):
  def emit(self, record):
    print >>sys.stderr, "%s: %s" % (current_prefix(), record.getMessage())

class _Pfx_LoggerAdapter(logging.LoggerAdapter):
  def process(self, msg, kwargs):
    return "%(cs.logutils.Pfx.prefix): "+msg, kwargs

class Pfx(object):
  ''' A context manager to maintain a per-thread stack of message prefices.
      The function current_prefix() returns the current prefix value.
  '''
  def __init__(self, mark, absolute=False, loggers=None):
    global _prefix
    # compute the new message prefix
    if absolute:
      newmark = mark
    else:
      newmark = _prefix.current + ': ' + str(mark)
    self.mark = newmark
    # make LoggerAdapters for all the specified loggers
    # to insert the prefix onto the messages
    if loggers is None:
      loggers = (logging.getLogger(), )
    elif not hasattr(loggers, '__getitem__'):
      loggers = (loggers, )
    extra = {'cs.logutils.Pfx.prefix': newmark}
    self.loggers = ( _Pfx_LoggerAdapter(L, extra) for L in loggers )
  def __enter__(self):
    global _prefix
    _prefix.prior.append( (_prefix.current, _prefix.raise_prefix) )
    _prefix.current = self.mark
    _prefix.raise_prefix = self.mark
  def __exit__(self, exc_type, exc_value, traceback):
    global _prefix
    pfx = _prefix.raise_prefix
    if pfx is not None:
      if exc_value is not None:
        if hasattr(exc_value, 'args') and len(exc_value.args) > 0:
          exc_value.args = [pfx + ": " + str(exc_value.args[0])] \
                         + list(exc_value.args[1:])
        else:
          # we can't modify this - at least report the current prefix state
          sys.stderr.write("%s: Pfx.__exit__: exc_value = %s\n" % (pfx, repr(exc_value),))
        pfx = None
    _prefix.current, _prefix.raise_prefix = _prefix.prior.pop()
    if pfx is None:
      _prefix.raise_prefix = None
    return False
  enter = __enter__
  exit = __exit__

  # Logger methods
  def exception(self, msg, *args):
    for L in self.loggers:
      L.exception(msg, *args)
  def log(self, level, msg, *args, **kwargs):
    for L in self.loggers:
      L.log(level, msg, *args, **kwargs)
  def debug(self, msg, *args, **kwargs):
    self.log(logging.DEBUG, msg, *args, **kwargs)
  def info(self, msg, *args, **kwargs):
    self.log(logging.INFO, msg, *args, **kwargs)
  def warning(self, msg, *args, **kwargs):
    self.log(logging.WARNING, msg, *args, **kwargs)
  warn = warning
  def error(self, msg, *args, **kwargs):
    self.log(logging.ERROR, msg, *args, **kwargs)
  def critical(self, msg, *args, **kwargs):
    self.log(logging.CRITICAL, msg, *args, **kwargs)

class LogTime(object):
  ''' LogTime is a content manager that logs the elapsed time of the enclosed
      code. After the run, the field .elapsed contains the elapsed time in
      seconds.
  '''
  def __init__(self, tag, level=None, threshold=None):
    if level is None:
      level = cs.misc.logging_level
    self.tag = tag
    self.level = level
    self.threshold = threshold
  def __enter__(self):
    self.start = time.time()
  def __exit__(self, exc_type, exc_value, traceback):
    now = time.time()
    elapsed = now - self.start
    if elapsed >= self.threshold:
      logging.log(self.level, "%s: %5.3fs" % (self.tag, elapsed))
    self.elapsed = elapsed
    return False
