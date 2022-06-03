#!/usr/bin/python
#
# pylint: disable=too-many-lines
#

r'''
Queue functions for execution later in priority and time order.

I use `Later` objects for convenient queuing of functions whose
execution occurs later in a priority order with capacity constraints.

Why not futures?
I already had this before futures came out,
I prefer its naming scheme and interface,
and futures did not then support prioritised execution.

Use is simple enough: create a `Later` instance and typically queue
functions with the `.defer()` method::

    L = Later(4)      # a Later with a parallelism of 4
    ...
    LF = L.defer(func, *args, **kwargs)
    ...
    x = LF()          # collect result

The `.defer` method and its siblings return a `LateFunction`,
which is a subclass of `cs.result.Result`.
As such it is a callable,
so to collect the result you just call the `LateFunction`.
'''

from __future__ import print_function
from contextlib import contextmanager
from functools import partial
from heapq import heappush, heappop
import logging
import sys
from threading import Lock, Thread, Event
import time

from cs.context import stackattrs
from cs.deco import OBSOLETE
from cs.excutils import logexc
import cs.logutils
from cs.logutils import error, warning, info, debug, ifdebug, exception, D
from cs.pfx import pfx_method
from cs.py.func import funcname
from cs.queues import IterableQueue, TimerQueue
from cs.resources import MultiOpenMixin
from cs.result import Result, report, after
from cs.seq import seq
from cs.threads import bg as bg_thread, State as ThreadState

from cs.x import X

__version__ = '20201021-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.excutils',
        'cs.logutils',
        'cs.pfx',
        'cs.py.func',
        'cs.queues',
        'cs.resources',
        'cs.result',
        'cs.seq',
        'cs.threads',
    ],
}

DEFAULT_RETRY_DELAY = 0.1

default = ThreadState(current=None)

def defer(func, *a, **kw):
  ''' Queue a function using the current default Later.
      Return the `LateFunction`.
  '''
  return default.current.defer(func, *a, **kw)

class RetryError(Exception):
  ''' Exception raised by functions which should be resubmitted to the queue.
  '''

def retry(retry_interval, func, *a, **kw):
  ''' Call the callable `func` with the supplied arguments.

      If it raises `RetryError`,
      run `time.sleep(retry_interval)`
      and then call again until it does not raise `RetryError`.
  '''
  while True:
    try:
      return func(*a, **kw)
    except RetryError:
      time.sleep(retry_interval)

class _Late_context_manager(object):
  ''' The `_Late_context_manager` is a context manager to run a suite via an
      existing Later object. Example usage:

          L = Later(4)    # a 4 thread Later
          ...
          with L.ready( ... optional Later.submit() args ... ):
            ... do stuff when L queues us ...

      This permits easy inline scheduled code.
  '''

  # pylint: disable=too-many-arguments
  def __init__(
      self, L, priority=None, delay=None, when=None, name=None, pfx=None
  ):
    self.later = L
    self.parameters = {
        'priority': priority,
        'delay': delay,
        'when': when,
        'name': name,
        'pfx': pfx,
    }
    self.commence = Lock()
    self.commence.acquire()
    self.completed = Lock()
    self.commence.acquire()

  def __enter__(self):
    ''' Entry handler: submit a placeholder function to the queue,
        acquire the "commence" lock, which will be made available
        when the placeholder gets to run.
    '''

    def run():
      ''' This is the placeholder function dispatched by the `Later` instance.
          It releases the "commence" lock for `__enter__` to acquire,
          permitting the with-suite to commence.
          It then blocks waiting to acquire the "completed" lock;
          `__exit__` releases that lock permitting the placeholder to return
          and release the `Later` resource.
      '''
      self.commence.release()
      self.completed.acquire()
      return "run done"

    # queue the placeholder function and wait for it to execute
    self.latefunc = self.later.submit(run, **self.parameters)
    self.commence.acquire()
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    ''' Exit handler: release the "completed" lock; the placeholder
        function is blocking on this, and will return on its release.
    '''
    self.completed.release()
    if exc_type is not None:
      return False
    W = self.latefunc.wait()
    self.latefunc = None
    _, lf_exc_info = W
    if lf_exc_info is not None:
      raise lf_exc_info[1]
    return True

class LateFunction(Result):
  ''' State information about a pending function,
      a subclass of `cs.result.Result`.

      A `LateFunction` is callable,
      so a synchronous call can be done like this:

          def func():
            return 3
          L = Later(4)
          LF = L.defer(func)
          x = LF()
          print(x)        # prints 3

      Used this way, if the called function raises an exception it is visible:

          LF = L.defer()
          try:
            x = LF()
          except SomeException as e:
            # handle the exception ...

      To avoid handling exceptions with try/except the .wait()
      method should be used:

          LF = L.defer()
          x, exc_info = LF.wait()
          if exc_info:
            # handle exception
            exc_type, exc_value, exc_traceback = exc_info
            ...
          else:
            # use `x`, the function result

      TODO: .cancel(), timeout for wait().
  '''

  def __init__(self, func, name=None, retry_delay=None):
    ''' Initialise a `LateFunction`.

        Parameters:
        * `func` is the callable for later execution.
        * `name`, if supplied, specifies an identifying name for the `LateFunction`.
        * `retry_local`: time delay before retry of this function on RetryError.
          Default from `later.retry_delay`.
    '''
    Result.__init__(self)
    self.func = func
    if name is None:
      name = "LF-%d[%s]" % (seq(), funcname(func))
    if retry_delay is None:
      retry_delay = DEFAULT_RETRY_DELAY
    self.name = name
    self.retry_delay = retry_delay

  def __str__(self):
    return "%s[%s]" % (type(self).__name__, self.name)

  def _resubmit(self):
    ''' Resubmit this function for later execution.
    '''
    # TODO: put the retry logic in Later notify func, resubmit with delay from there
    self.later._submit(
        self.func, delay=self.retry_delay, name=self.name, LF=self
    )

  def _dispatch(self):
    ''' ._dispatch() is called by the Later class instance's worker thread.
        It causes the function to be handed to a thread for execution.
    '''
    return self.bg(self.func)

  @OBSOLETE
  def wait(self):
    ''' Obsolete name for `.join`.
    '''
    return self.join()

  @pfx_method(use_str=True)
  def _complete(self, result, exc_info):
    ''' Wrapper for `Result._complete` which handles `RetryError`s.

        Further,
        if the function raises one of `NameError`, `AttributeError`
        or `RuntimeError`
        (broadly: "programmer errors"),
        report the stack trace to aid debugging.
    '''
    if exc_info:
      e = exc_info[1]
      if isinstance(e, RetryError):
        # resubmit this function
        warning("resubmit after RetryError: %s", e)
        self._resubmit()
        return
      if isinstance(e, (NameError, AttributeError, RuntimeError)):
        error("%s", e, exc_info=exc_info)
    Result._complete(self, result, exc_info)

# pylint: disable=too-many-public-methods,too-many-instance-attributes
class Later(MultiOpenMixin):
  ''' A management class to queue function calls for later execution.

      Methods are provided for submitting functions to run ASAP or
      after a delay or after other pending functions. These methods
      return `LateFunction`s, a subclass of `cs.result.Result`.

      A Later instance' close method closes the Later for further
      submission.
      Shutdown does not imply that all submitted functions have
      completed or even been dispatched.
      Callers may wait for completion and optionally cancel functions.

      TODO: __enter__ returns a SubLater, __exit__ closes the SubLater.

      TODO: drop global default Later.
  '''

  def __init__(self, capacity, name=None, inboundCapacity=0, retry_delay=None):
    ''' Initialise the Later instance.

        Parameters:
        * `capacity`: resource contraint on this Later; if an int, it is used
          to size a Semaphore to constrain the number of dispatched functions
          which may be in play at a time; if not an int it is presumed to be a
          suitable Semaphore-like object, perhaps shared with other subsystems.
        * `name`: optional identifying name for this instance.
        * `inboundCapacity`: if >0, used as a limit on the number of
          undispatched functions that may be queued up; the default is 0 (no
          limit).  Calls to submit functions when the inbound limit is reached
          block until some functions are dispatched.
        * `retry_delay`: time delay for requeued functions.
          Default: `DEFAULT_RETRY_DELAY`.
    '''
    if name is None:
      name = "Later-%d" % (seq(),)
    if ifdebug():
      import inspect  # pylint: disable=import-outside-toplevel
      filename, lineno = inspect.stack()[1][1:3]
      name = "%s[%s:%d]" % (name, filename, lineno)
    debug(
        "Later.__init__(capacity=%s, inboundCapacity=%s, name=%s)", capacity,
        inboundCapacity, name
    )
    if retry_delay is None:
      retry_delay = DEFAULT_RETRY_DELAY
    self.capacity = capacity
    self.inboundCapacity = inboundCapacity
    self.retry_delay = retry_delay
    self.name = name
    self._lock = Lock()
    self.outstanding = set()  # dispatched but uncompleted LateFunctions
    self.delayed = set()  # unqueued, delayed until specific time
    self.pending = []  # undispatched LateFunctions, a heap
    self.running = set()  # running LateFunctions
    # counter tracking jobs queued or active
    self._state = ""
    self.logger = None  # reporting; see logTo() method
    self._priority = (0,)
    self._timerQ = None  # queue for delayed requests; instantiated at need
    # inbound requests queue
    self._finished = None

  @contextmanager
  def startup_shutdown(self):
    self._finished = Event()
    global default  # pylint: disable=global-statement
    with stackattrs(default, current=self):
      try:
        yield
      finally:
        # Shut down the Later instance:
        # - close the request queue
        # - close the TimerQueue if any
        # - close the worker thread pool
        # - dispatch a Thread to wait for completion and fire the
        #   _finished Event
        if self._timerQ:
          self._timerQ.close()
          self._timerQ.join()
        # queue actions to detect activity completion
        bg_thread(self._finished.set)

  def _try_dispatch(self):
    ''' Try to dispatch the next `LateFunction`.
        Does nothing if insufficient capacity or no pending tasks.
        Return the dispatched `LateFunction` or `None`.
    '''
    LF = None
    with self._lock:
      if len(self.running) < self.capacity:
        try:
          pri_entry = heappop(self.pending)
        except IndexError:
          pass
        else:
          LF = pri_entry[-1]
          self.running.add(LF)
          # NB: we set up notify before dispatch so that it cannot
          # fire before we release the lock (which would happen if
          # the LF completes really fast - notify fires immediately
          # in the current thread if the function is already complete).
          LF.notify(self._complete_LF)
          debug("LATER: dispatch %s", LF)
          LF._dispatch()
      elif self.pending:
        debug("LATER: at capacity, nothing dispatched: %s", self)
    return LF

  def _complete_LF(self, LF):
    ''' Process a completed `LateFunction`: remove from .running,
        try to dispatch another function.
    '''
    with self._lock:
      self.running.remove(LF)
    self._try_dispatch()

  @property
  def finished(self):
    ''' Probe the finishedness.
    '''
    return self._finished.is_set()

  def wait(self):
    ''' Wait for the Later to be finished.
    '''
    f = self._finished
    if not f.is_set():
      info("Later.WAIT: %r", self)
    if not self._finished.wait(5.0):
      warning("  Later.WAIT TIMED OUT")

  def __repr__(self):
    return (
        '<%s "%s" capacity=%s running=%d pending=%d delayed=%d closed=%s>' % (
            self.__class__.__name__, self.name, self.capacity, len(
                self.running
            ), len(self.pending), len(self.delayed), self.closed
        )
    )

  def __str__(self):
    return (
        "<%s[%s] pending=%d running=%d delayed=%d>" % (
            self.name, self.capacity, len(self.pending), len(self.running),
            len(self.delayed)
        )
    )

  def state(self, new_state, *a):
    ''' Update the state of this Later.
    '''
    if a:
      new_state = new_state % a
    D("STATE %r [%s]", new_state, self)
    self._state = new_state

  def __call__(self, func, *a, **kw):
    ''' A Later object can be called with a function and arguments
        with the effect of deferring the function and waiting for
        it to complete, returning its return value.

        Example:

          def f(a):
            return a*2
          x = L(f, 3)   # x == 6
    '''
    return self.defer(func, *a, **kw)()

  def log_status(self):
    ''' Log the current delayed, pending and running state.
    '''
    for LF in list(self.delayed):
      self.debug("STATUS: delayed: %s", LF)
    for LF in list(self.pending):
      self.debug("STATUS: pending: %s", LF)
    for LF in list(self.running):
      self.debug("STATUS: running: %s", LF)

  def logTo(self, filename, logger=None, log_level=None):
    ''' Log to the file specified by `filename` using the specified
        logger named `logger` (default the module name, cs.later) at the
        specified log level `log_level` (default logging.INFO).
    '''
    if logger is None:
      logger = self.__module__
    if log_level is None:
      log_level = logging.INFO
    logger, handler = cs.logutils.logTo(filename, logger=logger)
    handler.setFormatter(
        logging.Formatter("%(asctime)-15s %(later_name)s %(message)s")
    )
    logger.setLevel(log_level)
    self.logger = logger

  def error(self, *a, **kw):
    ''' Issue an error message with `later_name` in `'extra'`.
    '''
    if self.logger:
      kw.setdefault('extra', {}).update(later_name=str(self))
      self.logger.error(*a, **kw)

  def warning(self, *a, **kw):
    ''' Issue a warning message with `later_name` in `'extra'`.
    '''
    if self.logger:
      kw.setdefault('extra', {}).update(later_name=str(self))
      self.logger.warning(*a, **kw)

  def info(self, *a, **kw):
    ''' Issue an info message with `later_name` in `'extra'`.
    '''
    if self.logger:
      kw.setdefault('extra', {}).update(later_name=str(self))
      self.logger.info(*a, **kw)

  def debug(self, *a, **kw):
    ''' Issue a debug message with `later_name` in `'extra'`.
    '''
    if self.logger:
      kw.setdefault('extra', {}).update(later_name=str(self))
      self.logger.debug(*a, **kw)

  @property
  def submittable(self):
    ''' May new tasks be submitted?
        This normally tracks "not self.closed", but running tasks
        are wrapped in a thread local override to permit them to
        submit further related tasks.
    '''
    return not self.closed

  def bg(self, func, *a, **kw):
    ''' Queue a function to run right now,
        ignoring the `Later`'s capacity and priority system.

        This is really just an easy way to utilise the `Later`'s thread pool
        and get back a handy `LateFunction` for result collection.
        Frankly, you're probably better off using `cs.result.bg` instead.

        It can be useful for transient control functions that themselves
        queue things through the `Later` queuing system but do not want to
        consume capacity themselves, thus avoiding deadlock at the cost of
        transient overthreading.

        The premise here is that the capacity limit
        is more about managing compute contention than pure `Thread` count,
        and that control functions should arrange other subfunctions
        and then block or exit,
        thus consuming neglible compute.
        It is common to want to dispatch a higher order operation
        via such a control function,
        but that higher order would itself normally consume some
        of the capacity
        thus requiring an an hoc increase to the required capacity
        to avoid deadlock.
    '''
    if not self.submittable:
      raise RuntimeError("%s.bg(...) but not self.submittable" % (self,))
    name = None
    if isinstance(func, str):
      name = func
      a = list(a)
      func = a.pop(0)
    if a or kw:
      func = partial(func, *a, **kw)
    LF = LateFunction(func, name=name)
    LF._dispatch()
    return LF

  def ready(self, **kwargs):
    ''' Awful name.
        Return a context manager to block until the Later provides a timeslot.
    '''
    return _Late_context_manager(self, **kwargs)

  # pylint: disable=too-many-arguments
  def submit(
      self, func, priority=None, delay=None, when=None, name=None, pfx=None
  ):
    ''' Submit the callable `func` for later dispatch.
        Return the corresponding `LateFunction` for result collection.

        If the parameter `priority` is not None then use it as the priority
        otherwise use the default priority.

        If the parameter `delay` is not None, delay consideration of
        this function until `delay` seconds from now.

        If the parameter `when` is not None, delay consideration of
        this function until the time `when`.
        It is an error to specify both `when` and `delay`.

        If the parameter `name` is not None, use it to name the `LateFunction`.

        If the parameter `pfx` is not None, submit pfx.partial(func);
          see the cs.logutils.Pfx.partial method for details.

        If the parameter `LF` is not None, construct a new `LateFunction` to
          track function completion.
    '''
    if not self.submittable:
      raise RuntimeError("%s.submit(...) but not self.submittable" % (self,))
    return self._submit(
        func, priority=priority, delay=delay, when=when, name=name, pfx=pfx
    )

  # pylint: disable=too-many-arguments
  def _submit(
      self,
      func,
      priority=None,
      delay=None,
      when=None,
      name=None,
      pfx=None,
      LF=None,
      retry_delay=None
  ):
    if delay is not None and when is not None:
      raise ValueError(
          "you can't specify both delay= and when= (%s, %s)" % (delay, when)
      )
    if priority is None:
      priority = self._priority
    elif isinstance(priority, int):
      priority = (priority,)
    if pfx is not None:
      func = pfx.partial(func)
    if LF is None:
      LF = LateFunction(func, name=name, retry_delay=retry_delay)
    pri_entry = list(priority)
    pri_entry.append(seq())  # ensure FIFO servicing of equal priorities
    pri_entry.append(LF)

    now = time.time()
    if delay is not None:
      when = now + delay
    if when is None or when <= now:
      # queue the request now
      self.debug("queuing %s", LF)
      heappush(self.pending, pri_entry)
      self._try_dispatch()
    else:
      # queue the request at a later time
      def queueFunc():
        LF = pri_entry[-1]
        self.debug("queuing %s after delay", LF)
        heappush(self.pending, pri_entry)
        self._try_dispatch()

      with self._lock:
        if self._timerQ is None:
          self._timerQ = TimerQueue(
              name="<TimerQueue %s._timerQ>" % (self.name,)
          )
      self.debug("delay %s until %s", LF, when)
      self._timerQ.add(when, queueFunc)
    # record the function as outstanding and attach a notification
    # to remove it from the outstanding set on completion
    self.outstanding.add(LF)
    LF.notify(self.outstanding.remove)
    return LF

  def complete(self, outstanding=None, until_idle=False):
    ''' Generator which waits for outstanding functions to complete and yields them.

        Parameters:
        * `outstanding`: if not None, an iterable of `LateFunction`s;
          default `self.outstanding`.
        * `until_idle`: if true,
          continue until `self.outstanding` is empty.
          This requires the `outstanding` parameter to be `None`.
    '''
    if outstanding is not None:
      if until_idle:
        raise ValueError("outstanding is not None and until_idle is true")
      for LF in report(outstanding):
        yield LF
      return
    # outstanding is None: loop until self.outstanding is empty
    while True:
      outstanding = list(self.outstanding)
      if not outstanding:
        break
      for LF in self.complete(outstanding):
        yield LF
      if not until_idle:
        break

  def wait_outstanding(self, until_idle=False):
    ''' Wrapper for complete(), to collect and discard completed `LateFunction`s.
    '''
    for _ in self.complete(until_idle=until_idle):
      pass

  def defer(self, func, *a, **kw):
    ''' Queue the function `func` for later dispatch using the
        default priority with the specified arguments `*a` and `**kw`.
        Return the corresponding `LateFunction` for result collection.

        `func` may optionally be preceeded by one or both of:
        * a string specifying the function's descriptive name,
        * a mapping containing parameters for `priority`,
          `delay`, and `when`.

        Equivalent to:

            submit(functools.partial(func, *a, **kw), **params)
    '''
    if not self.submittable:
      raise RuntimeError("%s.defer(...) but not self.submittable" % (self,))
    return self._defer(func, *a, **kw)

  def _defer(self, func, *a, **kw):
    # snapshot the arguments as supplied
    # note; a shallow snapshot
    if a:
      a = list(a)
    if kw:
      kw = dict(kw)
    params = {}
    # pop off leading parameters before the function
    while not callable(func):
      if isinstance(func, str):
        params['name'] = func
      else:
        params.update(func)
      func = a.pop(0)
    if a or kw:
      func = partial(func, *a, **kw)
    LF = self._submit(func, **params)
    return LF

  def with_result_of(self, callable1, func, *a, **kw):
    ''' Defer `callable1`, then append its result to the arguments for
        `func` and defer `func`.
        Return the `LateFunction` for `func`.
    '''

    def then():
      LF1 = self.defer(callable1)
      return self.defer(func, *[a + [LF1.result]], **kw)

    return then()

  def after(self, LFs, R, func, *a, **kw):
    ''' Queue the function `func` for later dispatch after completion of `LFs`.
        Return a `Result` for collection of the result of `func`.

        This function will not be submitted until completion of
        the supplied `LateFunction`s `LFs`.
        If `R` is `None` a new `Result` is allocated to
        accept the function return value.
        After `func` completes, its return value is passed to `R.put()`.

        Typical use case is as follows: suppose you're submitting
        work via this `Later` object, and a submitted function itself
        might submit more `LateFunction`s for which it must wait.
        Code like this:

              def f():
                LF = L.defer(something)
                return LF()

        may deadlock if the Later is at capacity. The `after()` method
        addresses this:

              def f():
                LF1 = L.defer(something)
                LF2 = L.defer(somethingelse)
                R = L.after( [LF1, LF2], None, when_done )
                return R

        This submits the `when_done()` function after the LFs have
        completed without spawning a thread or using the `Later`'s
        capacity.

        See the retry method for a convenience method that uses the
        above pattern in a repeating style.
    '''
    if not self.submittable:
      raise RuntimeError("%s.after(...) but not self.submittable" % (self,))
    return self._after(LFs, R, func, *a, **kw)

  def _after(self, LFs, R, func, *a, **kw):
    if not isinstance(LFs, list):
      LFs = list(LFs)
    if R is None:
      R = Result("Later.after(%s)" % (",".join(str(_) for _ in LFs)))
    elif not isinstance(R, Result):
      raise TypeError(
          "Later.after(LFs, R, func, ...): expected Result for R, got %r" %
          (R,)
      )

    def put_func():
      ''' Function to defer: run `func` and pass its return value to R.put().
      '''
      R.call(func, *a, **kw)

    put_func.__name__ = "%s._after(%r)[func=%s]" % (self, LFs, funcname(func))
    return after(LFs, None, lambda: self._defer(put_func))

  def defer_iterable(self, it, outQ, test_ready=None):
    ''' Submit an iterable `it` for asynchronous stepwise iteration
        to return results via the queue `outQ`.
        Return a `Result` for final synchronisation.

        Parameters:
        * `it`: the iterable for for asynchronous stepwise iteration
        * `outQ`: an `IterableQueue`like object
          with a `.put` method to accept items
          and a `.close` method to indicate the end of items.
          When the iteration is complete,
          call `outQ.close()` and complete the `Result`.
          If iteration ran to completion then the `Result`'s `.result`
          will be the number of iterations, otherwise if an iteration
          raised an exception the the `Result`'s .exc_info will contain
          the exception information.
        * `test_ready`: if not `None`, a callable to test if iteration
          is presently permitted; iteration will be deferred until
          the callable returns a true value.
    '''
    if not self.submittable:
      raise RuntimeError(
          "%s.defer_iterable(...) but not self.submittable" % (self,)
      )
    return self._defer_iterable(it, outQ=outQ, test_ready=test_ready)

  def _defer_iterable(self, it, outQ, test_ready=None):
    iterate = partial(next, iter(it))
    R = Result()
    iterationss = [0]

    @logexc
    def iterate_once():
      ''' Call `iterate`. Place the result on outQ.

          Close the queue at end of iteration or other exception.
          Otherwise, requeue ourself to collect the next iteration value.
      '''
      if test_ready is not None and not test_ready():
        raise RetryError("iterate_once: not ready yet")
      try:
        item = iterate()
      except StopIteration:
        outQ.close()
        R.result = iterationss[0]
      except Exception as e:  # pylint: disable=broad-except
        exception(
            "defer_iterable: iterate_once: exception during iteration: %s", e
        )
        outQ.close()
        R.exc_info = sys.exc_info()
      else:
        iterationss[0] += 1
        # put the item onto the output queue
        # this may itself defer various tasks (eg in a pipeline)
        debug("L.defer_iterable: iterate_once: %s.put(%r)", outQ, item)
        outQ.put(item)
        # now queue another iteration to run after those defered tasks
        self._defer(iterate_once)

    iterate_once.__name__ = "%s:next(iter(%s))" % (
        funcname(iterate_once), getattr(it, '__name__', repr(it))
    )
    self._defer(iterate_once)
    return R

  @contextmanager
  def priority(self, pri):
    ''' A context manager to temporarily set the default priority.

        Example:

            L = Later(4)
            with L.priority(1):
              L.defer(f)  # queue f() with priority 1
            with L.priority(2):
              L.defer(f, 3)  # queue f(3) with priority 2

        WARNING: this is NOT thread safe!

        TODO: is a thread safe version even a sane idea without a
              per-thread priority stack?
    '''
    oldpri = self._priority
    self._priority = pri
    try:
      yield
    finally:
      self._priority = oldpri

  def pool(self, *a, **kw):
    ''' Return a `LatePool` to manage some tasks run with this `Later`.
    '''
    return LatePool(L=self, *a, **kw)

class SubLater(object):
  ''' A class for managing a group of deferred tasks using an existing `Later`.
  '''

  def __init__(self, L):
    ''' Initialise the `SubLater` with its parent `Later`.

        TODO: accept `discard=False` param to suppress the queue and
        associated checks.
    '''
    self._later = L
    self._later.open()
    self._lock = Lock()
    self._deferred = 0
    self._queued = 0
    self._queue = IterableQueue()
    self.closed = False

  def __str__(self):
    return "%s(%s%s,deferred=%d,completed=%d)" % (
        type(self),
        self._later,
        "[CLOSED]" if self.closed else "",
        self._deferred,
        self._queued,
    )

  def __iter__(self):
    ''' Iteration over the `SubLater`
        iterates over the queue of completed `LateFUnction`s.
    '''
    return iter(self._queue)

  def close(self):
    ''' Close the SubLater.

        This prevents further deferrals.
    '''
    with self._lock:
      closed = self.closed
      if closed:
        self._later.warning("repeated close of %s", self)
      else:
        self.closed = True
        self._queue.close()
        self._later.close()

  def defer(self, func, *a, **kw):
    ''' Defer a function, return its `LateFunction`.

        The resulting `LateFunction` will queue itself for collection
        on completion.
    '''
    with self._lock:
      LF = self._later.defer(func, *a, **kw)
      self._deferred += 1

      def on_complete(R):
        with self._lock:
          self._queue.put(R)
          self._queued += 1
          if self.closed and self._queued >= self._deferred:
            self._queue.close()

    LF.notify(on_complete)
    return LF

  def reaper(self, handler=None):
    ''' Dispatch a `Thread` to collect completed `LateFunction`s.
        Return the `Thread`.

        `handler`: an optional callable to be passed each `LateFunction`
        as it completes.
    '''

    @logexc
    def reap(Q):
      for LF in Q:
        if handler:
          try:
            handler(LF)
          except Exception as e:  # pylint: disable=broad-except
            exception("%s: reap %s: %s", self, LF, e)

    T = Thread(name="reaper(%s)" % (self,), target=reap, args=(self._queue,))
    T.start()
    return T

class LatePool(object):
  ''' A context manager after the style of subprocess.Pool
      but with deferred completion.

      Example usage:

          L = Later(4)    # a 4 thread Later
          with LatePool(L) as LP:
            # several calls to LatePool.defer, perhaps looped
            LP.defer(func, *args, **kwargs)
            LP.defer(func, *args, **kwargs)
          # now we can LP.join() to block for all `LateFunctions`
          #
          # or iterate over LP to collect `LateFunction`s as they complete
          for LF in LP:
            result = LF()
            print(result)
  '''

  # pylint: disable=too-many-arguments
  def __init__(
      self,
      L=None,
      priority=None,
      delay=None,
      when=None,
      pfx=None,
      block=False
  ):
    ''' Initialise the `LatePool`.

        Parameters:
        * `L`: `Later` instance, default from default.current.
        * `priority`, `delay`, `when`, `name`, `pfx`:
          default values passed to Later.submit.
        * `block`: if true, wait for `LateFunction` completion
          before leaving __exit__.
    '''
    if L is None:
      L = default.current
    self.later = L
    self.parameters = {
        'priority': priority,
        'delay': delay,
        'when': when,
        'pfx': pfx,
    }
    self.block = block
    self.LFs = []

  def __enter__(self):
    ''' Entry handler: submit a placeholder function to the queue,
        acquire the "commence" lock, which will be made available
        when the placeholder gets to run.
    '''
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    ''' Exit handler.
        If .block is true, wait for `LateFunction` completion before return.
    '''
    if self.block:
      self.join()
    return False

  def add(self, LF):
    ''' Add a `LateFunction` to those to be tracked by this LatePool.
    '''
    self.LFs.append(LF)

  def submit(self, func, **params):
    ''' Submit a function using the LatePool's default parameters, overridden by `params`.
    '''
    submit_params = dict(self.parameters)
    submit_params.update(params)
    LF = self.later.submit(func, **submit_params)
    self.add(LF)
    return LF

  def defer(self, func, *a, **kw):
    ''' Defer a function using the LatePool's default parameters.
    '''
    if a or kw:
      func = partial(func, *a, **kw)
    return self.submit(func)

  def __iter__(self):
    ''' Report completion of the `LateFunction`s.
    '''
    for LF in report(self.LFs):
      yield LF

  def join(self):
    ''' Wait for completion of all the `LateFunction`s.
    '''
    for _ in self:
      pass

if __name__ == '__main__':
  import cs.later_tests
  cs.later_tests.selftest(sys.argv)
