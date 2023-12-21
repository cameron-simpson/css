#!/usr/bin/python
#
# Resourcing related classes and functions.
# - Cameron Simpson <cs@cskk.id.au> 11sep2014
#

''' Resource management classes and functions.
'''

from collections import defaultdict
from contextlib import contextmanager
import sys
from threading import Condition, Lock, RLock, current_thread, main_thread
import time
from typing import Any, Callable, Optional, Tuple, Union

from typeguard import typechecked

from cs.context import stackattrs, setup_cmgr, ContextManagerMixin
from cs.deco import default_params
from cs.gimmicks import error, warning, nullcontext
from cs.obj import Proxy
from cs.pfx import pfx_call, pfx_method
from cs.psutils import signal_handlers
from cs.py.func import prop
from cs.py.stack import caller, frames as stack_frames, stack_dump
from cs.result import CancellationError
from cs.threads import ThreadState, HasThreadState

__version__ = '20231221'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.context',
        'cs.deco',
        'cs.gimmicks',
        'cs.obj',
        'cs.pfx',
        'cs.psutils',
        'cs.py.func',
        'cs.py.stack',
        'cs.result',
        'cs.threads',
        'typeguard',
    ],
}

class ClosedError(Exception):
  ''' Exception for operations invalid when something is closed.
  '''

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

# pylint: disable=too-few-public-methods,too-many-instance-attributes
class _mom_state(object):

  def __init__(self, mom: "MultiOpenMixin"):
    self.mom = mom
    self.opened = False
    self.opens = 0
    self.opens_from = defaultdict(int)
    ##self.closed = False # final _close() not yet called
    self.final_close_from = None
    self._lock = RLock()
    self.join_lock = None

  def __repr__(self):
    return "%s:%r" % (self.__class__.__name__, self.__dict__)

  def open(self, caller_frame=None) -> int:
    ''' The open process:
        Bump the opens counter.
        If it goes to 1, run the startup phase of `self.mom.startup_shutdown`.
        Return the bumped opens counter.
    '''
    with self._lock:
      opens = self.opens
      opens += 1
      self.opens = opens
      if caller_frame is not None:
        frame_key = caller_frame.filename, caller_frame.lineno
        self.opens_from[frame_key] += 1
      if opens == 1:
        self.join_lock = Lock()
        self.join_lock.acquire()
        self._teardown = setup_cmgr(self.mom.startup_shutdown())
      self.opened = True
    return opens

  def close(
      self,
      *,
      caller_frame=None,
      enforce_final_close=False,
      unopened_ok=False,
  ) -> Tuple[int, Any]:
    ''' The close process:
        Decrement the opens counter.
        If it goes to 0, run the shutdown phase of `self.mom.startup_shutdown`.
        Return the bumped opens counter and the return value of the shutdown phase
        (or `None` if the shutdown was not run).
    '''
    if not self.opened:
      if unopened_ok:
        return None
      raise RuntimeError("%s: close before initial open" % (self,))
    retval = None
    with self._lock:
      opens = self.opens
      if opens < 1:
        error("%s: UNDERFLOW CLOSE from %s", self, caller())
        error("  final close was from %s", self.final_close_from)
        for frame_key in sorted(self.opens_from.keys()):
          error(
              "  opened from %s %d times", frame_key,
              self.opens_from[frame_key]
          )
        return retval
      opens -= 1
      self.opens = opens
      if opens == 0:
        ##INACTIVE##self.tcm_dump(MultiOpenMixin)
        self.final_close_from = caller_frame
        teardown, self._teardown = self._teardown, None
        retval = teardown()
        self.join_lock.release()
        self.join_lock = None
    if enforce_final_close and opens != 0:
      raise RuntimeError(
          "%s: expected this to be the final close, but it was not" % (self,)
      )
    return opens, retval

  def join(self):
    ''' Synchronise with the final `close()` call.
        Calling this before the initial `open()` raises a `ClosedError`.
    '''
    lock = self.join_lock
    if lock is None:
      if not self.opened:
        raise ClosedError("%s has not been opened")
      return
    lock.acquire()
    lock.release()

## debug: TrackedClassMixin
class MultiOpenMixin(ContextManagerMixin):
  ''' A multithread safe mixin to count open and close calls,
      and to call `.startup` on the first `.open`
      and to call `.shutdown` on the last `.close`.

      If used as a context manager this mixin calls `open()`/`close()` from
      `__enter__()` and `__exit__()`.

      It is recommended subclass implementations do as little as possible
      during `__init__`, and do almost all setup during startup so
      that the class may perform multiple startup/shutdown iterations.

      Classes using this mixin need to:
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
      state = self.__dict__['_MultiOpenMixin_state'] = _mom_state(self)
      assert state.opens == 0
    return state

  def tcm_get_state(self):
    ''' Support method for `TrackedClassMixin`.
    '''
    state = self.__mo_getstate()
    return {'opened': state.opened, 'opens': state.opens}

  def __enter_exit__(self):
    self.open(caller_frame=caller())
    try:
      yield
    finally:
      self.close()  ##caller_frame=caller())

  @contextmanager
  def startup_shutdown(self):
    ''' Default context manager form of startup/shutdown - just
        call the distinct `.startup()` and `.shutdown()` methods
        if both are present, do nothing if neither is present.

        This supports subclasses always using:

            with super().startup_shutdown():

        as an outer wrapper.

        The `.startup` check is to support legacy subclasses of
        `MultiOpenMixin` which have separate `startup()` and
        `shutdown()` methods.
        The preferred approach is a single `startup_shutdwn()`
        context manager overriding this method.

        The usual form looks like this:

            @contextmanager
            def startup_shutdown(self):
                with super().startup_shutdown():
                    ... do some set up ...
                    try:
                        yield
                    finally:
                        ... do some tear down ...
    '''
    try:
      startup = self.startup
    except AttributeError:
      shutdown = None
    else:
      shutdown = self.shutdown
      startup()
    try:
      yield
    finally:
      if shutdown is not None:
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
      state.opens_from[frame_key] = state.opens_from.get(frame_key, 0) + 1
    state.open(caller_frame=caller_frame)
    return self

  def close(
      self,
      *,
      enforce_final_close=False,
      caller_frame=None,
      unopened_ok=False
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
    if False:
      if caller_frame is None:
        caller_frame = caller()
      frame_key = caller_frame.filename, caller_frame.lineno
      state.opens_from[frame_key] = state.opens_from.get(frame_key, 0) + 1
    opens, retval = state.close(
        caller_frame=caller_frame,
        enforce_final_close=enforce_final_close,
        unopened_ok=unopened_ok,
    )
    return retval

  @property
  def closed(self):
    ''' Whether this object has been closed.
        Note: False if never opened.
    '''
    state = self.__mo_getstate()
    if state.opens > 0:
      return False
    ##if state.opens < 0:
    ##  raise RuntimeError("OPENS UNDERFLOW: opens < 0: %r" % (state.opens,))
    if not state.opened:
      # never opened, so not totally closed
      return False
    return True

  def join(self):
    ''' Join this object.

        Wait for the internal finalise `Condition` (if still not `None`).
        Normally this is notified at the end of the shutdown procedure
        unless the object's `finalise_later` parameter was true.
    '''
    self.__mo_getstate().join()

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
            (is_opened_wrapper.__name__, self, self.final_close_from)
        )
      if not self.opened:
        raise RuntimeError(
            "%s: %s: not yet opened" % (is_opened_wrapper.__name__, self)
        )
      return func(self, *a, **kw)

    is_opened_wrapper.__name__ = "is_opened_wrapper(%s)" % (func.__name__,)
    return is_opened_wrapper

# pylint: disable=too-few-public-methods
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

@contextmanager
def openif(obj):
  ''' Context manager to open `obj` if it has a `.open` method
      and also to close it via its `.close` method.
      This yields `obj.open()` if defined, or `obj` otherwise.
  '''
  try:
    open_method = obj.open
  except AttributeError:
    close_method = None
    opened = obj
  else:
    close_method = obj.close
    opened = pfx_call(open_method)
  try:
    yield opened
  finally:
    if close_method is not None:
      pfx_call(close_method)

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

# pylint: disable=too-many-instance-attributes
class RunState(HasThreadState):
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

  perthread_state = ThreadState()

  def __init__(
      self,
      name=None,
      signals=None,
      handle_signal=None,
      poll_cancel: Optional[Callable] = None,
      verbose=False,
  ):
    self.name = name
    self.verbose = verbose
    self._started_from = None
    self._signals = tuple(signals) if signals else ()
    self._sigstack = None
    self._sighandler = handle_signal or self.handle_signal
    # core state
    self._running = False
    self._cancelled = False
    self.poll_cancel = poll_cancel
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

  def __enter_exit__(self):
    ''' The `__enter__`/`__exit__` generator function:
        * push this RunState via HasThreadState
        * catch signals
        * start
        * `yield self` => run
        * cancel on exception during run
        * stop

        Note that if the `RunState` is already runnings we do not
        do any of that stuff apart from the `yield self` because
        we assume whatever setup should have been done has already
        been done.
        In particular, the `HasThreadState.Thread` factory calls this
        in the "running" state.
    '''
    with HasThreadState.as_contextmanager(self):
      if self.running:
        # we're already running - do not change states or push signal handlers
        # typical situation is HasThreadState.Thread setting up the "current" RunState
        yield self
      else:
        # if we're not in the main thread we suppress the signal shuffling as well
        in_main = current_thread() is main_thread()
        with (self.catch_signal(self._signals, call_previous=False,
                                handle_signal=self._sighandler)
              if in_main else nullcontext()) as sigstack:
          with (stackattrs(self, _sigstack=sigstack)
                if sigstack is not None else nullcontext()):
            self.start(running_ok=True)
            try:
              yield self
            except Exception:
              self.cancel()
              raise
            finally:
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
  def cancelled(self):
    ''' Test the .cancelled attribute, including a poll if supplied.
    '''
    if self._cancelled:
      return True
    if self.poll_cancel:
      if self.poll_cancel():
        self._cancelled = True
        return True
    return False

  @cancelled.setter
  def cancelled(self, cancel_status):
    ''' Set the .cancelled attribute.
    '''
    self._cancelled = cancel_status

  def raiseif(self, msg=None, *a):
    ''' Raise `CancellationError` is cancelled.

        Example:

            for item in items:
                runstate.raiseif()
                ... process item ...
    '''
    if self.cancelled:
      if msg is None:
        msg = "%s.cancelled" % (self,)
      else:
        if a:
          msg = msg % a
      raise CancellationError(msg)

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

  @contextmanager
  def catch_signal(
      self,
      sig,
      call_previous=False,
      handle_signal=None,
  ):
    ''' Context manager to catch the signal or signals `sig` and
        cancel this `RunState`.
        Restores the previous handlers on exit.
        Yield a mapping of `sig`=>`old_handler`.

        Parameters:
        * `sig`: an `int` signal number or an iterable of signal numbers
        * `call_previous`: optional flag (default `False`)
          passed to `cs.psutils.signal_handlers`
    '''
    if handle_signal is None:
      handle_signal = self.handle_signal
    sigs = (sig,) if isinstance(sig, int) else sig
    sig_hnds = {signum: handle_signal for signum in sigs}
    with signal_handlers(sig_hnds,
                         call_previous=call_previous) as old_handler_map:
      yield old_handler_map

  def handle_signal(self, sig, _):
    ''' `RunState` signal handler: cancel the run state.
        Warn if `self.verbose`.
      '''
    # pylint: disable=expression-not-assigned
    if self.verbose:
      warning("%s: received signal %s, cancelling", self, sig)
    self.cancel()

# use the prevailing RunState or make a fresh one
uses_runstate = default_params(
    runstate=lambda: RunState.default() or RunState()
)

class RunStateMixin(object):
  ''' Mixin to provide convenient access to a `RunState`.

      Provides: `.runstate`, `.cancelled`, `.running`, `.stopping`, `.stopped`.
  '''

  @typechecked
  def __init__(self, runstate: Optional[Union[RunState, str]]):
    ''' Initialise the `RunStateMixin`; sets the `.runstate` attribute.

        Parameters:
        * `runstate`: optional `RunState` instance or name.
          If a `str`, a new `RunState` with that name is allocated.
          If omitted, the default `RunState` is used.
    '''
    if runstate is None:
      runstate = RunState(runstate)
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
