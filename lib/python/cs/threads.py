#!/usr/bin/python
#
# Thread convenience facilities.
#       - Cameron Simpson <cs@cskk.id.au> 18nov2007
#

''' Thread related convenience classes and functions.
'''

from __future__ import with_statement
from collections import deque, namedtuple
import sys
from threading import Semaphore, current_thread
from cs.debug import Lock, Thread
from cs.excutils import logexc, transmute
from cs.logutils import LogTime, error, debug, exception
from cs.obj import O
from cs.py.func import funcname, prop
from cs.py3 import raise3
from cs.queues import IterableQueue, MultiOpenMixin, not_closed
from cs.seq import seq

DISTINFO = {
    'description': "threading and communication/synchronisation conveniences",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.debug',
        'cs.excutils',
        'cs.logutils',
        'cs.obj',
        'cs.py.func',
        'cs.py3'
        'cs.queues',
        'cs.seq',
    ],
}

def bg(func, daemon=None, name=None):
  ''' Dispatch the callable `func` in its own Thread; return the Thread.

      Parameters:
      `daemon`: optional argument specifying the .daemon attribute.
      `name`: optional argument specifying the Thread name.
  '''
  T = Thread(name=name, target=func)
  if daemon is not None:
    T.daemon = daemon
  T.start()
  return T

WTPoolEntry = namedtuple('WTPoolEntry', 'thread queue')

class WorkerThreadPool(MultiOpenMixin, O):
  ''' A pool of worker threads to run functions.
  '''

  def __init__(self, name=None, max_spare=4):
    ''' Initialise the WorkerThreadPool.

        Paramaters:
        `name`: optional name for the pool
        `max_spare`: maximum size of each idle pool (daemon and non-daemon)
    '''
    if name is None:
      name = "WorkerThreadPool-%d" % (seq(),)
    if max_spare < 1:
      raise ValueError("max_spare(%s) must be >= 1" % (max_spare,))
    O.__init__(self)
    MultiOpenMixin.__init__(self)
    self.name = name
    self.max_spare = max_spare
    self.idle_fg = deque()      # nondaemon Threads
    self.idle_daemon = deque()  # daemon Threads
    self.all = set()

  def __str__(self):
    return "WorkerThreadPool:%s" % (self.name,)

  __repr__ = __str__

  def startup(self):
    ''' Start the pool.
    '''
    pass

  def shutdown(self):
    ''' Shut down the pool.

        Close all the request queues.

        Note: does not wait for all Threads to complete; call .join after close.
    '''
    with self._lock:
      all_entries = list(self.all)
    for entry in all_entries:
      entry.queue.close()

  def join(self):
    ''' Wait for all outstanding Threads to complete.
    '''
    with self._lock:
      all_entries = list(self.all)
    for entry in all_entries:
      T = entry.thread
      if T is not current_thread():
        T.join()

  @not_closed
  def dispatch(self, func, retq=None, deliver=None, pfx=None, daemon=None):
    ''' Dispatch the callable `func` in a separate thread.

        On completion the result is the sequence
        `func_result, None, None, None`.
        On an exception the result is the sequence
        `None, exec_type, exc_value, exc_traceback`.

        If `retq` is not None, the result is .put() on retq.
        If `deliver` is not None, deliver(result) is called.
        If the parameter `pfx` is not None, submit pfx.partial(func);
        see the cs.logutils.Pfx.partial method for details.
        If `daemon` is not None, set the .daemon attribute of the Thread to `daemon`.

        TODO: high water mark for idle Threads.
    '''
    if self.closed:
      raise ValueError("%s: closed, but dispatch() called" % (self,))
    if pfx is not None:
      func = pfx.partial(func)
    if daemon is None:
      daemon = current_thread().daemon
    idle = self.idle_daemon if daemon else self.idle_fg
    with self._lock:
      debug("dispatch: idle = %s", idle)
      if idle:
        # use an idle thread
        entry = idle.pop()
        debug("dispatch: reuse %s", entry)
      else:
        debug("dispatch: need new thread")
        # no available threads - make one
        Targs = []
        T = Thread(target=self._handler, args=Targs, name=("%s:worker" % (self.name,)))
        T.daemon = daemon
        Q = IterableQueue(name="%s:IQ%d" % (self.name, seq()))
        entry = WTPoolEntry(T, Q)
        self.all.add(entry)
        Targs.append(entry)
        debug("%s: start new worker thread (daemon=%s)", self, T.daemon)
        T.start()
      entry.queue.put( (func, retq, deliver) )

  @logexc
  def _handler(self, entry):
    ''' The code run by each handler thread.

        Read a function `func`, return queue `retq` and delivery
        function `deliver` from the function queue.
        Run func().

        On completion the result is the sequence:
          func_result, None
        On an exception the result is the sequence:
          None, exc_info

        If retq is not None, the result is .put() on retq.
        If deliver is not None, deliver(result) is called.
        If both are None and an exception occurred, it gets raised.
    '''
    T, Q = entry
    idle = self.idle_daemon if T.daemon else self.idle_fg
    for func, retq, deliver in Q:
      oname = T.name
      T.name = "%s:RUNNING:%s" % (oname, func)
      result, exc_info = None, None
      try:
        debug("%s: worker thread: running task...", self)
        result = func()
        debug("%s: worker thread: ran task: result = %s", self, result)
      except Exception:
        exc_info = sys.exc_info()
        log_func = (
            exception
            if isinstance(exc_info[1], (TypeError, NameError, AttributeError))
            else debug
        )
        log_func("%s: worker thread: ran task: exception! %r", self, sys.exc_info())
        # don't let exceptions go unhandled
        # if nobody is watching, raise the exception and don't return
        # this handler to the pool
        if retq is None and deliver is None:
          error("%s: worker thread: reraise exception", self)
          raise3(*exc_info)
        debug("%s: worker thread: set result = (None, exc_info)", self)
      T.name = oname
      func = None     # release func+args
      reuse = False and (len(idle) < self.max_spare)
      if reuse:
        # make available for another task
        with self._lock:
          idle.append(entry)
        ##D("_handler released thread: idle = %s", idle)
      # deliver result
      result_info = result, exc_info
      if retq is not None:
        debug("%s: worker thread: %r.put(%s)...", self, retq, result_info)
        retq.put(result_info)
        debug("%s: worker thread: %r.put(%s) done", self, retq, result_info)
        retq = None
      if deliver is not None:
        debug("%s: worker thread: deliver %s...", self, result_info)
        deliver(result_info)
        debug("%s: worker thread: delivery done", self)
        deliver = None
      # forget stuff
      result = None
      exc_info = None
      result_info = None
      if not reuse:
        self.all.remove(entry)
        break
      debug("%s: worker thread: proceed to next function...", self)

class AdjustableSemaphore(object):
  ''' A semaphore whose value may be tuned after instantiation.
  '''

  def __init__(self, value=1, name="AdjustableSemaphore"):
    self.limit0 = value
    self.__sem = Semaphore(value)
    self.__value = value
    self.__name = name
    self.__lock = Lock()

  def __str__(self):
    return "%s[%d]" % (self.__name, self.limit0)

  def __enter__(self):
    with LogTime("%s(%d).__enter__: acquire", self.__name, self.__value):
      self.acquire()

  def __exit__(self, exc_type, exc_value, traceback):
    self.release()
    return False

  def release(self):
    ''' Release the semaphore.
    '''
    self.__sem.release()

  def acquire(self, blocking=True):
    ''' The acquire() method calls the base acquire() method if not blocking.
        If blocking is true, the base acquire() is called inside a lock to
        avoid competing with a reducing adjust().
    '''
    if not blocking:
      return self.__sem.acquire(blocking)
    with self.__lock:
      self.__sem.acquire(blocking)
    return True

  def adjust(self, newvalue):
    ''' Set capacity to `newvalue`
        by calling release() or acquire() an appropriate number of times.

        If `newvalue` lowers the semaphore capacity then adjust()
        may block until the overcapacity is released.
    '''
    if newvalue <= 0:
      raise ValueError("invalid newvalue, should be > 0, got %s" % (newvalue,))
    self.adjust_delta(newvalue - self.__value)

  def adjust_delta(self, delta):
    ''' Adjust capacity by `delta` by calling release() or acquire()
        an appropriate number of times.

        If `delta` lowers the semaphore capacity then adjust() may block
        until the overcapacity is released.
    '''
    newvalue = self.__value + delta
    with self.__lock:
      if delta > 0:
        while delta > 0:
          self.__sem.release()
          delta -= 1
      else:
        while delta < 0:
          with LogTime("AdjustableSemaphore(%s): acquire excess capacity", self.__name):
            self.__sem.acquire(True)
          delta += 1
      self.__value = newvalue

def locked(func):
  ''' A decorator for monitor functions that must run within a lock.
      Relies upon a ._lock attribute for locking.
  '''
  def lockfunc(self, *a, **kw):
    with self._lock:
      return func(self, *a, **kw)
  lockfunc.__name__ = "@locked(%s)" % (funcname(func),)
  return lockfunc

def locked_property(func, lock_name='_lock', prop_name=None, unset_object=None):
  ''' A thread safe property whose value is cached.
      The lock is taken if the value needs to computed.
  '''
  if prop_name is None:
    prop_name = '_' + func.__name__
  @transmute(AttributeError)
  def getprop(self):
    ''' Attempt lockless fetch of property first.
        Use lock if property is unset.
    '''
    p = getattr(self, prop_name, unset_object)
    if p is unset_object:
      try:
        lock = getattr(self, lock_name)
      except AttributeError:
        error("no .%s attribute", lock_name)
        raise
      with lock:
        p = getattr(self, prop_name, unset_object)
        if p is unset_object:
          ##debug("compute %s...", prop_name)
          p = func(self)
          setattr(self, prop_name, p)
        else:
          ##debug("inside lock, already computed %s", prop_name)
          pass
    else:
      ##debug("outside lock, already computed %s", prop_name)
      pass
    return p
  return prop(getprop)

class LockableMixin(object):
  ''' Trite mixin to control access to an object via its ._lock attribute.
      Exposes the ._lock as the property .lock.
      Presents a context manager interface for obtaining an object's lock.
  '''
  def __enter__(self):
    self._lock.acquire()
  def __exit(self, exc_type, exc_value, traceback):
    self._lock.release()
  @property
  def lock(self):
    ''' Return the lock.
    '''
    return self._lock

def via(cmanager, func, *a, **kw):
  ''' Return a callable that calls the supplied `func` inside a
      with statement using the context manager `cmanager`.
      This intended use case is aimed at deferred function calls.
  '''
  def f():
    with cmanager:
      return func(*a, **kw)
  return f

if __name__ == '__main__':
  import cs.threads_tests
  cs.threads_tests.selftest(sys.argv)
