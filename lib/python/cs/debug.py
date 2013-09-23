#!/usr/bin/python
#
# Assorted debugging facilities.
#       - Cameron Simpson <cs@zip.com.au> 20apr2013
#

from __future__ import print_function
import inspect
import logging
import sys
import threading
import time
from cs.py3 import Queue
import cs.logutils
from cs.logutils import infer_logging_level, debug, error, setup_logging, D, Pfx
from cs.obj import O
from cs.seq import seq
from cs.timeutils import sleep

def ifdebug():
  return cs.logutils.logging_level <= logging.DEBUG

def Lock():
  ''' Factory function: if cs.logutils.logging_level <= logging.DEBUG
      then return a DebuggingLock, otherwise a threading.Lock.
  '''
  if not ifdebug():
    return threading.Lock()
  filename, lineno = inspect.stack()[1][1:3]
  return DebuggingLock({'filename': filename, 'lineno': lineno})

def RLock():
  ''' Factory function: if cs.logutils.logging_level <= logging.DEBUG
      then return a DebuggingRLock, otherwise a threading.RLock.
  '''
  if not ifdebug():
    return threading.RLock()
  filename, lineno = inspect.stack()[1][1:3]
  return DebuggingRLock({'filename': filename, 'lineno': lineno})

def Thread(*a, **kw):
  if not ifdebug():
    return threading.Thread(*a, **kw)
  filename, lineno = inspect.stack()[1][1:3]
  return DebuggingThread({'filename': filename, 'lineno': lineno}, *a, **kw)

def thread_dump(Ts=None, fp=None):
  ''' Write thread identifiers and stack traces to the file `fp`.
      `Ts`: the Threads to dump; if unspecified use threading.enumerate().
      `fp`: the file to which to write; if unspecified use sys.stderr.
  '''
  import traceback
  if Ts is None:
    Ts = threading.enumerate()
  if fp is None:
    fp = sys.stderr
  with Pfx("thread_dump"):
    frames = sys._current_frames()
    for T in Ts:
      try:
        frame = frames[T.ident]
      except KeyError:
        warning("no frame for Thread.ident=%s", T.ident)
        continue
      print("Thread", T.ident, T.name, file=fp)
      traceback.print_stack(frame, None, fp)
      print(file=fp)

def DEBUG(f):
  ''' Decorator to wrap functions in timing and value debuggers.
  '''
  if not ifdebug():
    return f
  def inner(*a, **kw):
    from cs.threads import Result
    filename, lineno = inspect.stack()[1][1:3]
    n = seq()
    R = Result()
    T = threading.Thread(target=_debug_watcher, args=(filename, lineno, n, f.__name__, R))
    T.daemon = True
    T.start()
    debug("%s:%d: [%d] call %s(*%r, **%r)", filename, lineno, n, f.__name__, a, kw)
    start = time.time()
    try:
      result = f(*a, **kw)
    except Exception as e:
      error("EXCEPTION from %s(*%s, **%s): %s", f, a, kw, e)
      raise
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

class DebugWrapper(O):
  ''' Base class for classes presenting debugging wrappers.
  '''

  def __init__(self, **kw):
    O.__init__(self, **kw)

  def debug(self, msg, *a):
    if a:
      msg = msg % a
    cs.logutils.debug(': '.join( (self.debug_label, msg) ))

  @property
  def debug_label(self):
    info = '%s:%d' % (self.filename, self.lineno)
    try:
      context = self.context
    except AttributeError:
      pass
    else:
      info = ':'.join(info, str(context))
    label = '%s-%d[%s]' % (self.__class__.__name__, id(self), info)
    return label

class DebuggingLock(DebugWrapper):
  ''' Wrapper class for threading.Lock to trace creation and use.
      cs.threads.Lock() returns on of these in debug mode or a raw
      threading.Lock otherwise.
  '''

  def __init__(self, dkw, slow=2):
    DebugWrapper.__init__(self, **dkw)
    self.debug("__init__(slow=%r)", slow)
    if slow <= 0:
      raise ValueError("slow must be positive, received: %r" % (slow,))
    self.slow = slow
    self.lock = threading.Lock()
    self.held = None

  def __enter__(self):
    ##self.lock.__enter__()
    D("ENTER0")
    self.acquire()
    D("ENTER1")
    return self

  def __exit__(self, *a):
    ##return self.lock.__exit__(*a)
    self.release()
    return False

  def acquire(self, *a):
    # quietly support Python 3 arguments after blocking parameter
    blocking = True
    if a:
      blocking = a[0]
      a = a[1:]
    D("ACQUIRE0")
    filename, lineno = inspect.stack()[1][1:3]
    debug("%s:%d: acquire(blocking=%s)", filename, lineno, blocking)
    D("ACQUIRE1")
    if blocking:
      D("ACQUIRE2")
      # blocking
      # try non-blocking first
      # if successful, good
      # otherwise spawn a monitoring thread to report on slow acquisition
      # and block
      taken = self.lock.acquire(False)
      if not taken:
        Q = Queue()
        T = Thread(target=self._timed_acquire, args=(Q, filename, lineno))
        T.daemon = True
        T.start()
        taken = self.lock.acquire(blocking, *a)
        Q.put(taken)
    else:
      # non-blocking: do ordinary lock acquisition
      taken = self.lock.acquire(blocking, *a)
    if taken:
      self.held = (filename, lineno)
    return taken

  def release(self):
    filename, lineno = inspect.stack()[0][1:3]
    debug("%s:%d: release()", filename, lineno)
    self.held = None
    self.lock.release()

  def _timed_acquire(self, Q, filename, lineno):
    ''' Block waiting for lock acquisition.
        Report slow acquisition.
	This would be inline above except that Python 2 Locks do
	not have a timeout parameter, hence this thread.
	This probably scales VERY badly if there is a lot of Lock
	contention.
    '''
    slow = self.slow
    sofar = 0
    slowness = 0
    while True:
      try:
        taken = Q.get(True, 1)
      except Queue_Empty:
        sofar += 1
        slowness += 1
        if slowness >= slow:
          self.debug("from %s:%d: acquire: after %gs, held by %s", filename, lineno, sofar, self.held)
          # complain more slowly next time
          slowness = 0
          slow += 1
      else:
        break

class DebuggingRLock(DebugWrapper):
  ''' Wrapper class for threading.RLock to trace creation and use.
      cs.threads.RLock() returns on of these in debug mode or a raw
      threading.RLock otherwise.
  '''

  def __init__(self, dkw):
    D("dkw = %r", dkw)
    DebugWrapper.__init__(self, **dkw)
    self.debug('__init__')
    self.lock = threading.RLock()

  def __enter__(self):
    filename, lineno = inspect.stack()[0][1:3]
    self.debug('from %s:%d: __enter__ ...', filename, lineno)
    self.lock.__enter__()
    self.debug('from %s:%d: __enter__ ENTERED', filename, lineno)
    return self

  def __exit__(self, *a):
    filename, lineno = inspect.stack()[0][1:3]
    self.debug('%s:%d: __exit__(*%s) ...', filename, lineno, a)
    return self.lock.__exit__(*a)

  def acquire(self, blocking=True, timeout=-1):
    filename, lineno = inspect.stack()[0][1:3]
    self.debug('%s:%d: acquire(blocking=%s)', filename, lineno, blocking)
    if timeout < 0:
      self.lock.acquire(blocking)
    else:
      self.lock.acquire(blocking, timeout)

  def release(self):
    filename, lineno = inspect.stack()[0][1:3]
    self.debug('%s:%d: release()', filename, lineo)
    self.lock.release()

_debug_threads = set()

def dump_debug_threads():
  D("dump_debug_threads:")
  for T in _debug_threads:
    D("dump_debug_threads: thread %r: %r", T.name, T.debug_label)
  D("dump_debug_threads done")

class DebuggingThread(threading.Thread, DebugWrapper):

  def __init__(self, dkw, *a, **kw):
    DebugWrapper.__init__(self, **dkw)
    self.debug("NEW THREAD(*%r, **%r)", a, kw)
    _debug_threads.add(self)
    return threading.Thread.__init__(self, *a, **kw)

  @DEBUG
  def join(self, timeout=None):
    self.debug("join(timeout=%r)...", timeout)
    retval = threading.Thread.join(self, timeout=timeout)
    self.debug("join(timeout=%r) completed", timeout)
    _debug_threads.discard(self)
    return retval

if __name__ == '__main__':
  setup_logging()
  @DEBUG
  def testfunc(x):
    debug("into testfunc: x=%r", x)
    sleep(7)
    debug("leaving testfunc: returning x=%r", x)
    return x
  print("TESTFUNC", testfunc(9))
  thread_dump()
