#!/usr/bin/python
#

r'''
Queue functions for execution later in priority and time order.

I use Later objects for convenient queuing of functions whose execution occurs later in a priority order with capacity constraints.

Why not futures?
I already had this before futures came out,
I prefer its naming scheme and interface,
and futures did not seem to support prioritising execution.

Use is simple enough: create a Later instance and typically queue functions with the .defer() method::

  L = Later(4)      # a Later with a parallelism of 4
  ...
  LF = L.defer(func, *args, **kwargs)
  ...
  x = LF()          # collect result

The .defer method and its siblings return a LateFunction,
which is a subclass of cs.result.Result.
As such it is a callable, so to collect the result you just call the LateFunction.
'''

from __future__ import print_function
from contextlib import contextmanager
from functools import partial
import sys
import threading
import time
import traceback
from cs.debug import ifdebug, Lock, Thread
from cs.excutils import logexc, logexc_gen
from cs.logutils import error, warning, debug, exception, D, OBSOLETE
from cs.pfx import Pfx, PrePfx, XP
from cs.py.func import funcname
from cs.queues import IterableQueue, IterablePriorityQueue, PushQueue, \
                        MultiOpenMixin, TimerQueue
from cs.result import Result, _PendingFunction, AsynchState, report, after
from cs.seq import seq, TrackingCounter
from cs.threads import AdjustableSemaphore, WorkerThreadPool, bg

DISTINFO = {
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
        'cs.pfx',
        'cs.py.func',
        'cs.queues',
        'cs.result',
        'cs.seq',
        'cs.threads',
    ],
}

# function signature designators, used with Later.pipeline()
FUNC_ONE_TO_MANY = 0
FUNC_ONE_TO_ONE = 1
FUNC_SELECTOR = 2
FUNC_MANY_TO_MANY = 3

DEFAULT_RETRY_DELAY = 0.1

class _ThreadLocal(threading.local):
  ''' Thread local state to provide implied context within Later context managers.
  '''

  def __init__(self):
    self.stack = []

  @property
  def current(self):
    return self.stack[-1]

  def push(self, L):
    self.stack.append(L)

  def pop(self):
    return self.stack.pop()

default = _ThreadLocal()

def later(func, *a, **kw):
  ''' Queue a function using the current default Later.
      Return the LateFunction.
  '''
  return default.current.defer(func, *a, **kw)

class RetryError(Exception):
  ''' Exception raised by functions which should be resubmitted to the queue.
  '''
  pass

def retry(retry_interval, func, *a, **kw):
  ''' Call the callable `func` with the supplied arguments.
      If it raises RetryError, sleep(`retry_interval`) and call
      again until it does not raise RetryError.
  '''
  while True:
    try:
      return func(*a, **kw)
    except RetryError as e:
      time.sleep(retry_interval)

class _Late_context_manager(object):
  ''' The _Late_context_manager is a context manager to run a suite via an
      existing Later object. Example usage:

        L = Later(4)    # a 4 thread Later
        ...
        with L.ready( ... optional Later.submit() args ... ):
          ... do stuff when L queues us ...

      This permits easy inline scheduled code.
  '''

  def __init__(self, L, priority=None, delay=None, when=None, name=None, pfx=None):
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
      ''' This is the placeholder function dispatched by the Later instance.
          It releases the "commence" lock for __enter__ to acquire,
          permitting to with-suite to commence.
          It then blocks waiting to acquire the "completed" lock;
          __exit__ releases that lock permitting the placeholder to return
          and release the Later resource.
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
    lf_ret, lf_exc_info = W
    if lf_exc_info is not None:
      raise lf_exc_info[1]
    return True

class LateFunction(_PendingFunction):
  ''' State information about a pending function.
      A LateFunction is callable, so a synchronous call can be done like this:

        def func():
          return 3
        L = Later(4)
        LF = L.defer()
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

      TODO: .cancel()
            timeout for wait()
  '''

  def __init__(self, later, func, name=None, final=None, retry_delay=None):
    ''' Initialise a LateFunction.
        `later` is the controlling Later instance.
        `func` is the callable for later execution.
        `name`, if supplied, specifies an identifying name for the LateFunction.
        `retry_local`: time delay before retry of this function on RetryError.
            Default from `later.retry_delay`.
    '''
    _PendingFunction.__init__(self, func, final=final)
    if name is None:
      name = "LF-%d[func=%s]" % ( seq(), funcname(func) )
    if retry_delay is None:
      retry_delay = later.retry_delay
    self.name = name
    self.retry_delay = retry_delay
    self.later = L = later.open()
    L._busy.inc(name)
    ##D("NEW LATEFUNCTION %r - busy ==> %d", name, L._busy.value)
    ##for sn, s in ('running', L.running), ('pending', L.pending):
    ##  D("    %s=%r", sn, s)

  def __str__(self):
    return "<LateFunction %s>" % (self.name,)

  def _complete(self, result, exc_info):
    ''' Record the completion result of this LateFunction and update the parent Later.
    '''
    _PendingFunction._complete(self, result, exc_info)
    self.later._completed(self, result, exc_info)
    self.later._busy.dec(self.name)
    self.later.close()

  def _resubmit(self):
    ''' Resubmit this function for later execution.
    '''
    self.later._submit(self.func, delay=self.retry_delay, name=self.name, LF=self)

  def _dispatch(self):
    ''' ._dispatch() is called by the Later class instance's worker thread.
        It causes the function to be handed to a thread for execution.
    '''
    L = self.later
    L.debug("DISPATCH %s", self)
    with self._lock:
      if not self.pending:
        raise RuntimeError("should be pending, but state = %s", self.state)
      self.state = AsynchState.running
      L._workers.dispatch(self.func, deliver=self._worker_complete, daemon=True)

  @OBSOLETE
  def wait(self):
    return self.join()

  def _worker_complete(self, work_result):
    ''' Accept the result of the queued function as returned from the work queue.
        If the function raised RetryError, requeue the function for later.
        Otherwise record completion as normal.
        If the function raised one of NameError, AttributeError, RuntimeError
        (broadly: "programmer errors"), report the stack trace to aid debugging.
    '''
    result, exc_info = work_result
    if exc_info:
      e = exc_info[1]
      if isinstance(e, RetryError):
        # resubmit this function
        warning("%s._worker_completed: resubmit after RetryError: %s", e)
        self._resubmit()
        return
      if isinstance(e, (NameError, AttributeError, RuntimeError)):
        warning("%s._worker_completed: exc_info=%s", self.name, exc_info)
        with Pfx('>>'):
          for formatted in traceback.format_exception(*exc_info):
            for line in formatted.rstrip().split('\n'):
              warning(line)
    self._complete(result, exc_info)

class _PipelinePushQueue(PushQueue):
  ''' A _PipelinePushQueue subclasses cs.queues.PushQueue, adding some item tracking.
      We raise the pipeline's _busy counter for every item in play,
      and also raise it while the finalisation function has not run.
      This lets us inspect a pipeline for business, which we use in the
      cs.app.pilfer termination process.
  '''

  def __init__(self, name, pipeline, func_iter, outQ, func_final=None, retry_interval=None):
    ''' Initialise the _PipelinePushQueue, wrapping func_iter and func_final in code to inc/dec the main pipeline _busy counter.
    '''
    if retry_interval is None:
      retry_interval = DEFAULT_RETRY_DELAY
    self.pipeline = pipeline
    self.retry_interval = retry_interval

    # wrap func_iter to raise _busy while processing item
    @logexc_gen
    def func_push(item):
      self.pipeline._busy.inc()
      try:
        for item2 in retry(self.retry_interval, func_iter, item):
          yield item2
      except Exception:
        self.pipeline._busy.dec()
        raise
      self.pipeline._busy.dec()
    func_push.__name__ = "busy(%s)" % (func_iter.__name__,)

    # if there is a func_final, raise _busy until func_final completed
    if func_final is not None:
      self.pipeline._busy.inc()
      func_final0 = func_final
      @logexc
      def func_final():
        try:
          result = retry(self.retry_interval, func_final0)
        except Exception:
          self.pipeline._busy.dec()
          raise
        self.pipeline._busy.dec()
        return result
      func_final.__name__ = "pipeline_dec_busy(%s)" % (func_final0.__name__,)

    PushQueue.__init__(self, name, self.pipeline.later, func_push, outQ, func_final=func_final)

  def __str__(self):
    return "%s[%s]" % (PushQueue.__str__(self), self.pipeline)

class _Pipeline(MultiOpenMixin):
  ''' A _Pipeline encapsulates the chain of PushQueues created by a call to Later.pipeline.
  '''

  def __init__(self, name, L, filter_funcs, outQ):
    ''' Initialise the _Pipeline from `name`, Later instance `L`, list  of filter functions `filter_funcs` and output queue `outQ`.
    '''
    MultiOpenMixin.__init__(self)
    self.name = name
    self.later = L
    self.queues = [outQ]
    # counter tracking items in play
    self._busy = TrackingCounter(name="Pipeline<%s>._items" % (name,))
    RHQ = outQ
    count = len(filter_funcs)
    while filter_funcs:
      func_iter, func_final = self._pipeline_func(filter_funcs.pop())
      count -= 1
      pq_name = ":".join(
          ( name,
              "%s/%s" % (
                  (funcname(func_iter) if func_iter else "None"),
                  (funcname(func_final) if func_final else "None"),
              ),
              str(count),
              str(seq()),
          )
      )
      PQ = _PipelinePushQueue(pq_name, self, func_iter, RHQ, func_final=func_final).open()
      self.queues.insert(0, PQ)
      RHQ = PQ

  def __str__(self):
    return "cs.later._Pipeline:%s" % (self.name,)

  def __repr__(self):
    return "<%s %d queues, later=%s>" % (self, len(self.queues), self.later)

  def put(self, item):
    ''' Put an `item` onto the leftmost queue in the pipeline.
    '''
    return self.inQ.put(item)

  @property
  def inQ(self):
    ''' Property returning the leftmost queue in the pipeline, the input queue.
    '''
    return self.queues[0]

  @property
  def outQ(self):
    ''' Property returning the rightmost queue in the pipeline, the output queue.
    '''
    return self.queues[-1]

  def startup(self):
    pass

  def shutdown(self):
    ''' Close the leftmost queue in the pipeline.
    '''
    self.inQ.close(enforce_final_close=True)

  def join(self):
    ''' Wait for completion of the output queue.
    '''
    self.outQ.join()

  def _pipeline_func(self, o):
    ''' Accept a pipeline element. Return (func_iter, func_final).
        A pipeline element is either a single function, in which case it is
        presumed to be a one-to-many-generator with func_sig FUNC_ONE_TO_MANY,
        or a tuple of (func_sig, func).

        The returned func_iter and func_final take the following
        values according to the supplied func_sig:

          func_sig              func_iter, func_final

          FUNC_ONE_TO_MANY      func, None
                                Example: a directory listing.

          FUNC_SELECTOR         func is presumed to be a Boolean test, and
                                func_iter is a generator that yields its
                                argument if the test succeeds.
                                func_final is None.
                                Example: a test for inclusion.

          FUNC_MANY_TO_MANY     func_iter is set to save its argument to a
                                list and yield nothing. func_final applies
                                func to the list and yields the results.
                                Example: a sort.
    '''
    if callable(o):
      func = o
      func_sig = FUNC_ONE_TO_MANY
    else:
      # expect a tuple
      func_sig, func = o
    func_final = None
    if func_sig == FUNC_ONE_TO_ONE:
      def func_iter(item):
        yield retry(DEFAULT_RETRY_DELAY, func, item)
      func_iter.__name__ = "func_iter_1to1(func=%s)" % (funcname(func),)
    elif func_sig == FUNC_ONE_TO_MANY:
      func_iter = func
    elif func_sig == FUNC_SELECTOR:
      def func_iter(item):
        if retry(DEFAULT_RETRY_DELAY, func, item):
          yield item
      func_iter.__name__ = "func_iter_1toMany(func=%s)" % (funcname(func),)
    elif func_sig == FUNC_MANY_TO_MANY:
      gathered = []
      def func_iter(item):
        gathered.append(item)
        if False:
          yield
      func_iter.__name__ = "func_iter_gather(func=%s)" % (funcname(func),)
      def func_final():
        for item in retry(DEFAULT_RETRY_DELAY, func, gathered):
          yield item
      func_final.__name__ = "func_final_gather(func=%s)" % (funcname(func),)
    else:
      raise ValueError("unsupported function signature %r" % (func_sig,))
    return func_iter, func_final

class Later(MultiOpenMixin):
  ''' A management class to queue function calls for later execution.

      Methods are provided for submitting functions to run ASAP or
      after a delay or after other pending functions. These methods
      return LateFunctions, a subclass of cs.result.Asynchon.

      A Later instance' shutdown method closes the Later for further
      submission (except by functions run by this Later). Shutdown
      does not imply that all submitted functions have completed
      or even been dispatched. Callers may wait for completion and
      optionally cancel functions.
  '''

  def __init__(self, capacity, name=None, inboundCapacity=0, retry_delay=None):
    ''' Initialise the Later instance.
        If `capacity` is an int, it is used to size a Semaphore to constrain
        the number of dispatched functions which may be in play at a time.
        If `capacity` is not an int it is presumed to be a suitable
        Semaphore-like object.
        `inboundCapacity` can be specified to limit the number of undispatched
        functions that may be queued up; the default is 0 (no limit).
        Calls to submit functions when the inbound limit is reached block
        until some functions are dispatched.
        The `name` parameter may be used to supply an identifying name
        for this instance.
        `retry_delay`: time delay for requeued functions. Default
            from DEFAULT_RETRY_DELAY.
    '''
    if name is None:
      name = "Later-%d" % (seq(),)
    MultiOpenMixin.__init__(self)
    self._finished = threading.Event()
    if ifdebug():
      import inspect
      filename, lineno = inspect.stack()[1][1:3]
      name = "%s[%s:%d]" % (name, filename, lineno)
    debug("Later.__init__(capacity=%s, inboundCapacity=%s, name=%s)", capacity, inboundCapacity, name)
    if type(capacity) is int:
      capacity = AdjustableSemaphore(capacity)
    if retry_delay is None:
      retry_delay = DEFAULT_RETRY_DELAY
    self.capacity = capacity
    self.inboundCapacity = inboundCapacity
    self.retry_delay = retry_delay
    self.name = name
    self.outstanding = set()    # uncompleted LateFunctions
    self.delayed = set()        # unqueued, delayed until specific time
    self.pending = set()        # undispatched LateFunctions
    self.running = set()        # running LateFunctions
    # counter tracking jobs queued or active
    self._busy = TrackingCounter(name="Later<%s>._busy" % (name,), lock=self._lock)
    self._state = ""
    self.logger = None          # reporting; see logTo() method
    self._priority = (0,)
    self._timerQ = None         # queue for delayed requests; instantiated at need
    # inbound requests queue
    self._pendingq = IterablePriorityQueue(inboundCapacity, name="%s._pendingq" % (self.name,))
    self._workers = WorkerThreadPool(name=name + ":WorkerThreadPool").open()
    self._dispatchThread = Thread(
        name=self.name + '._dispatcher',
        target=self._dispatcher
    )
    self._dispatchThread.daemon = True
    self._dispatchThread.start()

  @property
  def finished(self):
    ''' Probe the finishedness.
    '''
    return self._finished.is_set()

  def wait(self):
    ''' Wait for the Later to be finished.
    '''
    self._finished.wait()

  def close(self, *a, **kw):
    with PrePfx("LATER.CLOSE %s", self):
      try:
        MultiOpenMixin.close(self, *a, **kw)
      except RuntimeError as e:
        XP("LATER NOT IDLE: %r", self)
        raise

  def __repr__(self):
    return (
        '<%s "%s" capacity=%s running=%d (%s) pending=%d (%s) delayed=%d busy=%d:%r closed=%s>'
        % (
            self.__class__.__name__, self.name,
            self.capacity,
            len(self.running), ','.join( repr(LF.name) for LF in self.running ),
            len(self.pending), ','.join( repr(LF.name) for LF in self.pending ),
            len(self.delayed),
            int(self._busy), self._busy,
            self.closed
        )
    )

  def __str__(self):
    return "<%s[%s] pending=%d running=%d delayed=%d busy=%d:%s opens=%d>" \
           % (self.name, self.capacity,
              len(self.pending), len(self.running), len(self.delayed),
              int(self._busy), self._busy,
              self._opens)

  def state(self, new_state, *a):
    if a:
      new_state = new_state % a
    D("STATE %r [%s]", new_state, self)
    self._state = new_state

  @MultiOpenMixin.is_opened
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

  def startup(self):
    pass

  def shutdown(self):
    ''' Shut down the Later instance:
        - close the TimerQueue if any
        - close the request queue
        - close the worker thread pool
    '''
    ##with Pfx("%s.shutdown()", self):
    with PrePfx("LATER.SHUTDOWN [%s]", self):
      if not self.closed:
        error("NOT CLOSED")
        raise RuntimeError("NOT CLOSED!")
      if self._timerQ:
        self._timerQ.close()
        self._timerQ.join()
      self._pendingq.close()          # prevent further submissions
      self._workers.close()
      # queue actions to detect activity completion
      def finish_up():
        self._dispatchThread.join()         # wait for all functions to be dispatched
        self._workers.join()                # wait for all worker Threads to complete
        self._finished.set()
      bg(finish_up)

  def _track(self, tag, LF, fromset, toset):
    def SN(s):
      if s is None:
        return "None"
      if s is self.delayed:
        return "delayed"
      if s is self.pending:
        return "pending"
      if s is self.running:
        return "running"
      return repr(s)
    debug("_track %s => %s: %s %s", SN(fromset), SN(toset), tag, LF.name)
    if not LF:
      raise ValueError("LF false! (%r)", LF)
    if fromset is None and toset is None:
      raise ValueError("fromset and toset are None")
    with self._lock:
      if fromset is not None:
        fromset.remove(LF)
      if toset is not None:
        toset.add(LF)

  def log_status(self):
    for LF in list(self.delayed):
      self.debug("STATUS: delayed: %s", LF)
    for LF in list(self.pending):
      self.debug("STATUS: pending: %s", LF)
    for LF in list(self.running):
      self.debug("STATUS: running: %s", LF)

  def _completed(self, LF, result, exc_info):
    self.debug("COMPLETE %s: result = %r, exc_info = %r", LF, result, exc_info)
    self.log_status()
    self.capacity.release()
    self._track("_completed(%s)" % (LF.name,), LF, self.running, None)

  def __enter__(self):
    global default
    debug("%s: __enter__", self)
    L = MultiOpenMixin.__enter__(self)
    default.push(L)
    return L

  def __exit__(self, exc_type, exc_val, exc_tb):
    ''' Exit handler: release the "complete" lock; the placeholder
        function is blocking on this, and will return on its release.
    '''
    global default
    debug("%s: __exit__: exc_type=%s", self, exc_type)
    MultiOpenMixin.__exit__(self, exc_type, exc_val, exc_tb)
    default.pop()
    return False

  @contextmanager
  def more_capacity(self, increment=1):
    self.capacity.adjust_delta(increment)
    yield
    self.capacity.adjust_delta(-increment)

  def logTo(self, filename, logger=None, log_level=None):
    ''' Log to the file specified by `filename` using the specified
        logger named `logger` (default the module name, cs.later) at the
        specified log level `log_level` (default logging.INFO).
    '''
    import logging
    import cs.logutils
    if logger is None:
      logger = self.__module__
    if log_level is None:
      log_level = logging.INFO
    logger, handler = cs.logutils.logTo(filename, logger=logger)
    handler.setFormatter(logging.Formatter("%(asctime)-15s %(later_name)s %(message)s"))
    logger.setLevel(log_level)
    self.logger = logger

  def error(self, *a, **kw):
    if self.logger:
      kw.setdefault('extra', {}).update(later_name=str(self))
      self.logger.error(*a, **kw)

  def warning(self, *a, **kw):
    if self.logger:
      kw.setdefault('extra', {}).update(later_name=str(self))
      self.logger.warning(*a, **kw)

  def info(self, *a, **kw):
    if self.logger:
      kw.setdefault('extra', {}).update(later_name=str(self))
      self.logger.info(*a, **kw)

  def debug(self, *a, **kw):
    if self.logger:
      kw.setdefault('extra', {}).update(later_name=str(self))
      self.logger.debug(*a, **kw)

  def __del__(self):
    if not self.closed:
      self.shutdown()

  def _dispatcher(self):
    ''' Read LateFunctions from the inbound queue as capacity is available
        and dispatch them. The LateFunction's ._dispatch() method will
        release the capacity on completion.
    '''
    while True:
      # will be released by the LateFunction
      self.capacity.acquire()
      try:
        pri_entry = self._pendingq.next()
      except StopIteration:
        # end of queue, not calling the handler
        self.capacity.release()
        break
      LF = pri_entry[-1]
      self._track("_dispatcher: dispatch", LF, self.pending, self.running)
      self.debug("dispatched %s", LF)
      LF._dispatch()

  @property
  def submittable(self):
    ''' May new tasks be submitted?
        This normally tracks "not self.closed", but running tasks
        are wrapped in a thread local override to permit them to
        submit further related tasks.
    '''
    return not self.closed

  @MultiOpenMixin.is_opened
  def bg(self, func, *a, **kw):
    ''' Queue a function to run right now, ignoring the Later's capacity and
        priority system. This is really just an easy way to utilise the Later's
        thread pool and get back a handy LateFunction for result collection.
        It can be useful for transient control functions that
        themselves queue things through the Later queuing system
        but do not want to consume capacity themselves, thus avoiding
        deadlock at the cost of transient overthreading.
    '''
    if not self.submittable:
      raise RuntimeError("%s.bg(...) but not self.submittable" % (self,))
    funcname = None
    if isinstance(func, str):
      funcname = func
      a = list(a)
      func = a.pop(0)
    if a or kw:
      func = partial(func, *a, **kw)
    LF = LateFunction(self, func, funcname)
    self._track("bg: dispatch", LF, None, self.running)
    LF._dispatch()
    return LF

  def ready(self, **kwargs):
    ''' Awful name.
        Return a context manager to block until the Later provides a timeslot.
    '''
    return _Late_context_manager(self, **kwargs)

  @MultiOpenMixin.is_opened
  def submit(self, func, priority=None, delay=None, when=None, name=None, pfx=None):
    ''' Submit the callable `func` for later dispatch.
        Return the corresponding LateFunction for result collection.
        If the parameter `priority` is not None then use it as the priority
        otherwise use the default priority.
        If the parameter `delay` is not None, delay consideration of
        this function until `delay` seconds from now.
        If the parameter `when` is not None, delay consideration of
        this function until the time `when`.
        It is an error to specify both `when` and `delay`.
        If the parameter `name` is not None, use it to name the LateFunction.
        If the parameter `pfx` is not None, submit pfx.partial(func);
          see the cs.logutils.Pfx.partial method for details.
        If the parameter `LF` is not None, construct a new LateFunction to
          track function completion.
    '''
    if not self.submittable:
      raise RuntimeError("%s.submit(...) but not self.submittable" % (self,))
    return self._submit(func, priority=priority, delay=delay, when=when, name=name, pfx=pfx)

  def _submit(self, func, priority=None, delay=None, when=None, name=None, pfx=None, LF=None, retry_delay=None):
    if delay is not None and when is not None:
      raise ValueError("you can't specify both delay= and when= (%s, %s)" % (delay, when))
    if priority is None:
      priority = self._priority
    elif type(priority) is int:
      priority = (priority,)
    if pfx is not None:
      func = pfx.partial(func)
    if LF is None:
      LF = LateFunction(self, func, name=name, retry_delay=retry_delay)
    pri_entry = list(priority)
    pri_entry.append(seq())     # ensure FIFO servicing of equal priorities
    pri_entry.append(LF)

    now = time.time()
    if delay is not None:
      when = now + delay
    if when is None or when <= now:
      # queue the request now
      self.debug("queuing %s", LF)
      self._track("_submit: _pendingq.put", LF, None, self.pending)
      self._pendingq.put( pri_entry )
    else:
      # queue the request at a later time
      def queueFunc():
        LF = pri_entry[-1]
        self.debug("queuing %s after delay", LF)
        self._track("_submit: _pendingq.put after delay", LF, self.delayed, self.running)
        self._pendingq.put( pri_entry )
      with self._lock:
        if self._timerQ is None:
          self._timerQ = TimerQueue(name="<TimerQueue %s._timerQ>" % (self.name,))
      self.debug("delay %s until %s", LF, when)
      self._track("_submit: delay", LF, None, self.delayed)
      self._timerQ.add(when, queueFunc)
    # record the function as outstanding and attach a notification
    # to remove it from the outstanding set on completion
    self.outstanding.add(LF)
    LF.notify(lambda LF: self.outstanding.remove(LF))
    return LF

  def complete(self, outstanding=None, until_idle=False):
    ''' Generator which waits for outstanding functions to complete and yields them.
        `outstanding`: if not None, an iterable of LateFunctions; default self.outstanding
        `until_idle`: if outstanding is not None, continue until self.outstanding is empty
    '''
    if outstanding is not None:
      if until_idle:
        raise ValueError("outstanding is not None and until_idle is not false")
      for LF in report(outstanding):
        yield LF
      return
    while True:
      outstanding = list(self.outstanding)
      if not outstanding:
        break
      for LF in self.complete(outstanding):
        yield LF
      if not until_idle:
        break

  def wait_outstanding(self, until_idle=False):
    ''' Wrapper for complete(), to collect and discard completed LateFunctions.
    '''
    for LF in self.complete(until_idle=until_idle):
      pass

  ##@trace_caller
  @MultiOpenMixin.is_opened
  def defer(self, func, *a, **kw):
    ''' Queue the function `func` for later dispatch using the
        default priority with the specified arguments `*a` and `**kw`.
        Return the corresponding LateFunction for result collection.
        `func` may optionally be preceeded by one or both of:
          a string specifying the function's descriptive name,
          a mapping containing parameters for `priority`,
            `delay`, and `when`.
        Equivalent to:
          submit(functools.partial(func, *a, **kw), **params)
    '''
    if not self.submittable:
      raise RuntimeError("%s.defer(...) but not self.submittable" % (self,))
    return self._defer(func, *a, **kw)

  def _defer(self, func, *a, **kw):
    if a:
      a = list(a)
    params = {}
    while not callable(func):
      if isinstance(func, str):
        params['name'] = func
        func = a.pop(0)
      else:
        params.update(func)
        func = a.pop(0)
    if a or kw:
      func = partial(func, *a, **kw)
    LF = self._submit(func, **params)
    return LF

  def with_result_of(self, callable1, func, *a, **kw):
    ''' Defer `callable1`, then add its result to the arguments for `func` and defer that. Return the LateFunction for `func`.
    '''
    def then():
      LF1 = self.defer(callable1)
      return self.defer(func, *[a + [LF1.result]])
    return then()

  @MultiOpenMixin.is_opened
  def after(self, LFs, R, func, *a, **kw):
    ''' Queue the function `func` for later dispatch after completion of `LFs`.
        Return a Result for collection of the result of `func`.

        This function will not be submitted until completion of
        the supplied LateFunctions `LFs`.
        If `R` is None a new cs.threads.Result is allocated to
        accept the function return value.
            After `func` completes, its return value is passed to R.put().

        Typical use case is as follows: suppose you're submitting
        work via this Later object, and a submitted function itself
        might submit more LateFunctions for which it must wait.
        Code like this:

              def f():
                LF = L.defer(something)
                return LF()

        may deadlock if the Later is at capacity. The after() method
        addresses this:

              def f():
                LF1 = L.defer(something)
                LF2 = L.defer(somethingelse)
                R = L.after( [LF1, LF2], None, when_done )
                return R

        This submits the when_done() function after the LFs have
        completed without spawning a thread or using the Later's
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
      raise TypeError("Later.after(LFs, R, func, ...): expected Result for R, got %r" % (R,))
    def put_func():
      ''' Function to defer: run `func` and pass its return value to R.put().
      '''
      R.call(func, *a, **kw)
    put_func.__name__ = "%s._after(%r)[func=%s]" % (self, LFs, funcname(func))
    def submit_func():
      self._defer(put_func)
      L.close()
      self._busy.dec("Later._after")
    self._busy.inc("Later._after")
    L = self.open()
    return after(LFs, None, submit_func)

  @MultiOpenMixin.is_opened
  def defer_iterable(self, I, outQ, test_ready=None):
    ''' Submit an iterable `I` for asynchronous stepwise iteration
        to return results via the queue `outQ`.
        `outQ` must have a .put method to accept items and a .close method to
        indicate the end of items.
        When the iteration is complete, call outQ.close().
        `test_ready`: if not None, a callable to test if iteration
            is presently permitted; iteration will be deferred until
            the callable returns a true value
    '''
    if not self.submittable:
      raise RuntimeError("%s.defer_iterable(...) but not self.submittable" % (self,))
    return self._defer_iterable(I, outQ=outQ, test_ready=test_ready)

  def _defer_iterable(self, I, outQ, test_ready=None):
    iterate = partial(next, iter(I))

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
      except Exception as e:
        exception("defer_iterable: iterate_once: exception during iteration: %s", e)
        outQ.close()
      else:
        # put the item onto the output queue
        # this may itself defer various tasks (eg in a pipeline)
        debug("L.defer_iterable: iterate_once: %s.put(%r)", outQ, item)
        outQ.put(item)
        # now queue another iteration to run after those defered tasks
        self._defer(iterate_once)

    iterate_once.__name__ = "%s:next(iter(%s))" % (funcname(iterate_once),
                                                   getattr(I, '__name__', repr(I)))
    self._defer(iterate_once)

  @MultiOpenMixin.is_opened
  def pipeline(self, filter_funcs, inputs=None, outQ=None, name=None):
    ''' Construct a function pipeline to be mediated by this Later queue.
        Return:
          input, output
        where `input`` is a closeable queue on which more data items can be put
        and `output` is an iterable from which result can be collected.

        `filter_funcs`: an iterable of filter functions accepting the
          single items from the iterable `inputs`, returning an
          iterable output.
        `inputs`: the initial iterable inputs; this may be None.
          If missing or None, it is expected that the caller will
          be supplying input items via `input.put()`.
        `outQ`: the optional output queue; if None, an IterableQueue() will be
          allocated.
        `name`: name for the PushQueue implementing this pipeline.

        If `inputs` is None or `open` is true, the returned `input` requires
        a call to `input.close()` when no further inputs are to be supplied.

        Example use with presupplied Later `L`:

          input, output = L.pipeline(
                  [
                    ls,
                    filter_ls,
                    ( FUNC_MANY_TO_MANY, lambda items: sorted(list(items)) ),
                  ],
                  ('.', '..', '../..'),
                 )
          for item in output:
            print(item)
    '''
    if not self.submittable:
      raise RuntimeError("%s.pipeline(...) but not self.submittable" % (self,))
    return self._pipeline(filter_funcs, inputs, outQ=outQ, name=name)

  def _pipeline(self, filter_funcs, inputs=None, outQ=None, name=None):
    filter_funcs = list(filter_funcs)
    if not filter_funcs:
      raise ValueError("no filter_funcs")
    if outQ is None:
      outQ = IterableQueue(name="pipelineIQ")
    if name is None:
      name = "pipelinePQ"
    pipeline = _Pipeline(name, self, filter_funcs, outQ)
    inQ = pipeline.inQ
    if inputs is not None:
      self._defer_iterable( inputs, inQ )
    else:
      debug("%s._pipeline: no inputs, NOT setting up _defer_iterable( inputs, inQ=%r)", self, inQ)
    return pipeline

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
    yield
    self._priority = oldpri

  def pool(self, *a, **kw):
    ''' Return a LatePool to manage some tasks run with this Later.
    '''
    return LatePool(L=self, *a, **kw)

class LatePool(object):
  ''' A context manager after the style of subprocess.Pool but with deferred completion.
      Example usage:

        L = Later(4)    # a 4 thread Later
        with LatePool(L) as LP:
          # several calls to LatePool.defer, perhaps looped
          LP.defer(func, *args, **kwargs)
          LP.defer(func, *args, **kwargs)
        # now we can LP.join() to block for all LateFunctions
        #
        # or iterate over LP to collect LateFunctions as they complete
        for LF in LP:
          result = LF()
          print(result)
  '''

  def __init__(self, L=None, priority=None, delay=None, when=None, pfx=None, block=False):
    ''' Initialise the LatePool.
        `L`: Later instance, default from default.current.
        `priority`, `delay`, `when`, `name`, `pfx`: default values passed to Later.submit.
        `block`: if true, wait for LateFunction completion before leaving __exit__.
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
        If .block is true, wait for LateFunction completion before return.
    '''
    if self.block:
      self.join()
    return False

  def add(self, LF):
    ''' Add a LateFunction to those to be tracked by this LatePool.
    '''
    self.LFs.append(LF)

  def submit(self, func, **params):
    ''' Submit a function using the LatePool's default paramaters, overridden by `params`.
    '''
    submit_params = dict(self.parameters)
    submit_params.update(params)
    LF = self.L.submit(func, **submit_params)
    self.add(LF)
    return LF

  def defer(self, func, *a, **kw):
    ''' Defer a function using the LatePool's default paramaters.
    '''
    if a or kw:
      func = partial(func, *a, **kw)
    return self.submit(func)

  def __iter__(self):
    ''' Report completion of the LateFunctions.
    '''
    for LF in report(self.LFs):
      yield LF

  def join(self):
    ''' Wait for completion of all the LateFunctions.
    '''
    for LF in self:
      pass

def capacity(func):
  ''' Decorator for functions which wish to manage concurrent requests.
      The caller must provide a `capacity` keyword arguments which
      is either a Later instance or an int; if an int a Later with
      that capacity will be made.
      The Later will be passed into the inner function as the
      `capacity` keyword argument.
  '''
  def with_capacity(*a, **kw):
    ''' Wrapper function provide a Later for resource control.
    '''
    L = kw.pop('capacity')
    if isinstance(L, int):
      L = Later(L)
    kw['capacity'] = L
    return func(*a, **kw)
  return with_capacity

if __name__ == '__main__':
  import cs.later_tests
  cs.later_tests.selftest(sys.argv)
