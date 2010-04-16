#!/usr/bin/python
#
# Convenience routines for logging.
#       - Cameron Simpson <cs@zip.com.au> 29aug2009
#

from __future__ import with_statement
import logging
import sys
import time
from thread import allocate_lock
import threading
import traceback
import cs.misc

class NullHandler(logging.Handler):
  def emit(self, record):
    pass

''' Convenience do-nothing logging handler as suggested by:
      http://docs.python.org/library/logging.html#configuring-logging-for-a-library
'''
nullHandler = NullHandler()

''' Top level logger for the cs library. Presently unused!
'''
logger = logging.getLogger("cs")
logger.addHandler(nullHandler)

__logExLock = allocate_lock()
def logException(exc_type, exc_value, exc_tb):
  ''' Replacement for sys.excepthook that reports via the cs.logutils
      logging wrappers.
  '''
  with __logExLock:
    curhook = sys.excepthook
    sys.excepthook = sys.__excepthook__
    exception("EXCEPTION: %s:%s" % (exc_type, exc_value))
    for line in traceback.format_exception(exc_type, exc_value, exc_tb):
      exception("EXCEPTION> "+line)
    sys.excepthook = curhook

class _PrefixState(threading.local):
  def __init__(self):
    self.raise_prefix = None
    self.cur = Pfx(cs.misc.cmd)
    self.cur.prefix = cs.misc.cmd
    self.old = []

class Pfx_LoggerAdapter(logging.LoggerAdapter):
  def process(self, msg, kwargs):
    if len(_prefix.cur.prefix) > 0:
      msg = _prefix.cur.prefix + ": " + msg
    return msg, kwargs

def pfx(tag, loggers=None):
  ''' Decorator for functions that should run inside:
        with Pfx(tag, loggers=loggers):
  '''
  def wrap(func):
    def wrapped(*args, **kwargs):
      with Pfx(tag, loggers=loggers):
        func(*args, **kwargs)
    return wrapped
  return wrap

class Pfx(object):
  ''' A context manager to maintain a per-thread stack of message prefices.
      The function current_prefix() returns the current prefix value.
  '''
  def __init__(self, mark, absolute=False, loggers=None):
    self.mark = str(mark)
    self.absolute = absolute
    if loggers is not None:
      if not hasattr(loggers, '__getitem__'):
        loggers = (loggers, )
    self.logto(loggers)

  def logto(self, newLoggers):
    ''' Define the Loggers anew.
    '''
    self._loggers = newLoggers
    self._loggerAdapters = None

  @property
  def loggers(self):
    if self._loggerAdapters is None:
      _loggers = self._loggers
      if _loggers is None:
        # get the Logger list from an ancestor
        for P in _prefix.old:
          if P._loggers is not None:
            _loggers = P._loggers
            break
        if _loggers is None:
          _loggers = (logging.getLogger(),)
      self._loggerAdapters = list( Pfx_LoggerAdapter(L, {}) for L in _loggers )
    return self._loggerAdapters

  def __enter__(self):
    global _prefix
    # compute the new message prefix
    mark = self.mark
    if not self.absolute and len(_prefix.cur.prefix) > 0:
      mark = _prefix.cur.prefix + ': ' + mark
    self.prefix = mark

    _prefix.old.append(_prefix.cur)
    _prefix.cur = self
    _prefix.raise_prefix = self.prefix

  def __exit__(self, exc_type, exc_value, traceback):
    global _prefix
    if exc_value is not None:
      if exc_type is not SystemExit:
        if _prefix.raise_prefix is not None:
          if hasattr(exc_value, 'args') and len(exc_value.args) > 0:
            exc_value.args = [_prefix.raise_prefix + ": " + str(exc_value.args[0])] \
                           + list(exc_value.args[1:])
          else:
            # we can't modify this - at least report the current prefix state
            sys.stderr.write("%s: Pfx.__exit__: exc_value = %s\n" % (_prefix.raise_prefix, repr(exc_value),))
          # prevent outer Pfx wrappers from hacking stuff as well
        _prefix.raise_prefix = None
    _prefix.cur = _prefix.old.pop()
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

_prefix = _PrefixState()

def current_prefix():
  ''' Return the current prefix value as used by the Pfx class.
  '''
  global _prefix
  return _prefix.cur.prefix

# Logger public functions
def exception(msg, *args):
  _prefix.cur.exception(msg, *args)
def log(level, msg, *args, **kwargs):
  _prefix.cur.log(level, msg, *args, **kwargs)
def debug(msg, *args, **kwargs):
  log(logging.DEBUG, msg, *args, **kwargs)
def info(msg, *args, **kwargs):
  log(logging.INFO, msg, *args, **kwargs)
def warning(msg, *args, **kwargs):
  log(logging.WARNING, msg, *args, **kwargs)
warn = warning
def error(msg, *args, **kwargs):
  log(logging.ERROR, msg, *args, **kwargs)
def critical(msg, *args, **kwargs):
  log(logging.CRITICAL, msg, *args, **kwargs)

class LogTime(object):
  ''' LogTime is a content manager that logs the elapsed time of the enclosed
      code. After the run, the field .elapsed contains the elapsed time in
      seconds.
  '''
  def __init__(self, tag, level=None, threshold=None):
    if level is None:
      level = logging.INFO
    self.tag = tag
    self.level = level
    self.threshold = threshold
  def __enter__(self):
    self.start = time.time()
  def __exit__(self, exc_type, exc_value, traceback):
    now = time.time()
    elapsed = now - self.start
    if self.threshold is not None and elapsed >= self.threshold:
      log(self.level, "%s: %5.3fs" % (self.tag, elapsed))
    self.elapsed = elapsed
    return False
