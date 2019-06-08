#!/usr/bin/python
#
# Resourcing related classes and functions.
#       - Cameron Simpson <cs@cskk.id.au> 11sep2014
#

''' Resource management classes and functions.
'''

from __future__ import print_function
from contextlib import contextmanager
import sys
from threading import Condition, RLock, Lock
import time
from cs.logutils import error, warning
from cs.obj import O, Proxy
from cs.py.func import prop
from cs.py.stack import caller, frames as stack_frames, stack_dump

DISTINFO = {
    'description': "resourcing related classes and functions",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.logutils', 'cs.obj', 'cs.py.func', 'cs.py.stack'],
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
      raise ClosedError("%s: %s: already closed" % (not_closed_wrapper.__name__, self))
    return func(self, *a, **kw)
  not_closed_wrapper.__name__ = "not_closed_wrapper(%s)" % (func.__name__,)
  return not_closed_wrapper


_mom_lockclass = RLock

## debug: TrackedClassMixin
class MultiOpenMixin(O):
  ''' A mixin to count open and close calls, and to call .startup
      on the first .open and to call .shutdown on the last .close.

      Recommended subclass implementations do as little as possible
      during __init__, and do almost all setup during startup so
      that the class may perform multiple startup/shutdown iterations.

      If used as a context manager calls open()/close() from
      __enter__() and __exit__().

      Multithread safe.

      This mixin defines ._lock = RLock(); subclasses need not
      bother, but may supply their own lock.

      Classes using this mixin need to define .startup and .shutdown.
  '''

  def __init__(self, finalise_later=False, lock=None, subopens=False):
    ''' Initialise the MultiOpenMixin state.

        Parameters:
        * `finalise_later`: do not notify the finalisation Condition on
          shutdown, require a separate call to .finalise().
          This is mode is useful for objects such as queues where
          the final close prevents further .put calls, but users
          calling .join may need to wait for all the queued items
          to be processed.
        * `lock`: if set and not None, an RLock to use; otherwise one will be allocated

        TODO:
        * `subopens`: if true (default false) then .open will return
          a proxy object with its own .closed attribute set by the
          proxy's .close.
    '''
    if subopens:
      raise RuntimeError("subopens not implemented")
    O.__init__(self)
    ##INACTIVE##TrackedClassMixin.__init__(self, MultiOpenMixin)
    if lock is None:
      lock = _mom_lockclass()
    self.opened = False
    self._opens = 0
    self._opened_from = {}
    ##self.closed = False # final _close() not yet called
    self._final_close_from = None
    self._lock = lock
    self.__mo_lock = _mom_lockclass()
    self._finalise_later = finalise_later
    self._finalise = None

  def tcm_get_state(self):
    ''' Support method for TrackedClassMixin.
    '''
    return {'opened': self.opened, 'opens': self._opens}

  def __enter__(self):
    self.open(caller_frame=caller())
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    self.close(caller_frame=caller())
    return False

  def open(self, caller_frame=None):
    ''' Increment the open count.
        On the first .open call self.startup().
    '''
    if False:
      if caller_frame is None:
        caller_frame = caller()
      frame_key = caller_frame.filename, caller_frame.lineno
      self._opened_from[frame_key] = self._opened_from.get(frame_key, 0) + 1
    self.opened = True
    with self.__mo_lock:
      opens = self._opens
      opens += 1
      self._opens = opens
      if opens == 1:
        self._finalise = Condition(self.__mo_lock)
        self.startup()
    return self

  def close(self, enforce_final_close=False, caller_frame=None):
    ''' Decrement the open count.
        If the count goes to zero, call self.shutdown() and return its value.

        Parameters:
        * `enforce_final_close`: if true, the caller expects this to
          be the final close for the object and a RuntimeError is
          raised if this is not actually the case.
        * `caller_frame`: used for debugging; the caller may specify
          this if necessary, otherwise it is computed from
          cs.py.stack.caller when needed. Presently the caller of the
          final close is recorded to help debugging extra close calls.
    '''
    if not self.opened:
      raise RuntimeError("%s: close before initial open" % (self,))
    retval = None
    with self.__mo_lock:
      opens = self._opens
      if opens < 1:
        error("%s: UNDERFLOW CLOSE", self)
        error("  final close was from %s", self._final_close_from)
        for frame_key in sorted(self._opened_from.keys()):
          error("  opened from %s %d times", frame_key, self._opened_from[frame_key])
        ##from cs.debug import thread_dump
        ##from threading import current_thread
        ##thread_dump([current_thread()])
        ##raise RuntimeError("UNDERFLOW CLOSE of %s" % (self,))
        return retval
      opens -= 1
      self._opens = opens
      if opens == 0:
        ##INACTIVE##self.tcm_dump(MultiOpenMixin)
        if caller_frame is None:
          caller_frame = caller()
        self._final_close_from = caller_frame
        retval = self.shutdown()
    if opens == 0:
      if not self._finalise_later:
        self.finalise()
    else:
      if enforce_final_close:
        raise RuntimeError("%s: expected this to be the final close, but it was not" % (self,))
    return retval

  def finalise(self):
    ''' Finalise the object, releasing all callers of .join().
        Normally this is called automatically after .shutdown unless
        `finalise_later` was set to true during initialisation.
    '''
    with self.__mo_lock:
      finalise = self._finalise
      if finalise is None:
        raise RuntimeError("%s: finalised more than once" % (self,))
      self._finalise = None
      finalise.notify_all()

  @property
  def closed(self):
    ''' Whether this object has been closed.
        Note: false if never opened.
    '''
    if self._opens > 0:
      return False
    ##if self._opens < 0:
    ##  raise RuntimeError("_OPENS UNDERFLOW: _opens < 0: %r" % (self._opens,))
    if not self.opened:
      # never opened, so not totally closed
      return False
    return True

  def join(self):
    ''' Join this object.

        Wait for the internal _finalise Condition (if still not None).
        Normally this is notified at the end of the shutdown procedure
        unless the object's `finalise_later` parameter was true.
    '''
    self.__mo_lock.acquire()
    if self._finalise:
      self._finalise.wait()
    else:
      self.__mo_lock.release()

  @staticmethod
  def is_opened(func):
    ''' Decorator to wrap MultiOpenMixin proxy object methods which
        should raise if the object is not yet open.
    '''
    def is_opened_wrapper(self, *a, **kw):
      ''' Wrapper method which checks that the instance is open.
      '''
      if self.closed:
        raise RuntimeError(
            "%s: %s: already closed from %s"
            % (is_opened_wrapper.__name__, self, self._final_close_from))
      if not self.opened:
        raise RuntimeError(
            "%s: %s: not yet opened"
            % (is_opened_wrapper.__name__, self))
      return func(self, *a, **kw)
    is_opened_wrapper.__name__ = "is_opened_wrapper(%s)" % (func.__name__,)
    return is_opened_wrapper

class _SubOpen(Proxy):
  ''' A single use proxy for another object with its own independent .closed attribute.

      The target use case is MultiOpenMixins which return independent
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

  def __init__(self, openable, finalise_later=False, lock=None):
    ''' Initialise: save the `openable` and call the MultiOpenMixin initialiser.
    '''
    MultiOpenMixin.__init__(self, finalise_later=finalise_later, lock=lock)
    self.openable = openable

  def startup(self):
    ''' Open the associated openable object.
    '''
    self.openable.open()

  def shutdown(self):
    ''' Close the associated openable object.
    '''
    self.openable.close()

class Pool(O):
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
    O.__init__(self)
    if max_size is None:
      max_size = 4
    if lock is None:
      lock = Lock()
    self.new_object = new_object
    self.max_size = max_size
    self.pool = []
    self._lock = lock

  def __str__(self):
    return "Pool(max_size=%s, new_object=%s)" % (self.max_size, self.new_object)

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

      A `RunState` can be used a a context manager, with the enter
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
        Further, assigning a true value to it also sets `.start_time` to now.
        Assigning a false value to it also sets `.stop_time` to now.
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
        ( type(self).__name__
          if self.name is None
          else ':'.join( (type(self).__name__, repr(self.name)) ) ),
        id(self), self.state, self.run_time
    )

  def __enter__(self):
    self.start()
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

  def start(self):
    ''' Start: adjust state, set `start_time` to now.
        Sets `.cancelled` to `False` and sets `.running` to `True`.
    '''
    if self.running:
      warning("runstate.start() when already running")
      print("runstate.start(): originally started from:", file=sys.stderr)
      stack_dump(Fs=self._started_from)
    else:
      self._started_from = stack_frames()
    assert not self.running
    self.cancelled = False
    self.running = True

  def stop(self):
    ''' Stop: adjust state, set `stop_time` to now.
        Sets sets `.running` to `False`.
    '''
    assert self.running
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

        `runstate`: `RunState` instance or name.
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
