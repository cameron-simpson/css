#!/usr/bin/python
#
# Assorted debugging facilities.
#       - Cameron Simpson <cs@zip.com.au> 20apr2013
#

import inspect
from logging import DEBUG
from threading import Thread
import time
import cs.logutils
from cs.logutils import infer_logging_level, debug, setup_logging
from cs.seq import seq
from cs.threads import Result
from cs.timeutils import sleep

def ifdebug():
  return 1
  return infer_logging_level() <= logging.DEBUG

def DEBUG(f):
  ''' Decorator to wrap functions in timing and value debuggers.
  '''
  if not ifdebug():
    return f
  def inner(*a, **kw):
    filename, lineno = inspect.stack()[1][1:3]
    n = seq()
    R = Result()
    T = Thread(target=_debug_watcher, args=(filename, lineno, n, f.__name__, R))
    T.daemon = True
    T.start()
    debug("%s:%d: [%d] call %s(*%r, **%r)", filename, lineno, n, f.__name__, a, kw)
    start = time.time()
    result = f(*a, **kw)
    end = time.time()
    debug("%s:%d: [%d] called %s, elapsed %gs, got %r", filename, lineno, n, f.__name__, end - start, result)
    R.put(result)
    return result
  return inner

def _debug_watcher(filename, lineno, n, funcname, R):
  slow = 2
  sofar = 0
  slowness = 0
  while not R.ready:
    if slowness >= slow:
      debug("%s:%d: [%d] calling %s, %gs elapsed so far...", filename, lineno, n, funcname, sofar)
      # reset report time and complain more slowly next time
      slowness = 0
      slow += 1
    time.sleep(1)
    sofar += 1
    slowness += 1

if __name__ == '__main__':
  setup_logging()
  @DEBUG
  def testfunc(x):
    debug("into testfunc: x=%r", x)
    sleep(7)
    debug("leaving testfunc: returning x=%r", x)
    return x
  print "TESTFUNC", testfunc(9)
