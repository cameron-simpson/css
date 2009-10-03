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

class _PrefixState(threading.local):
  def __init__(self):
    self.current = cs.misc.cmd
    self.raise_prefix = None
    self.prior = []
_prefix = _PrefixState()

class Pfx(object):
  ''' A context manager to maintain a per-thread stack of message prefices.
      The function current_prefix() returns the current prefix value.
  '''
  def __init__(self, mark, absolute=False):
    global _prefix
    if absolute:
      newmark = mark
    else:
      newmark = _prefix.current + ': ' + str(mark)
    self.mark = newmark
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
        if hasattr(exc_value, 'args'):
          exc_value.args = [pfx + ": " + str(exc_value.args[0])] \
                         + list(exc_value.args[1:])
        else:
          # we can't modify this - at least report the current prefix state
          print >>sys.stderr, "%s: Pfx.__exit__: exc_value = %s" % (pfx, `exc_value`,)
        pfx = None
    _prefix.current, _prefix.raise_prefix = _prefix.prior.pop()
    if pfx is None:
      _prefix.raise_prefix = None
    return False
  enter = __enter__
  exit = __exit__

def current_prefix():
  ''' Return the current prefix value as used by the Pfx class.
  '''
  global _prefix
  return _prefix.current

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
