#!/usr/bin/python
#
# Thread convenience facilities.
#       - Cameron Simpson <cs@cskk.id.au> 18nov2007
#

''' Thread related convenience classes and functions.
'''

from collections import defaultdict, namedtuple
from contextlib import contextmanager
from heapq import heappush, heappop
from inspect import ismethod
import sys
from threading import (
    current_thread,
    Semaphore,
    Thread as builtin_Thread,
    Lock,
    local as thread_local,
)
from typing import Any, Mapping, Optional

from cs.context import ContextManagerMixin, stackattrs, stackset
from cs.deco import decorator
from cs.excutils import logexc, transmute
from cs.gimmicks import error, warning
from cs.pfx import Pfx  # prefix
from cs.py.func import funcname, prop
from cs.seq import Seq

__version__ = '20240303'

DISTINFO = {
    'description':
    "threading and communication/synchronisation conveniences",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.context',
        'cs.deco',
        'cs.excutils',
        'cs.gimmicks',
        'cs.pfx',
        'cs.py.func',
        'cs.seq',
    ],
}

class ThreadState(thread_local):
  ''' A `Thread` local object with attributes
      which can be used as a context manager to stack attribute values.

      Example:

          from cs.threads import ThreadState

          S = ThreadState(verbose=False)

          with S(verbose=True) as prev_attrs:
              if S.verbose:
                  print("verbose! (formerly verbose=%s)" % prev_attrs['verbose'])
  '''

  def __init__(self, **kw):
    ''' Initiale the `ThreadState`, providing the per-Thread initial values.
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
    ''' Calling a `ThreadState` returns a context manager which stacks some state.
        The context manager yields the previous values
        for the attributes which were stacked.
    '''
    with stackattrs(self, **kw) as prev_attrs:
      yield prev_attrs

# backward compatible deprecated name
State = ThreadState

# TODO: what to do about overlapping HasThreadState usage of a particular class?
class HasThreadState(ContextManagerMixin):
  ''' A mixin for classes with a `cs.threads.ThreadState` instance as `.state`
      providing a context manager which pushes `current=self` onto that state
      and a `default()` class method returning `cls.perthread_state.current`
      as the default instance of that class.

      *NOTE*: the documentation here refers to `cls.perthread_state`, but in
      fact we honour the `cls.THREAD_STATE_ATTR` attribute to name
      the state attribute which allows perclass state attributes,
      and also use with classes which already use `.perthread_state` for
      another purpose.

      *NOTE*: `HasThreadState.Thread` is a _class_ method whose default
      is to push state for all active `HasThreadState` subclasses.
      Contrast with `HasThreadState.bg` which is an _instance_method
      whose default is to push state for just that instance.
      The top level `cs.threads.bg` function calls `HasThreadState.Thread`
      to obtain its `Thread`.
  '''

  _HasThreadState_lock = Lock()
  _HasThreadState_classes = set()

  # the default name for the Thread state attribute
  THREAD_STATE_ATTR = 'perthread_state'

  @classmethod
  def default(cls, factory=None, raise_on_None=False):
    ''' The default instance of this class from `cls.perthread_state.current`.

        Parameters:
        * `factory`: optional callable to create an instance of `cls`
          if `cls.perthread_state.current` is `None` or missing;
          if `factory` is `True` then `cls` is used as the factory
        * `raise_on_None`: if `cls.perthread_state.current` is `None` or missing
          and `factory` is false and `raise_on_None` is true,
          raise a `RuntimeError`;
          this is primarily a debugging aid
    '''
    current = getattr(getattr(cls, cls.THREAD_STATE_ATTR), 'current', None)
    if current is None:
      if factory:
        if factory is True:
          factory = cls
        return factory()
      if raise_on_None:
        raise RuntimeError(
            "%s.default: %s.%s.current is missing/None and ifNone is None" %
            (cls.__name__, cls.__name__, cls.THREAD_STATE_ATTR)
        )
    return current

  def __enter_exit__(self):
    ''' Push `self.perthread_state.current=self` as the `Thread` local current instance.

        Include `self.__class__` in the set of currently active classes for the duration.
    '''
    cls = self.__class__
    with cls._HasThreadState_lock:
      stacked = stackset(
          HasThreadState._HasThreadState_classes, cls, cls._HasThreadState_lock
      )
    with stacked:
      state = getattr(cls, cls.THREAD_STATE_ATTR)
      with state(current=self):
        yield

  @classmethod
  def get_thread_states(cls, all_classes=None):
    ''' Return a mapping of `class`->*current_instance*`
        for use with `HasThreadState.with_thread_states`
        or `HasThreadState.Thread` or `HasThreadState.bg`.

        The default behaviour returns just a mapping for this class,
        expecting the default instance to be responsible for what
        other resources it holds.

        There is also a legacy mode for `all_classes=True`
        where the mapping is for all active classes,
        probably best used for `Thread`s spawned outside
        a `HasThreadState` context.

        Parameters:
        * `all_classes`: optional flag, default `False`;
          if true, return a mapping of class to current instance
          for all `HasThreadState` subclasses with an open instance,
          otherwise just a mapping from this class to its current instance
    '''
    if all_classes is None:
      all_classes = False
    with cls._HasThreadState_lock:
      if all_classes:
        # the "current" instance for every HasThreadState._HasThreadState_classes
        currency = {
            htscls:
            getattr(
                getattr(htscls, htscls.THREAD_STATE_ATTR), 'current', None
            )
            for htscls in HasThreadState._HasThreadState_classes
        }
      elif cls is HasThreadState:
        currency = {}
      else:
        # just the current instance of the calling class
        currency = {
            cls: getattr(getattr(cls, cls.THREAD_STATE_ATTR, 'current'), None)
        }
    return currency

  @classmethod
  @contextmanager
  def with_thread_states(
      cls, thread_states: Optional[Mapping[type, Any]] = None
  ):
    ''' Context manager to push all the current objects from `thread_states`
        by calling each as a context manager.

        The default `thread_states` comes from `HasThreadState.get_thread_states()`.
    '''
    if thread_states is None or isinstance(thread_states, bool):
      thread_states = cls.get_thread_states(all_classes=thread_states)
    if not thread_states:
      yield
    else:
      state_iter = iter(list(thread_states.values()))

      def with_thread_states_pusher():
        try:
          htsobj = next(state_iter)
        except StopIteration:
          yield
        else:
          if htsobj is None:
            # no current object, skip to the next class
            yield from with_thread_states_pusher()
          else:
            with htsobj:
              yield from with_thread_states_pusher()

      yield from with_thread_states_pusher()

  @classmethod
  def Thread(cls, *Thread_a, target, thread_states=None, **Thread_kw):
    ''' Class factory for a `Thread` to push the `.current` state for this class.

        The optional parameter `thread_states`
        may be used to pass an explicit mapping of `type`->`instance`
        of thread states to use;
        the default states come from `HasThreadState.get_thread_states()`.
        The values of this mapping are iterated over and used as context managers.

        A boolean value may also be passed meaning:
        * `False`: do not apply any thread states
        * `True`: apply the default thread states

        Note: the default `thread_states` does a `with current:`
        for this class' `current` instance (if any) so that the
        `Thread` holds it open until completed.
        For some worker threads such as `MultiOpenMixin`s consuming
        a queue of tasks this may be undesirable if the instance
        shutdown phase includes a close-and-drain for the queue -
        because the `Thread` holds the instance open, the shutdown
        phase never arrives.
        In this case, pass `thread_states=False` to this call.
    '''
    # snapshot the .current states in the source Thread
    if thread_states is None:
      thread_states = False
    if isinstance(thread_states, bool):
      thread_states = cls.get_thread_states(all_classes=thread_states)
    if thread_states:

      def target_wrapper(*a, **kw):
        ''' Wrapper for the `Thread.target` to push the source `.current`
            states in the new Thread before running the target.
        '''
        with cls.with_thread_states(thread_states):
          return target(*a, **kw)
    else:
      # no state to prepare, eschew the wrapper
      target_wrapper = target

    return builtin_Thread(*Thread_a, target=target_wrapper, **Thread_kw)

  def bg(self, func, *bg_a, thread_states=None, **bg_kw):
    ''' Get a `Thread` using `self.Thread` and start it.
        Return the `Thread`.

        Note: the default `thread_states` is `{type(self): self}`
        in order to arranges a `with self:` so that the `Thread`
        holds it open until completed.
        For some worker threads such as `MultiOpenMixin`s consuming
        a queue of tasks this may be undesirable if the instance
        shutdown phase includes a close-and-drain for the queue -
        because the `Thread` holds the instance open, the shutdown
        phase never arrives.
        In this case, pass `thread_states=False` to this call.
    '''
    cls = type(self)
    if thread_states is None:
      thread_states = {cls: self}
    return bg(
        func, *bg_a, thread_states=thread_states, thread_class=cls, **bg_kw
    )

# pylint: disable=too-many-arguments
def bg(
    func,
    daemon=None,
    name=None,
    no_start=False,
    no_logexc=False,
    thread_class=None,
    thread_states=None,
    args=None,
    kwargs=None,
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
      * `thread_class`: the `Thread` factory, default `HasThreadState.Thread`
      * `thread_states`: passed tothe  `thread_class` factory
      * `args`, `kwargs`: passed to the `Thread` constructor
  '''
  if name is None:
    name = funcname(func)
  if args is None:
    args = ()
  if kwargs is None:
    kwargs = {}
  if thread_class is None:
    thread_class = HasThreadState.Thread

  ##thread_prefix = prefix() + ': ' + name
  thread_prefix = name

  def thread_body():
    with Pfx(thread_prefix):
      return func(*args, **kwargs)

  T = thread_class(
      name=thread_prefix,
      target=thread_body,
      thread_states=thread_states,
  )
  if not no_logexc:
    func = logexc(func)
  if daemon is not None:
    T.daemon = daemon
  if not no_start:
    T.start()
  return T

def joinif(T: builtin_Thread):
  ''' Call `T.join()` if `T` is not the current `Thread`.

      Unlike `threading.Thread.join`, this function is a no-op if
      `T` is the current `Thread.

      The use case is situations such as the shutdown phase of the
      `MultiOpenMixin.startup_shutdown` context manager. Because
      the "initial open" startup phase is not necessarily run in
      the same thread as the "final close" shutdown phase, it is
      possible for example for a worker `Thread` to execute the
      shutdown phase and try to join itself. Using this function
      allows that scenario.
  '''
  if T is not current_thread():
    T.join()

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
    from cs.logutils import LogTime  # pylint: disable=import-outside-toplevel
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
      self.__sem.acquire(blocking)  # pylint: disable=consider-using-with
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
        from cs.logutils import LogTime  # pylint: disable=import-outside-toplevel
        while delta < 0:
          with LogTime("AdjustableSemaphore(%s): acquire excess capacity",
                       self.__name):
            self.__sem.acquire(True)  # pylint: disable=consider-using-with
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
  def __exit__(self, exc_type, exc_value, traceback):
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

# pylint: disable=too-many-instance-attributes
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

      This is a simple approach which requires class instances to have a
      `._lock` which is an `RLock` or compatible
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
