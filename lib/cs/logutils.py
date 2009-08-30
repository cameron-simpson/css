#!/usr/bin/python
#
# Convenience routines for logging.
#       - Cameron Simpson <cs@zip.com.au> 29aug2009
#

from __future__ import with_statement
import logging
import time
import threading
import cs.misc

class _PrefixState(threading.local):
  def __init__(self):
    self.current = cs.misc.cmd
    self.prior = []
_prefix = _PrefixState()

class Pfx(object):
  def __enter__(self, mark, absolute=False):
    global _prefix
    if absolute:
      new = mark
    else:
      new = self.current + ': ' + mark
    self.prior.append(self.current)
    self.current = new
  def __exit__(self, exc_type, exc_value, traceback):
    self.current = self.prior.pop()
    return False

def current_prefix():
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
