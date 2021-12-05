#!/usr/bin/python
#
# Resourcing related classes and functions.
# - Cameron Simpson <cs@cskk.id.au> 11sep2014
#

''' Resource management classes and functions.
'''

from __future__ import print_function
from contextlib import contextmanager
import sys
from threading import Condition, Lock, RLock
import time
from cs.context import setup_cmgr, ContextManagerMixin
from cs.logutils import error, warning
from cs.obj import Proxy
from cs.pfx import pfx_method
from cs.py.func import prop
from cs.py.stack import caller, frames as stack_frames, stack_dump

__version__ = '20210906-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.context',
        'cs.logutils',
        'cs.obj',
        'cs.pfx',
        'cs.py.func',
        'cs.py.stack',
    ],
}

class ClosedError(Exception):
  ''' Exception for operations invalid when something is closed.
  '''
  pass

def not_closed(func):
  ''' Decorator to wrap methods of objects with a .closed property
      which should raise when self.closed.
  '''

  def not_closed_wrapper(self, *a, **kw):
    ''' Wrapper function to check that this instance is not closed.
    '''
    if self.closed:
      raise ClosedError(
          "%s: %s: already closed" % (not_closed_wrapper.__name__, self)
      )
    return func(self, *a, **kw)

  not_closed_wrapper.__name__ = "not_closed_wrapper(%s)" % (func.__name__,)
  return not_closed_wrapper

class _mom_state(object):

  def __init__(self):
    self.opened = False
    self._opens = 0
    self._opened_from = {}
    ##self.closed = False # final _close() not yet called
    self._final_close_from = None
    self._lock = RLock()
    self._finalise_later = False
    self._finalise = None

## debug: TrackedClassMixin
class MultiOpenMixin(ContextManagerMixin):
  ''' A multithread safe mixin to count open and close calls,
      and to call `.startup` on the first `.open`
      and to call `.shutdown` on the last `.close`.

      If used as a context manager this mixin calls `open()`/`close()` from
      `__enter__()` and `__exit__()`.

      Recommended subclass implementations do as little as possible
      during `__init__`, and do almost all setup during startup so
      that the class may perform multiple startup/shutdown iterations.

      Classes using this mixin need to _either_:
      * _either_ define a context manager method `.startup_shutdown`
        which does the startup actions before yeilding
        and then does the shutdown actions
      * _or_ define separate `.startup` and `.shutdown` methods.

      Example:

          class DatabaseThing(MultiOpenMixin):
              @contextmanager
              def startup_shutdown(self):
                  self._db = open_the_database()
                  yield
                  self._db.close()
          ...
          with DatabaseThing(...) as db_thing:
              ... use db_thing ...

      Why not a plain context manager? Because in multithreaded
      code one wants to keep the instance "open" while any thread
      is still using it.
      This mixin lets threads use an instance in overlapping fashion:

          db_thing = DatabaseThing(...)
          with db_thing:
              ... kick off threads with access to the db ...
          ...
          thread 1:
          with db_thing:
             ... use db_thing ...
          thread 2:
          with db_thing:
             ... use db_thing ...

      TODO:
      * `subopens`: if true (default false) then `.open` will return
        a proxy object with its own `.closed` attribute set by the
        proxy's `.close`.
  '''

  def __mo_getstate(self):
    ''' Fetch the state object for the mixin,
        something of a hack to avoid providing an __init__.
    '''
    # used to be self.__mo_state and catch AttributeError, but
    # something up the MRO weirds this - suspect the ABC base class
    try:
      state = self.__dict__['_MultiOpenMixin_state']
    except KeyError:
      state = self.__dict__['_MultiOpenMixin_state'] = _mom_state()
      assert state._opens == 0
    return state

  @property
  def finalise_later(self):
    return self.__mo_getstate()._finalise_later

  @finalise_later.setter
  def finalise_later(self, truthy):
    self.__mo_getstate()._finalise_later = truthy

  def tcm_get_state(self):
    ''' Support method for `TrackedClassMixin`.
    '''
    state = self.__mo_getstate()
    return {'opened': state.opened, 'opens': state._opens}

  def __enter_exit__(self):
    self.open(caller_frame=caller())
    try:
      yield
    finally:
      self.close(caller_frame=caller())

  @contextmanager
  def startup_shutdown(self):
    ''' Default context manager form of startup/shutdown - just calls them.
    '''
    try:
      startup = self.startup
    except AttributeError:
      warning(
          "MultiOpenMixin.startup_shutdown: no %s.startup" %
          (type(self).__name__,)
      )
    else:
      startup()
    try:
      yield
    finally:
      try:
        shutdown = self.shutdown
      except AttributeError:
        warning(
            "MultiOpenMixin.startup_shutdown: no %s.shutdown" %
            (type(self).__name__,)
        )
      else:
        shutdown()

  def open(self, caller_frame=None):
    ''' Increment the open count.
        On the first `.open` call `self.startup()`.
    '''
    state = self.__mo_getstate()
    if False:
      if caller_frame is None:
        caller_frame = caller()
      frame_key = caller_frame.filename, caller_frame.lineno
      state._opened_from[frame_key] = state._opened_from.get(frame_key, 0) + 1
    state.opened = True
    with state._lock:
      opens = state._opens
      opens += 1
      state._opens = opens
      if opens == 1:
        state._finalise = Condition(state._lock)
        state._teardown = setup_cmgr(self.startup_shutdown())
    return self

  def close(
      self, enforce_final_close=False, caller_frame=None, unopened_ok=False
  ):
    ''' Decrement the open count.
        If the count goes to zero, call `self.shutdown()` and return its value.

        Parameters:
        * `enforce_final_close`: if true, the caller expects this to
          be the final close for the object and a `RuntimeError` is
          raised if this is not actually the case.
        * `caller_frame`: used for debugging; the caller may specify
          this if necessary, otherwise it is computed from
          `cs.py.stack.caller` when needed. Presently the caller of the
          final close is recorded to help debugging extra close calls.
        * `unopened_ok`: if true, it is not an error if this is not open.
          This is intended for closing callbacks which might get called
          even if the original open never happened.
          (I'm looking at you, `cs.resources.RunState`.)
    '''
    state = self.__mo_getstate()
    if not state.opened:
      if unopened_ok:
        return None
      raise RuntimeError("%s: close before initial open" % (self,))
    retval = None
    with state._lock:
      opens = state._opens
      if opens < 1:
        error("%s: UNDERFLOW CLOSE", self)
        error("  final close was from %s", state._final_close_from)
        for frame_key in sorted(state._opened_from.keys()):
          error(
              "  opened from %s %d times", frame_key,
              state._opened_from[frame_key]
          )
        ##from cs.debug import thread_dump
        ##from threading import current_thread
        ##thread_dump([current_thread()])
        ##raise RuntimeError("UNDERFLOW CLOSE of %s" % (self,))
        return retval
      opens -= 1
      state._opens = opens
      if opens == 0:
        ##INACTIVE##state.tcm_dump(MultiOpenMixin)
        if caller_frame is None:
          caller_frame = caller()
        state._final_close_from = caller_frame
        teardown, state._teardown = state._teardown, None
        retval = teardown()
        if not state._finalise_later:
          self.finalise()
    if enforce_final_close and opens != 0:
      raise RuntimeError(
          "%s: expected this to be the final close, but it was not" % (self,)
      )
    return retval

  def finalise(self):
    ''' Finalise the object, releasing all callers of `.join()`.
        Normally this is called automatically after `.shutdown` unless
        `finalise_later` was set to true during initialisation.
    '''
    state = self.__mo_getstate()
    with state._lock:
      finalise = state._finalise
      if finalise is None:
        raise RuntimeError("%s: finalised more than once" % (self,))
      state._finalise = None
      finalise.notify_all()

  @property
  def closed(self):
    ''' Whether this object has been closed.
        Note: False if never opened.
    '''
    state = self.__mo_getstate()
    if state._opens > 0:
      return False
    ##if state._opens < 0:
    ##  raise RuntimeError("_OPENS UNDERFLOW: _opens < 0: %r" % (state._opens,))
    if not state.opened:
      # never opened, so not totally closed
      return False
    return True

  def join(self):
    ''' Join this object.

        Wait for the internal _finalise `Condition` (if still not `None`).
        Normally this is notified at the end of the shutdown procedure
        unless the object's `finalise_later` parameter was true.
    '''
    state = self.__mo_getstate()
    state._lock.acquire()
    if state._finalise:
      state._finalise.wait()
    else:
      state._lock.release()

  @staticmethod
  def is_opened(func):
    ''' Decorator to wrap `MultiOpenMixin` proxy object methods which
        should raise if the object is not yet open.
    '''

    def is_opened_wrapper(self, *a, **kw):
      ''' Wrapper method which checks that the instance is open.
      '''
      if self.closed:
        raise RuntimeError(
            "%s: %s: already closed from %s" %
            (is_opened_wrapper.__name__, self, self._final_close_from)
        )
      if not self.opened:
        raise RuntimeError(
            "%s: %s: not yet opened" % (is_opened_wrapper.__name__, self)
        )
      return func(self, *a, **kw)

    is_opened_wrapper.__name__ = "is_opened_wrapper(%s)" % (func.__name__,)
    return is_opened_wrapper

class _SubOpen(Proxy):
  ''' A single use proxy for another object with its own independent .closed attribute.

      The target use case is `MultiOpenMixin`s which return independent
      closables from their .open method.
  '''

  def __init__(self, proxied):
    Proxy.__init__(self, proxied)
    self.closed = False

  def close(self):
    ''' Close the proxy.
    '''
    if self.closed:
      raise RuntimeError("already closed")
    self._proxied.close()
    self.closed = True

class MultiOpen(MultiOpenMixin):
  ''' Context manager class that manages a single open/close object
      using a MultiOpenMixin.
  '''

  def __init__(self, openable, finalise_later=False):
    ''' Initialise: save the `openable` and call the MultiOpenMixin initialiser.
    '''
    MultiOpenMixin.__init__(self, finalise_later=finalise_later)
    self.openable = openable

  def startup(self):
    ''' Open the associated openable object.
    '''
    self.openable.open()

  def shutdown(self):
    ''' Close the associated openable object.
    '''
    self.openable.close()

class Pool(object):
  ''' A generic pool of objects on the premise that reuse is cheaper than recreation.

      All the pool objects must be suitable for use, so the
      `new_object` callable will typically be a closure.
      For example, here is the __init__ for a per-thread AWS Bucket using a
      distinct Session:

          def __init__(self, bucket_name):
              Pool.__init__(self, lambda: boto3.session.Session().resource('s3').Bucket(bucket_name)
  '''

  def __init__(self, new_object, max_size=None, lock=None):
    ''' Initialise the Pool with creator `new_object` and maximum size `max_size`.

        Parameters:
        * `new_object` is a callable which returns a new object for the Pool.
        * `max_size`: The maximum size of the pool of available objects saved for reuse.
            If omitted or `None`, defaults to 4.
            If 0, no upper limit is applied.
        * `lock`: optional shared Lock; if omitted or `None` a new Lock is allocated
    '''
    if max_size is None:
      max_size = 4
    if lock is None:
      lock = Lock()
    self.new_object = new_object
    self.max_size = max_size
    self.pool = []
    self._lock = lock

  def __str__(self):
    return "Pool(max_size=%s, new_object=%s)" % (
        self.max_size, self.new_object
    )

  @contextmanager
  def instance(self):
    ''' Context manager returning an object for use, which is returned to the pool afterwards.
    '''
    with self._lock:
      try:
        o = self.pool.pop()
      except IndexError:
        o = self.new_object()
    try:
      yield o
    finally:
      with self._lock:
        if self.max_size == 0 or len(self.pool) < self.max_size:
          self.pool.append(o)

class RunState(object):
  ''' A class to track a running task whose cancellation may be requested.

      Its purpose is twofold, to provide easily queriable state
      around tasks which can start and stop, and to provide control
      methods to pronounce that a task has started (`.start`),
      should stop (`.cancel`)
      and has stopped (`.stop`).

      A `RunState` can be used as a context manager, with the enter
      and exit methods calling `.start` and `.stop` respectively.
      Note that if the suite raises an exception
      then the exit method also calls `.cancel` before the call to `.stop`.

      Monitor or daemon processes can poll the `RunState` to see when
      they should terminate, and may also manage the overall state
      easily using a context manager.
      Example:

          def monitor(self):
              with self.runstate:
                  while not self.runstate.cancelled:
                      ... main loop body here ...

      A `RunState` has three main methods:
      * `.start()`: set `.running` and clear `.cancelled`
      * `.cancel()`: set `.cancelled`
      * `.stop()`: clear `.running`

      A `RunState` has the following properties:
      * `cancelled`: true if `.cancel` has been called.
      * `running`: true if the task is running.
        Further, assigning a true value to it sets `.start_time` to now.
        Assigning a false value to it sets `.stop_time` to now.
      * `start_time`: the time `.running` was last set to true.
      * `stop_time`: the time `.running` was last set to false.
      * `run_time`: `max(0,.stop_time-.start_time)`
      * `stopped`: true if the task is not running.
      * `stopping`: true if the task is running but has been cancelled.
      * `notify_start`: a set of callables called with the `RunState` instance
        to be called whenever `.running` becomes true.
      * `notify_end`: a set of callables called with the `RunState` instance
        to be called whenever `.running` becomes false.
      * `notify_cancel`: a set of callables called with the `RunState` instance
        to be called whenever `.cancel` is called.
  '''

  def __init__(self, name=None):
    self.name = name
    self._started_from = None
    # core state
    self._running = False
    self.cancelled = False
    # timing state
    self.start_time = None
    self.stop_time = None
    self.total_time = 0
    # callbacks
    self.notify_start = set()
    self.notify_cancel = set()
    self.notify_end = set()

  def __bool__(self):
    ''' Return true if the task is running.
    '''
    return self.running

  __nonzero__ = __bool__

  def __str__(self):
    return "%s:%s[%s:%gs]" % (
        (
            type(self).__name__ if self.name is None else ':'.join(
                (type(self).__name__, repr(self.name))
            )
        ), id(self), self.state, self.run_time
    )

  def __enter__(self):
    self.start(running_ok=True)
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    if exc_type:
      self.cancel()
    self.stop()

  @prop
  def state(self):
    ''' The `RunState`'s state as a string.

        Meanings:
        * `"pending"`: not yet running/started.
        * `"stopping"`: running and cancelled.
        * `"running"`: running and not cancelled.
        * `"cancelled"`: cancelled and no longer running.
        * `"stopped"`: no longer running and not cancelled.
    '''
    start_time = self.start_time
    if start_time is None:
      label = "pending"
    elif self.running:
      if self.cancelled:
        label = "stopping"
      else:
        label = "running"
    elif self.cancelled:
      label = "cancelled"
    else:
      label = "stopped"
    return label

  @pfx_method
  def start(self, running_ok=False):
    ''' Start: adjust state, set `start_time` to now.
        Sets `.cancelled` to `False` and sets `.running` to `True`.
    '''
    if not running_ok and self.running:
      warning("already running")
      print("runstate.start(): originally started from:", file=sys.stderr)
      stack_dump(Fs=self._started_from)
    else:
      self._started_from = stack_frames()
    self.cancelled = False
    self.running = True

  def stop(self):
    ''' Stop: adjust state, set `stop_time` to now.
        Sets sets `.running` to `False`.
    '''
    self.running = False

  # compatibility
  end = stop

  @property
  def running(self):
    ''' Property expressing whether the task is running.
    '''
    return self._running

  @running.setter
  def running(self, status):
    ''' Set the running property.

        `status`: the new running state, a Boolean

        A change in status triggers the time measurements.
    '''
    if self._running:
      # running -> not running
      if not status:
        self.stop_time = time.time()
        self.total_time += self.run_time
        for notify in self.notify_end:
          notify(self)
    elif status:
      # not running -> running
      self.start_time = time.time()
      for notify in self.notify_start:
        notify(self)
    self._running = status

  @property
  def stopping(self):
    ''' Is the process stopping? Running is true and cancelled is true.
    '''
    return self.running and self.cancelled

  @property
  def stopped(self):
    ''' Was the process stopped? Running is false and cancelled is true.
    '''
    return self.cancelled and not self.running

  def cancel(self):
    ''' Set the cancelled flag; the associated process should notice and stop.
    '''
    self.cancelled = True
    for notify in self.notify_cancel:
      notify(self)

  @property
  def run_time(self):
    ''' Property returning most recent run time (`stop_time-start_time`).
        If still running, use now as the stop time.
        If not started, return `0.0`.
    '''
    start_time = self.start_time
    if start_time is None:
      return 0.0
    if self.running:
      stop_time = time.time()
    else:
      stop_time = self.stop_time
    return max(0, stop_time - start_time)

class RunStateMixin(object):
  ''' Mixin to provide convenient access to a `RunState`.

      Provides: `.runstate`, `.cancelled`, `.running`, `.stopping`, `.stopped`.
  '''

  def __init__(self, runstate=None):
    ''' Initialise the `RunStateMixin`; sets the `.runstate` attribute.

        Parameters:
        * `runstate`: optional `RunState` instance or name.
          If a `str`, a new `RunState` with that name is allocated.
    '''
    if runstate is None:
      runstate = RunState(type(self).__name__)
    elif isinstance(runstate, str):
      runstate = RunState(runstate)
    self.runstate = runstate

  def cancel(self):
    ''' Call .runstate.cancel().
    '''
    return self.runstate.cancel()

  @property
  def cancelled(self):
    ''' Test .runstate.cancelled.
    '''
    return self.runstate.cancelled

  @property
  def running(self):
    ''' Test .runstate.running.
    '''
    return self.runstate.running

  @property
  def stopping(self):
    ''' Test .runstate.stopping.
    '''
    return self.runstate.stopping

  @property
  def stopped(self):
    ''' Test .runstate.stopped.
    '''
    return self.runstate.stopped
