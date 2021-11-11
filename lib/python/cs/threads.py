#!/usr/bin/python
#
# Thread convenience facilities.
#       - Cameron Simpson <cs@cskk.id.au> 18nov2007
#

''' Thread related convenience classes and functions.
'''

from __future__ import with_statement
from collections import defaultdict, deque, namedtuple
from contextlib import contextmanager
from heapq import heappush, heappop
from inspect import ismethod
import sys
from threading import Semaphore, Thread, current_thread, Lock, local as thread_local
from cs.context import stackattrs
from cs.deco import decorator
from cs.excutils import logexc, transmute
from cs.logutils import LogTime, error, warning, debug, exception
from cs.pfx import Pfx, prefix
from cs.py.func import funcname, prop
from cs.py3 import raise3
from cs.queues import IterableQueue, MultiOpenMixin, not_closed
from cs.seq import seq, Seq

__version__ = '20210306-post'

DISTINFO = {
    'description':
    "threading and communication/synchronisation conveniences",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.context',
        'cs.deco',
        'cs.excutils',
        'cs.logutils',
        'cs.pfx',
        'cs.py.func',
        'cs.py3',
        'cs.queues',
        'cs.seq',
    ],
}

class State(thread_local):
  ''' A `Thread` local object with attributes
      which can be used as a context manager to stack attribute values.

      Example:

          from cs.threads import State

          S = State(verbose=False)

          with S(verbose=True) as prev_attrs:
              if S.verbose:
                  print("verbose! (formerly verbose=%s)" % prev_attrs['verbose'])
  '''

  def __init__(self, **kw):
    ''' Initiale the `State`, providing the per-Thread initial values.
    '''
    thread_local.__init__(self)
    for k, v in kw.items():
      setattr(self, k, v)

  def __str__(self):
    return "%s(%s)" % (
        type(self).__name__,
        ','.join("%s=%r" % kv for kv in self.__dict__.items())
    )

  __repr__ = __str__

  @contextmanager
  def __call__(self, **kw):
    ''' Calling a `State` returns a context manager which stacks some state.
        The context manager yields the previous values
        for the attributes which were stacked.
    '''
    with stackattrs(self, **kw) as prev_attrs:
      yield prev_attrs

# pylint: disable=too-many-arguments
def bg(
    func,
    daemon=None,
    name=None,
    no_start=False,
    no_logexc=False,
    args=None,
    kwargs=None
):
  ''' Dispatch the callable `func` in its own `Thread`;
      return the `Thread`.

      Parameters:
      * `func`: a callable for the `Thread` target.
      * `daemon`: optional argument specifying the `.daemon` attribute.
      * `name`: optional argument specifying the `Thread` name,
        default: the name of `func`.
      * `no_logexc`: if false (default `False`), wrap `func` in `@logexc`.
      * `no_start`: optional argument, default `False`.
        If true, do not start the `Thread`.
      * `args`, `kwargs`: passed to the `Thread` constructor
  '''
  if name is None:
    name = funcname(func)
  if args is None:
    args = ()
  if kwargs is None:
    kwargs = {}

  ##thread_prefix = prefix() + ': ' + name
  thread_prefix = name

  def thread_body():
    with Pfx(thread_prefix):
      return func(*args, **kwargs)

  T = Thread(name=thread_prefix, target=thread_body)
  if not no_logexc:
    func = logexc(func)
  if daemon is not None:
    T.daemon = daemon
  if not no_start:
    T.start()
  return T

WTPoolEntry = namedtuple('WTPoolEntry', 'thread queue')

class WorkerThreadPool(MultiOpenMixin):
  ''' A pool of worker threads to run functions.
  '''

  def __init__(self, name=None, max_spare=4):
    ''' Initialise the WorkerThreadPool.

        Parameters:
        * `name`: optional name for the pool
        * `max_spare`: maximum size of each idle pool (daemon and non-daemon)
    '''
    if name is None:
      name = "WorkerThreadPool-%d" % (seq(),)
    if max_spare < 1:
      raise ValueError("max_spare(%s) must be >= 1" % (max_spare,))
    MultiOpenMixin.__init__(self)
    self.name = name
    self.max_spare = max_spare
    self.idle_fg = deque()  # nondaemon Threads
    self.idle_daemon = deque()  # daemon Threads
    self.all = set()
    self._lock = Lock()

  def __str__(self):
    return "WorkerThreadPool:%s" % (self.name,)

  __repr__ = __str__

  def startup(self):
    ''' Start the pool.
    '''

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
        T = Thread(
            target=self._handler,
            args=Targs,
            name=("%s:worker" % (self.name,))
        )
        T.daemon = daemon
        Q = IterableQueue(name="%s:IQ%d" % (self.name, seq()))
        entry = WTPoolEntry(T, Q)
        self.all.add(entry)
        Targs.append(entry)
        debug("%s: start new worker thread (daemon=%s)", self, T.daemon)
        T.start()
      entry.queue.put((func, retq, deliver))

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
      except Exception:  # pylint: disable=broad-except
        exc_info = sys.exc_info()
        log_func = (
            exception
            if isinstance(exc_info[1],
                          (TypeError, NameError, AttributeError)) else debug
        )
        log_func(
            "%s: worker thread: ran task: exception! %r", self, sys.exc_info()
        )
        # don't let exceptions go unhandled
        # if nobody is watching, raise the exception and don't return
        # this handler to the pool
        if retq is None and deliver is None:
          error("%s: worker thread: reraise exception", self)
          raise3(*exc_info)
        debug("%s: worker thread: set result = (None, exc_info)", self)
      T.name = oname
      func = None  # release func+args
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
          with LogTime("AdjustableSemaphore(%s): acquire excess capacity",
                       self.__name):
            self.__sem.acquire(True)
          delta += 1
      self.__value = newvalue

@decorator
def locked(func, initial_timeout=10.0, lockattr='_lock'):
  ''' A decorator for instance methods that must run within a lock.

      Decorator keyword arguments:
      * `initial_timeout`:
        the initial lock attempt timeout;
        if this is `>0` and exceeded a warning is issued
        and then an indefinite attempt is made.
        Default: `2.0`s
      * `lockattr`:
        the name of the attribute of `self`
        which references the lock object.
        Default `'_lock'`
  '''
  citation = "@locked(%s)" % (funcname(func),)

  def lockfunc(self, *a, **kw):
    ''' Obtain the lock and then call `func`.
    '''
    lock = getattr(self, lockattr)
    if initial_timeout > 0 and lock.acquire(timeout=initial_timeout):
      try:
        return func(self, *a, **kw)
      finally:
        lock.release()
    else:
      if initial_timeout > 0:
        warning(
            "%s: timeout after %gs waiting for %s<%s>.%s, continuing to wait",
            citation, initial_timeout,
            type(self).__name__, self, lockattr
        )
      with lock:
        return func(self, *a, **kw)

  lockfunc.__name__ = citation
  lockfunc.__doc__ = getattr(func, '__doc__', '')
  return lockfunc

@decorator
def locked_property(
    func, lock_name='_lock', prop_name=None, unset_object=None
):
  ''' A thread safe property whose value is cached.
      The lock is taken if the value needs to computed.

      The default lock attribute is `._lock`.
      The default attribute for the cached value is `._`*funcname*
      where *funcname* is `func.__name__`.
      The default "unset" value for the cache is `None`.
  '''
  if prop_name is None:
    prop_name = '_' + func.__name__

  @transmute(exc_from=AttributeError)
  def locked_property_getprop(self):
    ''' Attempt lockless fetch of property first.
        Use lock if property is unset.
    '''
    p = getattr(self, prop_name, unset_object)
    if p is unset_object:
      try:
        lock = getattr(self, lock_name)
      except AttributeError:
        error("no %s.%s attribute", type(self).__name__, lock_name)
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

  return prop(locked_property_getprop)

class LockableMixin(object):
  ''' Trite mixin to control access to an object via its `._lock` attribute.
      Exposes the `._lock` as the property `.lock`.
      Presents a context manager interface for obtaining an object's lock.
  '''

  def __enter__(self):
    self._lock.acquire()

  # pylint: disable=unused-argument
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

class PriorityLockSubLock(namedtuple('PriorityLockSubLock',
                                     'name priority lock priority_lock')):
  ''' The record for the per-`acquire`r `Lock` held by `PriorityLock.acquire`.
  '''

  def __str__(self):
    return "%s(name=%r,priority=%s,lock=%s:%s,priority_lock=%r)" \
        % (type(self).__name__,
           self.name,
           self.priority,
           type(self.lock).__name__, id(self.lock),
           str(self.priority_lock))

class PriorityLock(object):
  ''' A priority based mutex which is acquired by and released to waiters
      in priority order.

      The initialiser sets a default priority, itself defaulting to `0`.

      The `acquire()` method accepts an optional `priority` value
      which specifies the priority of the acquire request;
      lower values have higher priorities.
      `acquire` returns a new `PriorityLockSubLock`.

      Note that internally this allocates a `threading.Lock` per acquirer.

      When `acquire` is called, if the `PriorityLock` is taken
      then the acquirer blocks on their personal `Lock`.

      When `release()` is called the highest priority `Lock` is released.

      Within a priority level `acquire`s are served in FIFO order.

      Used as a context manager, the mutex is obtained at the default priority.
      The `priority()` method offers a context manager
      with a specified priority.
      Both context managers return the `PriorityLockSubLock`
      allocated by the `acquire`.
  '''

  _cls_seq = Seq()

  def __init__(self, default_priority=0, name=None):
    ''' Initialise the `PriorityLock`.

        Parameters:
        * `default_priority`: the default `acquire` priority,
          default `0`.
        * `name`: optional identifying name
    '''
    if name is None:
      name = str(next(self._cls_seq))
    self.name = name
    self.default_priority = default_priority
    # heap of active priorities
    self._priorities = []
    # queues per priority
    self._blocked = defaultdict(list)
    self._nlocks = 0
    self._current_sublock = None
    self._seq = Seq()
    self._lock = Lock()

  def __str__(self):
    return "%s[%s]" % (type(self).__name__, self.name)

  def acquire(self, priority=None):
    ''' Acquire the mutex with `priority` (default from `default_priority`).
        Return the new `PriorityLockSubLock`.

        This blocks behind any higher priority `acquire`s
        or any earlier `acquire`s of the same priority.
    '''
    if priority is None:
      priority = self.default_priority
    priorities = self._priorities
    blocked_map = self._blocked
    # prepare an acquired Lock at the right priority
    my_lock = PriorityLockSubLock(
        str(self) + '-' + str(next(self._seq)), priority, Lock(), self
    )
    my_lock.lock.acquire()
    with self._lock:
      self._nlocks += 1
      if self._nlocks == 1:
        # we're the only contender: return now
        assert self._current_sublock is None
        self._current_sublock = my_lock
        return my_lock
      # store my_lock in the pending locks
      blocked = blocked_map[priority]
      if not blocked:
        # new priority
        heappush(priorities, priority)
      blocked.append(my_lock)
    # block until someone frees my_lock
    my_lock.lock.acquire()
    assert self._current_sublock is None
    self._current_sublock = my_lock
    return my_lock

  def release(self):
    ''' Release the mutex.

        Internally, this releases the highest priority `Lock`,
        allowing that `acquire`r to go forward.
    '''
    # release the top Lock
    priorities = self._priorities
    with self._lock:
      my_lock = self._current_sublock
      self._current_sublock = None
      my_lock.lock.release()
      self._nlocks -= 1
      if self._nlocks > 0:
        # release to highest priority pending lock
        top_priority = priorities[0]
        top_blocked = self._blocked[top_priority]
        top_lock = top_blocked.pop(0)
        # release the lock ASAP
        top_lock.lock.release()
        if not top_blocked:
          # no more locks of this priority, discard the queue and the priority
          del self._blocked[top_priority]
          heappop(priorities)

  def __enter__(self):
    ''' Enter the mutex as a context manager at the default priority.
        Returns the new `Lock`.
    '''
    return self.acquire()

  def __exit__(self, *_):
    ''' Exit the context manager.
    '''
    self.release()
    return False

  @contextmanager
  def priority(self, this_priority):
    ''' A context manager with the specified `this_priority`.
        Returns the new `Lock`.
    '''
    my_lock = self.acquire(this_priority)
    try:
      yield my_lock
    finally:
      self.release()

@decorator
def monitor(cls, attrs=None, initial_timeout=10.0, lockattr='_lock'):
  ''' Turn a class into a monitor, all of whose public methods are `@locked`.

      This is a simple approach requires class instances to have a `._lock`
      which is an `RLock` or compatible
      because methods may naively call each other.

      Parameters:
      * `attrs`: optional iterable of attribute names to wrap in `@locked`.
        If omitted, all names commencing with a letter are chosen.
      * `initial_timeout`: optional initial lock timeout, default `10.0`s.
      * `lockattr`: optional lock attribute name, default `'_lock'`.

      Only attributes satifying `inspect.ismethod` are wrapped
      because `@locked` requires access to the instance `._lock` attribute.
  '''
  if attrs is None:
    attrs = filter(lambda attr: attr and attr[0].isalpha(), dir(cls))
  for name in attrs:
    method = getattr(cls, name)
    if ismethod(method):
      setattr(
          cls, name,
          locked(method, initial_timeout=initial_timeout, lockattr=lockattr)
      )
  return cls

if __name__ == '__main__':
  import cs.threads_tests
  cs.threads_tests.selftest(sys.argv)
