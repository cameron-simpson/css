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

class _PrefixState(threading.local):
  def __init__(self):
    self.raise_prefix = None
    self.cur = Pfx(cs.misc.cmd)
    self.cur.prefix = cs.misc.cmd
    self.old = []

class _Pfx_LoggerAdapter(logging.LoggerAdapter):
  def process(self, msg, kwargs):
    msg = _prefix.cur.prefix + ": " + msg
    return msg, kwargs

class Pfx(object):
  ''' A context manager to maintain a per-thread stack of message prefices.
      The function current_prefix() returns the current prefix value.
  '''
  def __init__(self, mark, absolute=False, loggers=None):
    self.mark = str(mark)
    self.absolute = absolute
    if loggers is None:
      loggers = (logging.getLogger(), )
    elif not hasattr(loggers, '__getitem__'):
      loggers = (loggers, )
    self._loggers = loggers
    self.loggers = None

  def __rig_loggers(self):
    if self.loggers is None:
      # make LoggerAdapters for all the specified loggers
      # to insert the prefix onto the messages
      self.loggers = ( _Pfx_LoggerAdapter(L, {}) for L in self._loggers )

  def __enter__(self):
    global _prefix
    # compute the new message prefix
    mark = self.mark
    if not self.absolute:
      mark = _prefix.cur.prefix + ': ' + mark
    self.prefix = mark

    _prefix.old.append(_prefix.cur)
    _prefix.cur = self
    self.raise_prefix = self.prefix

  def __exit__(self, exc_type, exc_value, traceback):
    global _prefix
    if _prefix.raise_prefix:
      if exc_value is not None:
        if hasattr(exc_value, 'args') and len(exc_value.args) > 0:
          exc_value.args = [self.raise_prefix + ": " + str(exc_value.args[0])] \
                         + list(exc_value.args[1:])
        else:
          # we can't modify this - at least report the current prefix state
          sys.stderr.write("%s: Pfx.__exit__: exc_value = %s\n" % (self.raise_prefix, repr(exc_value),))
      # prevent outer Pfx wrappers from hacking stuff as well
      _prefix.raise_prefix = None
    _prefix.cur = _prefix.old.pop()
    return False
  enter = __enter__
  exit = __exit__

  # Logger methods
  def exception(self, msg, *args):
    self.__rig_loggers()
    for L in self.loggers:
      L.exception(msg, *args)
  def log(self, level, msg, *args, **kwargs):
    self.__rig_loggers()
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
  return _prefix.current

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
