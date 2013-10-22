#!/usr/bin/python
#

from __future__ import print_function
from contextlib import contextmanager
from functools import partial
import sys
from collections import deque
import threading
import traceback
from cs.py3 import Queue, raise3
import time
from cs.debug import ifdebug, Lock, RLock, Thread, trace_caller
from cs.queues import IterableQueue, IterablePriorityQueue, PushQueue
from cs.threads import AdjustableSemaphore, \
                       WorkerThreadPool, TimerQueue
from cs.asynchron import Result, Asynchron, ASYNCH_RUNNING
from cs.seq import seq
from cs.logutils import Pfx, error, info, warning, debug, D, OBSOLETE

# function signature designators, used with Later.pipeline()
FUNC_ONE_TO_MANY = 0
FUNC_ONE_TO_ONE = 1
FUNC_SELECTOR = 2
FUNC_MANY_TO_MANY = 3

class _ThreadLocal(threading.local):
  ''' Thread local state to provide implied context withing Later context managers.
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
    self.parameters = { 'priority': priority,
                        'delay': delay,
                        'when': when,
                        'name': name,
                        'pfx': pfx,
                      }
    self.commence = Lock()
    self.commence.acquire()
    self.complete = Lock()
    self.commence.acquire()

  def __enter__(self):
    ''' Entry handler: submit a placeholder function to the queue,
        acquire the "commence" lock, which will be made available
        when the placeholder gets to run.
    '''

    def run():
      ''' This is the placeholder function dispatcher by the Later instance.
          It releases the "commence" lock for __enter__ to acquire,
          permitting to with-suite to commence.
          It then blocks waiting to acquire the "complete" lock;
          __exit__ releases that lock permitting the placeholder to return
          and release the Later resource.
      '''
      self.commence.release()
      self.complete.acquire()
      return "run done"

    # queue the placeholder function and wait for it to execute
    self.latefunc = self.later.submit(run, **self.parameters)
    self.commence.acquire()
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    ''' Exit handler: release the "complete" lock; the placeholder
        function is blocking on this, and will return on its release.
    '''
    self.complete.release()
    if exc_type is not None:
      return False
    W = self.latefunc.wait()
    self.latefunc = None
    lf_ret, lf_exc_info = W
    if lf_exc_info is not None:
      raise lf_exc_type(lf_exc_info)
    return True

class PendingFunction(Asynchron):

  def __init__(self, func, *a, **kw):
    Asynchron.__init__(self)
    if a or kw:
      func = partial(func, *a, **kw)
    self.func = func

class OnDemandFunction(PendingFunction):
  ''' Wrap a callable, call it later.
      Like a LateFunction, you can call the wrapper many times; the
      inner function will only run once.
  '''

  def __call__(self):
    with self._lock:
      state = self.state
      if state == ASYNC_CANCELLED:
        raise CancellationError()
      if state == ASYNCH_PENDING:
        self.state = ASYNCH_RUNNING
      else:
        raise RuntimeError("state should be ASYNCH_PENDING but is %s" % (self.state))
    result, exc_info = None, None
    try:
      result = self.func()
    except:
      exc_info = sys.exc_info()
      self.exc_info = exc_info
      raise
    else:
      self.result = result
    return result

class LateFunction(PendingFunction):
  ''' State information about a pending function.
      A LateFunction is callable, so a synchronous call can be done like this:

        def func():
          return 3
        L = Later(4)
        LF = L.defer()
        x = LF()
        print x         # prints 3

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

  def __init__(self, later, func, name=None):
    ''' Initialise a LateFunction.
        `later` is the controlling Later instance.
        `func` is the callable for later execution.
        `name`, if supplied, specifies an identifying name for the LateFunction.
    '''
    PendingFunction.__init__(self, func)
    if name is None:
      name = "LateFunction-%d" % (seq(),)
    self.name = name
    self.later = later

  def __str__(self):
    return "<LateFunction %s>" % (self.name,)

  def _dispatch(self):
    ''' ._dispatch() is called by the Later class instance's worker thread.
        It causes the function to be handed to a thread for execution.
    '''
    self.later.debug("DISPATCH %s", self)
    with self._lock:
      if not self.pending:
        raise RuntimeError("should be pending, but state = %s", self.state)
      self.state = ASYNCH_RUNNING
      self.later._workers.dispatch(self.func, deliver=self._worker_complete)
      self.func = None

  @OBSOLETE
  def wait(self):
    return self.join()

  def _worker_complete(self, work_result):
    result, exc_info = work_result
    if exc_info:
      if isinstance(exc_info[1], (NameError, AttributeError, RuntimeError)):
        warning("LateFunction<%s>._worker_completed: exc_info=%s", self.name, exc_info[1])
        with Pfx('>>'):
          for formatted in traceback.format_exception(*exc_info):
            for line in formatted.rstrip().split('\n'):
              warning(line)
    self._complete(result, exc_info)

  def _complete(self, result, exc_info):
    PendingFunction._complete(self, result, exc_info)
    self.later._completed(self, result, exc_info)

class Later(object):
  ''' A management class to queue function calls for later execution.
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
  '''
  def __init__(self, capacity, inboundCapacity=0, name=None):
    if name is None:
      name = "Later-%d" % (seq(),)
    if ifdebug():
      import inspect
      filename, lineno = inspect.stack()[1][1:3]
      name = "%s[%s:%d]" % (name, filename, lineno)
    debug("Later.__init__(capacity=%s, inboundCapacity=%s, name=%s)", capacity, inboundCapacity, name)
    if type(capacity) is int:
      capacity = AdjustableSemaphore(capacity)
    self.capacity = capacity
    self.inboundCapacity = inboundCapacity
    self.name = name
    self.delayed = set()        # unqueued, delayed until specific time
    self.pending = set()        # undispatched LateFunctions
    self.running = set()        # running LateFunctions
    self.logger = None          # reporting; see logTo() method
    self.closed = False
    self._priority = (0,)
    self._timerQ = None         # queue for delayed requests; instantiated at need
    # inbound requests queue
    self._LFPQ = IterablePriorityQueue(inboundCapacity, name="%s._LFPQ" % (self.name,))
    self._LFPQ.open()
    self._workers = WorkerThreadPool(name=name+":WorkerThreadPool", open=True)
    self._dispatchThread = Thread(name=self.name+'._dispatcher', target=self._dispatcher)
    self._lock = Lock()
    self._dispatchThread.start()

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

  ##@trace_caller
  def close(self):
    ''' Shut down the Later instance:
        - close the TimerQueue, if any, and wait for it to complete
        - close the request queue
        - wait for the job dispatcher to finish
	- close the worker thread pool, which waits for any of its
          outstanding threads to complete
    '''
    with Pfx("%s.close()" % (self,)):
      if self.closed:
        warning("close of closed Later %r", self)
      else:
        self.closed = True
        if self._timerQ:
          self._timerQ.close()
          self._timerQ.join()
        self._LFPQ.close()              # prevent further submissions
        self._dispatchThread.join()     # wait for all functions to be dispatched
        self._workers.close()           # wait for all worker threads to complete

  ## TODO
  def idle(self):
    ''' Wait for all active and pending jobs to complete, including
        any jobs they may themselves queue.
    '''
    raise RuntimeError("UNIMPLEMENTED")

  def __repr__(self):
    return '<%s "%s" capacity=%s running=%d (%s) pending=%d (%s) delayed=%d closed=%s>' \
           % ( self.__class__.__name__, self.name,
               self.capacity,
               len(self.running), ','.join( repr(LF.name) for LF in self.running ),
               len(self.pending), ','.join( repr(LF.name) for LF in self.pending ),
               len(self.delayed),
               self.closed
             )

  def __str__(self):
    return "<%s[%s] pending=%d running=%d delayed=%d>" \
           % (self.name, self.capacity,
              len(self.pending), len(self.running), len(self.delayed))

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
    self.running.remove(LF)

  def __enter__(self):
    debug("%s: __enter__", self)
    global default
    default.push(self)
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    ''' Exit handler: release the "complete" lock; the placeholder
        function is blocking on this, and will return on its release.
    '''
    debug("%s: __exit__: exc_type=%s", self, exc_type)
    global default
    default.pop()
    self.close()
    return False

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
      kw.setdefault('extra', {}).update(later_name = str(self))
      self.logger.error(*a, **kw)

  def warning(self, *a, **kw):
    if self.logger:
      kw.setdefault('extra', {}).update(later_name = str(self))
      self.logger.warning(*a, **kw)

  def info(self, *a, **kw):
    if self.logger:
      kw.setdefault('extra', {}).update(later_name = str(self))
      self.logger.info(*a, **kw)

  def debug(self, *a, **kw):
    if self.logger:
      kw.setdefault('extra', {}).update(later_name = str(self))
      self.logger.debug(*a, **kw)

  def __del__(self):
    if not self.closed:
      self.close()

  def _dispatcher(self):
    ''' Read LateFunctions from the inbound queue as capacity is available
        and dispatch them. The LateFunction's ._dispatch() method will
        release the capacity on completion.
    '''
    while True:
      self.capacity.acquire()   # will be released by the LateFunction
      try:
        pri_entry = self._LFPQ.next()
      except StopIteration:
        self.capacity.release() # end of queue, not calling the handler
        break
      LF = pri_entry[-1]
      self.pending.remove(LF)
      self.running.add(LF)
      self.debug("dispatched %s", LF)
      LF._dispatch()

  def bg(self, func, *a, **kw):
    ''' Queue a function to run right now, ignoring the Later's capacity and
        priority system. This is really an easy way to utilise the Later's
        thread pool and get back a handy LateFunction for result collection.
	It can be useful for transient control functions that
	themselves queue things through the Later queuing system
	but do not want to consume capacity themselves, thus avoiding
	deadlock at the cost of transient overthreading.
    '''
    if self.closed:
      warning("%s.bg(...) after close()", self)
    funcname = None
    if isinstance(func, str):
      funcname = func
      a = list(a)
      func = a.pop(0)
    if a or kw:
      func = partial(func, *a, **kw)
    LF = LateFunction(self, func, funcname)
    self.running.add(LF)
    LF._dispatch()
    return LF

  def ready(self, **kwargs):
    ''' Awful name.
        Return a context manager to block until the Later provides a timeslot.
    '''
    return _Late_context_manager(self, **kwargs)

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
        If the parameter `pfx` is not None, submit pfx.func(func);
          see cs.logutils.Pfx's .func method for details.
    '''
    if self.closed:
      warning("%s.submit(...) after close()", self)
    return self._submit(func, priority=priority, delay=delay, when=when, name=name, pfx=pfx)

  def _submit(self, func, priority=None, delay=None, when=None, name=None, pfx=None):
    if delay is not None and when is not None:
      raise ValueError("you can't specify both delay= and when= (%s, %s)" % (delay, when))
    if priority is None:
      priority = self._priority
    elif type(priority) is int:
      priority = (priority,)
    if pfx is not None:
      func = pfx.func(func)
    LF = LateFunction(self, func, name=name)
    pri_entry = list(priority)
    pri_entry.append(seq())     # ensure FIFO servicing of equal priorities
    pri_entry.append(LF)

    now = time.time()
    if delay is not None:
      when = now + delay
    if when is None or when <= now:
      # queue the request now
      self.pending.add(LF)
      self.debug("queuing %s", LF)
      self._LFPQ.put( pri_entry )
    else:
      # queue the request at a later time
      def queueFunc():
        LF = pri_entry[-1]
        self.delayed.remove(LF)
        self.pending.add(LF)
        self.debug("queuing %s after delay", LF)
        self._LFPQ.put( pri_entry )
      with self._lock:
        if self._timerQ is None:
          self._timerQ = TimerQueue(name="<TimerQueue %s._timerQ>"%(self.name))
      self.delayed.add(LF)
      self.debug("delay %s until %s", LF, when)
      self._timerQ.add(when, queueFunc)

    return LF

  def multisubmit(self, params, func, I):
    ''' Generator that iterates over the iterable `I`, submitting
	function calls and yielding the corresponding LateFunctions,
	specificly calling:
          self.defer(params, func, item)
        where `i` is an element of `I`.
        Handy for submitting a batch of jobs.
        Caution: being a generator, the functions are not submitted
        until the caller iterates over the returned generator.
        Examples:
          L.multisubmit(f, xrange(100))
    '''
    for item in I:
      yield self.defer(params, func, item)

  ##@trace_caller
  def defer(self, func, *a, **kw):
    ''' Queue the function `func` for later dispatch using the
        default priority with the specified arguments `*a` and `**kw`.
        Return the corresponding LateFunction for result collection.
        `func` may optionally be preceeded by one or both of:
          a string specifying the function's descriptive name
	  a mapping containing parameters for `priority`,
            `delay`, and `when`.
        Equivalent to:
          submit(functools.partial(func, *a, **kw), **params)
    '''
    if self.closed:
      warning("%s.defer(...) after close()", self)
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
    MLF = self.submit(func, **params)
    return MLF

  def after(self, LFs, R, func, *a, **kw):
    ''' Queue the function `func` for later dispatch after completion of `LFs`.
        Return a Result for later collection of the function result.

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
    if self.closed:
      warning("%s.after(...) after close()", self)
    return self._after(LFs, R, func, *a, **kw)

  def _after(self, LFs, R, func, *a, **kw):
    if R is None:
      R = Result()
    elif not isinstance(R, Asynchron):
      raise TypeError("Later.after(LFs, R, func, ...): expected Asynchron for R, got %r" % (R,))
    LFs = list(LFs)
    count = len(LFs)
    def put_func():
      ''' Function to defer: run `func` and pass its return value to R.put().
      '''
      R.call(func, *a, **kw)
    if count == 0:
      # nothing to wait for - queue the function immediately
      warning("Later.after: len(LFs) == 0, func=%s", func.__name__)
      self._defer(put_func)
    else:
      # create a notification function which submits put_func
      # after sufficient notifications have been received
      countery = [count]  # to stop "count" looking like a local var inside the closure
      def submit_func(LF):
        ''' Notification function to submit `func` after sufficient invocations.
        '''
        countery[0] -= 1
        if countery[0] == 0:
          self._defer(put_func)
      # submit the notifications
      for LF in LFs:
        LF.notify(submit_func)
    return R

  def retry(self, R, func, *a, **kw):
    ''' Queue the call `func` for later dispatch and possible
        repetition.
	If `R` is None a new cs.threads.Result is allocated to
	accept the function return value.
        The return value from `func` should be a tuple:
          LFs, result
	where LFs, if not empty, is a sequence of LateFunctions
	which should complete. After completion, `func` is queued
	again.
	When LFs is empty, result is passed to R.put() and `func`
	is not requeued.
    '''
    if R is None:
      R = Result()
    def retry():
      LFs = []
      LFs, result = func(LFs, *a, **kw)
      if LFs:
        self.after(LFs, R, retry)
      else:
        R.put(result)
    self.defer(retry)
    return R

  def defer_iterable(self, I, outQ=None):
    ''' Submit an iterable `I` for asynchronous stepwise iteration
        to return results via the queue `outQ`.
        `outQ` must have a .put method to accept items and a .close method to
        indicate the end of items.
        When the iteration is complete, call outQ.close().
        If `outQ` is None, instantiate a new IterableQueue.
        Return `outQ`.
    '''
    if self.closed:
      warning("%s.defer_iterable after close", self)
    return self._defer_iterable(I, outQ=outQ)

  def _defer_iterable(self, I, outQ=None):
    if outQ is None:
      outQ = IterableQueue(name="IQ:defer_iterable:outQ%d" % seq(), open=True)
    iterate = iter(I).next

    def iterate_once():
      ''' Call `iterate`. Place the result on outQ.
          Close the queue at end of iteration or other exception.
          Otherwise, requeue ourself to collect the next iteration value.
      '''
      try:
        item = iterate()
      except StopIteration:
        outQ.close()
      except Exception as e:
        error("defer_iterable: iterate_once: exception during iteration: %s", e)
        outQ.close()
      else:
        outQ.put(item)
        self._defer(iterate_once)

    self._defer(iterate_once)
    return outQ

  def pipeline(self, filter_funcs, inputs, outQ=None, open=False):
    ''' Construct a function pipeline to be mediated by this Later queue.
        Return:
          input, output
        where `input`` is a closeable queue on which more data items can be put
        and `output` is an iterable from which result can be collected.

        `filter_funcs`: an iterable of filter functions accepting the
          single items from the iterable `inputs`, returning an
          iterable output.
	`inputs`: the initial iterable inputs; this may be None.
	  If None, it is expected that the caller will be supplying
	  input items via `input.put()`.
        `outQ`: the optional output queue; if None, an IterableQueue() will be
          allocated.

        If `inputs` is None or `open` is true, the returned `input` requires
        a call to `input.close()` when no further inputs are to be supplied.

        Example use with presupplied 

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
    if self.closed:
      warning("%s.pipeline after close", self)
    return self._pipeline(filter_funcs, inputs, outQ=None)

  def _pipeline(self, filter_funcs, inputs, outQ=None, open=False):
    filter_funcs = list(filter_funcs)
    if not filter_funcs:
      raise ValueError("no filter_funcs")
    if outQ is None:
      outQ = IterableQueue(name="pipelineIQ", open=True)
    ##outQ.close = trace_caller(outQ.close)
    RHQ = outQ
    count = 0
    while filter_funcs:
      func_sig, func_iter, func_final = self._pipeline_func(filter_funcs.pop())
      count += 1
      PQ = PushQueue(self, func_iter, RHQ, is_iterable=True, func_final=func_final, name="pipelinePQ%d"%count, open=True)
      RHQ = PQ
    if inputs is not None:
      if open:
        # extra open() so that defer_iterable doesn't perform the final close
        RHQ.open()
      self._defer_iterable( inputs, RHQ )
    return RHQ, outQ

  def _pipeline_func(self, o):
    ''' Accept a pipeline element. Return (func_sig, func_iter, func_final).
        A pipeline element is either a single function, in which case it is
        presumed to be a one-to-many-generator with func_sig FUNC_ONE_TO_MANY,
        or a tuple of (func_sig, func).
        The returned func_iter and func_final take the following values according to func_sig:

          func_sig              func_iter, func_final

          FUNC_ONE_TO_MANY      func, None
                                Example: a directory listing.

          FUNC_SELECTOR         func is presumed to be a Boolean test, and
                                func_iter is a generator that yields its
                                argument if the test succeeds.
                                func_final is None.
                                Example: a test for inclusion.

          FUNC_MANY_TO_MANY     func_iter is set to save its argument to a list and yield nothing.
                                func_final applies func to the list and yields the results.
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
        yield func(item)
    elif func_sig == FUNC_ONE_TO_MANY:
      func_iter = func
    elif func_sig == FUNC_SELECTOR:
      def func_iter(item):
        if func(item):
          yield item
    elif func_sig == FUNC_MANY_TO_MANY:
      gathered = []
      def func_iter(item):
        gathered.append(item)
        if False:
          yield
      def func_final():
        for item in func(gathered):
          yield item
    else:
      raise ValueError("unsupported function signature %r" % (func_sig,))
    return func_sig, func_iter, func_final

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

if __name__ == '__main__':
  import cs.later_tests
  cs.later_tests.selftest(sys.argv)
