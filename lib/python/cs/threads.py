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
    Lock,
    Semaphore,
    Thread as builtin_Thread,
    local as thread_local,
)

from cs.context import (
    ContextManagerMixin,
    stackattrs,
    stackset,
    twostep,
    withall,
)
from cs.deco import decorator
from cs.excutils import logexc, transmute
from cs.gimmicks import error, warning
from cs.pfx import Pfx  # prefix
from cs.py.func import funcname, prop
from cs.py.stack import caller
from cs.seq import Seq

__version__ = '20240412-post'

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
        'cs.py.stack',
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
    ''' Initiate the `ThreadState`, providing the per-Thread initial values.
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
  def default(cls, *, factory=None, raise_on_None=False):
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
  def Thread(
      cls,
      *,
      name=None,
      target,
      enter_objects=None,
      **Thread_kw,
  ):
    ''' Factory for a `Thread` to push the `.current` state for the
        currently active classes.

        The optional parameter `enter_objects` may be used to pass
        an iterable of objects whose contexts should be entered
        using `with obj:`.
        If this is set to `True` that indicates that every "current"
        `HasThreadStates` instance should be entered.
        The default does not enter any object contexts.
        The `HasThreadStates.bg` method defaults to passing
        `enter_objects=(self,)` to enter the context for `self`.
    '''
    if name is None:
      name = funcname(target)
    if enter_objects is True:
      # the bool True means enter all the state objects, marked as for-with
      enter_tuples = (
          (
              getattr(
                  getattr(htscls, htscls.THREAD_STATE_ATTR), 'current', None
              ), True
          ) for htscls in HasThreadState._HasThreadState_classes
      )
    else:
      enter_tuples = (
          # all the current objects, marked as not-for-with
          [
              (
                  getattr(
                      getattr(htscls, htscls.THREAD_STATE_ATTR), 'current',
                      None
                  ), False
              ) for htscls in HasThreadState._HasThreadState_classes
          ] +
          # the enter_objects, marked as for-with
          [(obj, True) for obj in enter_objects or ()]
      )
    enter_it = iter(enter_tuples)

    def with_enter_objects():
      ''' A recursive context manager to enter all the contexts
          implied by `enter_it`.
          For each `(enter_obj,for_with)` in `enter_it`, if `for_with`
          is true, enter the object using `with enter_obj:` otherwise
          enter using: `with enter_object.per_thread_state(current=enter_obj)`.
      '''
      try:
        enter_obj, for_with = next(enter_it)
      except StopIteration:
        yield
      else:
        if enter_obj is None:
          # no current object, skip to the next one
          yield from with_enter_objects()
        else:
          if for_with:
            print("WITH enter_obj", enter_obj)
            with enter_obj:
              yield from with_enter_objects()
          else:
            thread_state = getattr(enter_obj, enter_obj.THREAD_STATE_ATTR)
            with thread_state(curret=enter_obj):
              yield from with_enter_objects()

    def target_wrapper(*a, **kw):
      ''' Wrapper for the `Thread.target` to push the current states
          from `enter_it` in the new Thread before running the `target`.
      '''
      with contextmanager(with_enter_objects)():
        return target(*a, **kw)

    return builtin_Thread(name=name, target=target_wrapper, **Thread_kw)

  def bg(self, func, *, enter_objects=None, pre_enter_objects=None, **bg_kw):
    ''' Get a `Thread` using `type(elf).Thread` and start it.
        Return the `Thread`.

        The `HasThreadState.Thread` factory duplicates the current `Thread`'s
        `HasThreadState` current objects as current in the new `Thread`.
        Additionally it enters the contexts of various objects using
        `with obj` according to the `enter_objects` parameter.

        The value of the optional parameter `enter_objects` governs
        which objects have their context entered using `with obj`
        in the child `Thread` while running `func` as follows:
        - `None`: the default, meaning `(self,)`
        - `False`: no object contexts are entered
        - `True`: all current `HasThreadState` object contexts will be entered
        - an iterable of objects whose contexts will be entered;
          pass `()` to enter no objects
    '''
    cls = type(self)
    if enter_objects is None:
      enter_objects = (self,)
    return bg(
        func,
        thread_factory=cls.Thread,
        enter_objects=enter_objects,
        **bg_kw,
    )

# pylint: disable=too-many-arguments
def bg(
    func,
    *,
    daemon=None,
    name=None,
    no_start=False,
    no_logexc=False,
    args=None,
    kwargs=None,
    thread_factory=None,
    pre_enter_objects=None,
    **tfkw,
):
  ''' Dispatch the callable `func` in its own `Thread`;
      return the `Thread`.

      Parameters:
      * `func`: a callable for the `Thread` target.
      * `args`, `kwargs`: passed to the `Thread` constructor
      * `kwargs`, `kwargs`: passed to the `Thread` constructor
      * `daemon`: optional argument specifying the `.daemon` attribute.
      * `name`: optional argument specifying the `Thread` name,
        default: the name of `func`.
      * `no_logexc`: if false (default `False`), wrap `func` in `@logexc`.
      * `no_start`: optional argument, default `False`.
        If true, do not start the `Thread`.
      * `pre_enter_objects`: an optional iterable of objects which
        should be entered using `with`

      If `pre_enter_objects` is supplied, these objects will be
      entered before the `Thread` is started and exited when the
      `Thread` target function ends.
      If the `Thread` is _not_ started (`no_start=True`, very
      unusual) then it will be the caller's responsibility to manage
      to entered objects.
  '''
  if name is None:
    name = funcname(func)
  if args is None:
    args = ()
  if kwargs is None:
    kwargs = {}
  if thread_factory is None:
    thread_factory = HasThreadState.Thread
  ##thread_prefix = prefix() + ': ' + name
  thread_prefix = name
  preopen_close_it = None

  def thread_body():
    ''' Establish a basic `Pfx` context for the target `func`.
    '''
    try:
      with Pfx("Thread:%d:%s", current_thread().ident, thread_prefix):
        return func(*args, **kwargs)
    finally:
      if preopen_close_it is not None:
        # do the closes
        next(preopen_close_it)

  T = thread_factory(
      name=thread_prefix,
      target=thread_body,
      **tfkw,
  )
  if not no_logexc:
    func = logexc(func)
  if daemon is not None:
    T.daemon = daemon
  if pre_enter_objects:
    # prepare a context manager to open all the pre_enter_objects
    preopen_close_it = twostep(withall(pre_enter_objects))
    # do the opens
    next(preopen_close_it)
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
      supports that scenario.
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

class DeadlockError(RuntimeError):
  ''' Raised by `NRLock` when a lock is attempted from the `Thread` currently holding the lock.
  '''

class NRLock:
  ''' A nonrecursive lock.
      Attempting to take this lock when it is already held by the current `Thread`
      will raise `DeadlockError`.
      Otherwise this behaves likc `threading.Lock`.
  '''

  __slots__ = ('_lock', '_lock_thread', '_locked_by')

  def __init__(self):
    self._lock = Lock()
    self._lock_thread = None
    self._locked_by = None

  def __repr__(self):
    return (
        f'{self.__class__.__name__}:{self._lock}:{self._lock_thread}:{self._locked_by}'
        if self.locked() else f'{self.__class__.__name__}:{self._lock}'
    )

  def locked(self):
    ''' Return the lock status.
    '''
    return self._lock.locked()

  def acquire(self, *a, caller_frame=None, **kw):
    ''' Acquire the lock as for `threading.Lock`.
        Raises `DeadlockError` is the lock is already held by the current `Thread`.
    '''
    lock = self._lock
    if lock.locked() and current_thread() is self._lock_thread:
      raise DeadlockError('lock already held by current Thread')
    acquired = lock.acquire(*a, **kw)
    if acquired:
      if caller_frame is None:
        caller_frame = caller()
      self._lock_thread = current_thread()
      self._locked_by = caller_frame
    return acquired

  def release(self):
    self._lock.release()
    self._lock_thread = None
    self._locked_by = None

  def __enter__(self):
    acquired = self.acquire(caller_frame=caller())
    assert acquired
    return acquired

  def __exit__(self, *_):
    self.release()

if __name__ == '__main__':
  import cs.threads_tests
  cs.threads_tests.selftest(sys.argv)
