#!/usr/bin/python
#
# Assorted debugging facilities.
#       - Cameron Simpson <cs@cskk.id.au> 20apr2013
#

r'''
Assorted debugging facilities.
'''

from __future__ import print_function
from cmd import Cmd
import inspect
import logging
import os
from subprocess import Popen, PIPE
import sys
import threading
import time
import traceback
from types import SimpleNamespace as NS

import cs.logutils
from cs.logutils import debug, error, warning, D, ifdebug, loginfo
from cs.obj import Proxy
from cs.pfx import Pfx
from cs.py.stack import caller
from cs.py3 import Queue, Queue_Empty, exec_code
from cs.seq import seq
from cs.x import X

__version__ = '20211208'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.logutils',
        'cs.obj',
        'cs.pfx',
        'cs.py.stack',
        'cs.py3',
        'cs.seq',
        'cs.x',
    ],
}

# @DEBUG dispatches a thread to monitor function elapsed time.
# This is how often it polls for function completion.
DEBUG_POLL_RATE = 0.25

def Lock():
  ''' Factory function: if `cs.logutils.loginfo.level<=logging.DEBUG`
      then return a `DebuggingLock`, otherwise a `threading.Lock`.
  '''
  if not ifdebug():
    return threading.Lock()
  filename, lineno = inspect.stack()[1][1:3]
  return DebuggingLock({'filename': filename, 'lineno': lineno})

def RLock():
  ''' Factory function: if `cs.logutils.loginfo.level<=logging.DEBUG`
      then return a `DebuggingRLock`, otherwise a `threading.RLock`.
  '''
  if not ifdebug():
    return threading.RLock()
  filename, lineno = inspect.stack()[1][1:3]
  return DebuggingRLock({'filename': filename, 'lineno': lineno})

class TimingOutLock(object):
  ''' A `Lock` replacement which times out, used for locating deadlock points.
  '''

  def __init__(self, deadlock_timeout=20.0, recursive=False):
    self._lock = threading.RLock() if recursive else threading.Lock()
    self._deadlock_timeout = deadlock_timeout

  def acquire(self, blocking=True, timeout=-1, name=None):
    if timeout < 0:
      timeout = self._deadlock_timeout
    else:
      timeout = min(timeout, self._deadlock_timeout)
    ok = (
        self._lock.acquire(timeout=timeout)
        if blocking else self._lock.acquire(blocking=blocking)
    )
    if not ok:
      raise RuntimeError(
          "TIMEOUT acquiring lock held by %s:%r" %
          (self.owner, self.owner_name)
      )
    self.owner = caller()
    self.owner_name = name
    return True

  def release(self):
    return self._lock.release()

  def __enter__(self):
    self.acquire()
    self.owner = caller()
    return True

  def __exit__(self, *a):
    return self._lock.__exit__(*a)

class TraceSuite(object):
  ''' Context manager to trace start and end of a code suite.
  '''

  def __init__(self, msg, *a):
    if a:
      msg = msg % a
    self.msg = msg

  def __enter__(self):
    X("TraceSuite ENTER %s", self.msg)

  def __exit__(self, exc_type, exc_value, exc_tb):
    X("TraceSuite LEAVE %s: exc_value=%s", self.msg, exc_value)

def Thread(*a, **kw):
  if not ifdebug():
    return threading.Thread(*a, **kw)
  filename, lineno = inspect.stack()[1][1:3]
  return DebuggingThread({'filename': filename, 'lineno': lineno}, *a, **kw)

def thread_dump(Ts=None, fp=None):
  ''' Write thread identifiers and stack traces to the file `fp`.

      Parameters:
      * `Ts`: the `Thread`s to dump; if unspecified use `threading.enumerate()`.
      * `fp`: the file to which to write; if unspecified use `sys.stderr`.
  '''
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

def stack_dump(stack=None, limit=None, logger=None, log_level=None):
  ''' Dump a stack trace to a logger.

      Parameters:
      * `stack`: a stack list as returned by `traceback.extract_stack`.
        If missing or `None`, use the result of `traceback.extract_stack()`.
      * `limit`: a limit to the number of stack entries to dump.
        If missing or `None`, dump all entries.
      * `logger`: a `logger.Logger` ducktype or the name of a logger.
        If missing or `None`, obtain a logger from `logging.getLogger()`.
      * `log_level`: the logging level for the dump.
        If missing or `None`, use `cs.logutils.loginfo.level`.
  '''
  if stack is None:
    stack = traceback.extract_stack()
  if limit is not None:
    stack = stack[:limit]
  if logger is None:
    logger = logging.getLogger()
  elif isinstance(logger, str):
    logger = logging.getLogger(logger)
  if log_level is None:
    log_level = loginfo.level
  for text in traceback.format_list(stack):
    for line in text.splitlines():
      logger.log(log_level, line.rstrip())

def DEBUG(f, force=False):
  ''' Decorator to wrap functions in timing and value debuggers.
  '''
  from cs.result import Result

  def inner(*a, **kw):
    if not force and not ifdebug():
      return f(*a, **kw)
    filename, lineno = inspect.stack()[1][1:3]
    n = seq()
    R = Result()
    T = threading.Thread(
        target=_debug_watcher, args=(filename, lineno, n, f.__name__, R)
    )
    T.daemon = True
    T.start()
    debug(
        "%s:%d: [%d] call %s(*%r, **%r)", filename, lineno, n, f.__name__, a,
        kw
    )
    start = time.time()
    try:
      retval = f(*a, **kw)
    except Exception as e:
      error("EXCEPTION from %s(*%s, **%s): %s", f, a, kw, e)
      raise
    end = time.time()
    debug(
        "%s:%d: [%d] called %s, elapsed %gs, got %r", filename, lineno, n,
        f.__name__, end - start, retval
    )
    R.put(retval)
    return retval

  return inner

def _debug_watcher(filename, lineno, n, funcname, R):
  slow = 2
  sofar = 0
  slowness = 0
  while not R.ready:
    if slowness >= slow:
      debug(
          "%s:%d: [%d] calling %s, %gs elapsed so far...", filename, lineno, n,
          funcname, sofar
      )
      # reset report time and complain more slowly next time
      slowness = 0
      slow += 1
    time.sleep(DEBUG_POLL_RATE)
    sofar += DEBUG_POLL_RATE
    slowness += DEBUG_POLL_RATE

def DF(func, *a, **kw):
  ''' Wrapper for a function call to debug its use.

      This requires rewriting the call from `f(*a,*kw)` to `DF(f,*a,**kw)`.
      Alternatively one could rewrite as `DEBUG(f)(*a,**kw)`.
  '''
  return DEBUG(func, force=True)(*a, **kw)

class DebugWrapper(NS):
  ''' Base class for classes presenting debugging wrappers.
  '''

  def debug(self, msg, *a):
    if a:
      msg = msg % a
    cs.logutils.debug(': '.join((self.debug_label, msg)))

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
  ''' Wrapper class for `threading.Lock` to trace creation and use.

      `cs.threads.Lock()` returns one of these in debug mode or a raw
      `threading.Lock` otherwise.
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
    self.acquire()
    return self

  def __exit__(self, *a):
    ##return self.lock.__exit__(*a)
    self.release()
    return False

  def acquire(self, *a):
    ''' Acquire the lock.
    '''
    # quietly support Python 3 arguments after blocking parameter
    blocking = True
    if a:
      blocking = a[0]
      a = a[1:]
    filename, lineno = inspect.stack()[1][1:3]
    debug("%s:%d: acquire(blocking=%s)", filename, lineno, blocking)
    if blocking:
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
    ''' Release the lock.
    '''
    filename, lineno = inspect.stack()[0][1:3]
    debug("%s:%d: release()", filename, lineno)
    self.held = None
    self.lock.release()

  def _timed_acquire(self, Q, filename, lineno):
    ''' Block waiting for lock acquisition.
        Report slow acquisition.

        This would be inline above except that Python 2 `Lock`s do
        not have a timeout parameter, hence this thread.
        This probably scales VERY badly if there is a lot of `Lock`
        contention.
    '''
    slow = self.slow
    sofar = 0
    slowness = 0
    while True:
      # block until lock acquired
      try:
        Q.get(True, 1)
      except Queue_Empty:
        sofar += 1
        slowness += 1
        if slowness >= slow:
          self.debug(
              "from %s:%d: acquire: after %gs, held by %s", filename, lineno,
              sofar, self.held
          )
          # complain more slowly next time
          slowness = 0
          slow += 1
      else:
        break

class DebuggingRLock(DebugWrapper):
  ''' Wrapper class for threading.RLock to trace creation and use.

      `cs.threads.RLock()` returns on of these in debug mode or a raw
      `threading.RLock` otherwise.
  '''

  def __init__(self, dkw):
    D("dkw = %r", dkw)
    DebugWrapper.__init__(self, **dkw)
    self.debug('__init__')
    self.lock = threading.RLock()
    self.stack = None

  def __str__(self):
    return "%s%r" % (
        type(self).__name__, [
            "%s:%s:%s" % (f.filename, f.lineno, f.code_context[0].strip())
            for f in self.stack
        ] if self.stack else "NO_STACK"
    )

  def __enter__(self):
    filename, lineno = inspect.stack()[1][1:3]
    self.debug('from %s:%d: __enter__ ...', filename, lineno)
    self.lock.__enter__()
    self.stack = inspect.stack()
    self.debug('from %s:%d: __enter__ ENTERED', filename, lineno)
    return self

  def __exit__(self, *a):
    filename, lineno = inspect.stack()[1][1:3]
    self.debug('%s:%d: __exit__(*%s) ...', filename, lineno, a)
    return self.lock.__exit__(*a)

  def acquire(self, blocking=True, timeout=-1):
    filename, lineno = inspect.stack()[0][1:3]
    self.debug('%s:%d: acquire(blocking=%s)', filename, lineno, blocking)
    if timeout < 0:
      ret = self.lock.acquire(blocking)
    else:
      ret = self.lock.acquire(blocking, timeout)
    if ret:
      self.stack = inspect.stack()
    return ret

  def release(self):
    filename, lineno = inspect.stack()[0][1:3]
    self.debug('%s:%d: release()', filename, lineno)
    self.lock.release()
    self.stack = None

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
    threading.Thread.__init__(self, *a, **kw)

  @DEBUG
  def join(self, timeout=None):
    self.debug("join(timeout=%r)...", timeout)
    retval = threading.Thread.join(self, timeout=timeout)
    self.debug("join(timeout=%r) completed", timeout)
    _debug_threads.discard(self)
    return retval

def trace_caller(func):
  ''' Decorator to report the caller of a function when called.
  '''

  def subfunc(*a, **kw):
    frame = caller()
    D(
        "CALL %s()<%s:%d> FROM %s()<%s:%d>", func.__name__,
        func.__code__.co_filename, func.__code__.co_firstlineno,
        frame.funcname, frame.filename, frame.lineno
    )
    return func(*a, **kw)

  subfunc.__name__ = "trace_caller/subfunc/" + func.__name__
  return subfunc

class TracingObject(Proxy):

  def __init__(self, other):
    Proxy.__init__(self, other)
    self.__attr_map = {}

  def __getattribute__(self, attr):
    X("TracingObject.__getattribute__(attr=%r)", attr)
    _proxied = Proxy.__getattribute__(self, '_proxied')
    try:
      value = object.__getattribute__(_proxied, attr)
    except AttributeError:
      X("no .%s attribute", attr)
      raise
    else:
      X("getattr .%s", attr)
      return TracingObject(value)

  def __call__(self, *a, **kw):
    _proxied = Proxy.__getattribute__(self, '_proxied')
    X("call %s(*%r, **%r)", _proxied, a, kw)
    return _proxied(*a, **kw)

class DummyMap(object):

  def __init__(self, label, d=None):
    X("new DummyMap labelled %r, d=%r", label, d)
    self.__label = label
    self.__map = {}
    if d:
      self.__map.update(d)

  def __str__(self):
    return self.__label

  def items(self):
    X("%s.items", self)
    return []

  def __getitem__(self, key):
    v = self.__map.get(key)
    X("%s[%r] => %r", self, key, v)
    return v

def openfiles(substr=None, pid=None):
  ''' Run lsof(8) against process `pid`
      returning paths of open files whose paths contain `substr`.

      Parameters:
      * `substr`: default substring to select by; default returns all paths.
      * `pid`: process to examine; default from `os.getpid()`.
  '''
  if pid is None:
    pid = os.getpid()
  paths = []
  P = Popen(['lsof', '-p', str(pid)], stdout=PIPE)
  for lsof in P.stdout:
    lsof = lsof.decode()
    fields = lsof.split()
    if len(fields) >= 9:
      if fields[4] == 'REG':
        if substr is None or substr in fields[8]:
          paths.append(fields[8])
  P.wait()
  return paths

class DebugShell(Cmd):
  ''' An interactive prompt for python statements, attached to `/dev/tty` by default.
  '''

  def __init__(self, var_dict, stdin=None, stdout=None):
    if stdin is None:
      stdin = open('/dev/tty', 'r')
    if stdout is None:
      stdout = open('/dev/tty', 'a')
    self.stdin = stdin
    self.stdout = stdout
    Cmd.__init__(self, stdin=stdin, stdout=stdout)
    self.vars = var_dict

  def default(self, line):
    ''' Default command action.
    '''
    if line == 'EOF':
      return True
    try:
      exec_code(line, globals(), self.vars)
    except Exception as e:
      X("Exception: %s", e)
    self.stdout.flush()
    return False

def debug_object_shell(o, prompt=None):
  ''' Interactive prompt for inspecting variables.
  '''
  if prompt is None:
    prompt = str(o) + '> '
  v = o.__dict__
  C = DebugShell(v)
  intro = '\n\n'
  for k in sorted(v.keys()):
    intro += '\n  %s = %r' % (k, v[k])
  intro += '\n'
  C.prompt = prompt
  C.cmdloop(intro)

def selftest(module_name, defaultTest=None, argv=None):
  ''' Called by my unit tests.
  '''
  # pylint: disable=import-outside-toplevel
  if argv is None:
    argv = sys.argv
  import importlib
  importlib.import_module(module_name)
  import signal
  signal.signal(signal.SIGHUP, lambda sig, frame: thread_dump())
  signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(thread_dump()))
  import unittest
  return unittest.main(module=module_name, defaultTest=defaultTest, argv=argv)
