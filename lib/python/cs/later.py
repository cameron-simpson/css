#!/usr/bin/python
#

from __future__ import print_function

DISTINFO = {
    'description': "queue functions for execution later",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'requires': ['cs.py3', 'cs.py.func', 'cs.debug', 'cs.excutils', 'cs.queues', 'cs.threads', 'cs.asynchron', 'cs.seq', 'cs.logutils'],
}

from contextlib import contextmanager
from functools import partial
import sys
from collections import deque
import threading
import traceback
from cs.py3 import Queue, raise3
from cs.py.func import funcname
import time
from cs.debug import ifdebug, Lock, RLock, Thread, trace_caller, thread_dump
from cs.excutils import noexc, noexc_gen, logexc, logexc_gen, LogExceptions
from cs.queues import IterableQueue, IterablePriorityQueue, PushQueue, \
                        NestingOpenCloseMixin, TimerQueue
from cs.threads import AdjustableSemaphore, \
                       WorkerThreadPool, locked
from cs.asynchron import Result, Asynchron, ASYNCH_RUNNING
from cs.seq import seq, TrackingCounter
from cs.logutils import Pfx, PfxCallInfo, error, info, warning, debug, exception, D, OBSOLETE

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
      ''' This is the placeholder function dispatched by the Later instance.
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
    final = kw.pop('final', None)
    Asynchron.__init__(self, final=final)
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

  def __init__(self, later, func, name=None, final=None):
    ''' Initialise a LateFunction.
        `later` is the controlling Later instance.
        `func` is the callable for later execution.
        `name`, if supplied, specifies an identifying name for the LateFunction.
    '''
    PendingFunction.__init__(self, func, final=final)
    if name is None:
      name = "LF-%d[func=%s]" % ( seq(), funcname(func) )
    self.name = name
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
    PendingFunction._complete(self, result, exc_info)
    self.later._completed(self, result, exc_info)
    self.later._busy.dec(self.name)
    self.later.close()

  def _dispatch(self):
    ''' ._dispatch() is called by the Later class instance's worker thread.
        It causes the function to be handed to a thread for execution.
    '''
    L = self.later
    L.debug("DISPATCH %s", self)
    with self._lock:
      if not self.pending:
        raise RuntimeError("should be pending, but state = %s", self.state)
      self.state = ASYNCH_RUNNING
      L._workers.dispatch(self.func, deliver=self._worker_complete, daemon=True)
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

class _PipelinePushQueue(PushQueue):
  ''' A _PipelinePushQueue subclasses cs.queues.PushQueue, adding some item tracking.
      We raise the pipeline's _busy counter for every item in play,
      and also raise it while the finalisation function has not run.
      This lets us inspect a pipeline for business, which we use in the
      cs.app.pilfer termination process.
  '''

  def __init__(self, name, pipeline, func_iter, outQ, func_final=None):
    ''' Initialise the _PipelinePushQueue, wrapping func_iter and func_final in code to inc/dec the main pipeline _busy counter.
    '''
    self.pipeline = pipeline

    # wrap func_iter to raise _busy while processing item
    @logexc_gen
    def func_push(item):
      self.pipeline._busy.inc()
      try:
        for item2 in func_iter(item):
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
          result = func_final0()
        except Exception:
          self.pipeline._busy.dec()
          raise
        self.pipeline._busy.dec()
        return result
      func_final.__name__ = "pipeline_dec_busy(%s)" % (func_final0.__name__,)

    PushQueue.__init__(self, name, self.pipeline.later, func_push, outQ, func_final=func_final)

  def __str__(self):
    return "%s[%s]" % (PushQueue.__str__(self), self.pipeline)

class _Pipeline(NestingOpenCloseMixin):
  ''' A _Pipeline encapsulates the chain of PushQueues created by a call to Later.pipeline.
  '''

  def __init__(self, name, L, filter_funcs, outQ):
    ''' Initialise the _Pipeline from `name`, Later instance `L`, list  of filter functions `filter_funcs` and output queue `outQ`.
    '''
    self.name = name
    self.later = L
    self.queues = [outQ]
    self._lock = Lock()
    NestingOpenCloseMixin.__init__(self)
    # counter tracking items in play
    self._busy = TrackingCounter(name="Pipeline<%s>._items" % (name,))
    RHQ = outQ
    count = len(filter_funcs)
    while filter_funcs:
      func_iter, func_final = self._pipeline_func(filter_funcs.pop())
      count -= 1
      pq_name = ":".join( (name,
                           "%s/%s" % ( (funcname(func_iter) if func_iter else "None"),
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
        yield func(item)
      func_iter.__name__ = "func_iter_1to1(func=%s)" % (funcname(func),)
    elif func_sig == FUNC_ONE_TO_MANY:
      func_iter = func
    elif func_sig == FUNC_SELECTOR:
      def func_iter(item):
        if func(item):
          yield item
      func_iter.__name__ = "func_iter_1toMany(func=%s)" % (funcname(func),)
    elif func_sig == FUNC_MANY_TO_MANY:
      gathered = []
      def func_iter(item):
        debug("GATHER %r FOR %s", item, funcname(func))
        gathered.append(item)
        if False:
          yield
      func_iter.__name__ = "func_iter_gather(func=%s)" % (funcname(func),)
      def func_final():
        for item in func(gathered):
          yield item
      func_final.__name__ = "func_final_gather(func=%s)" % (funcname(func),)
    else:
      raise ValueError("unsupported function signature %r" % (func_sig,))
    return func_iter, func_final

class Later(NestingOpenCloseMixin):
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
  def __init__(self, capacity, name=None, inboundCapacity=0):
    if name is None:
      name = "Later-%d" % (seq(),)
    self._lock = RLock()
    self._finished = threading.Condition(self._lock)
    self.finished = False
    NestingOpenCloseMixin.__init__(self)
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
    self._busy = TrackingCounter(name="Later<%s>._busy" % (name,)) # counter tracking jobs queued or active
    self._quiescing = False
    self._state = ""
    self.logger = None          # reporting; see logTo() method
    self._priority = (0,)
    self._timerQ = None         # queue for delayed requests; instantiated at need
    # inbound requests queue
    self._pendingq = IterablePriorityQueue(inboundCapacity, name="%s._pendingq" % (self.name,))
    self._workers = WorkerThreadPool(name=name+":WorkerThreadPool").open()
    self._dispatchThread = Thread(name=self.name+'._dispatcher', target=self._dispatcher)
    self._dispatchThread.daemon = True
    self._dispatchThread.start()

  def __repr__(self):
    return '<%s "%s" capacity=%s running=%d (%s) pending=%d (%s) delayed=%d busy=%d:%s closed=%s>' \
           % ( self.__class__.__name__, self.name,
               self.capacity,
               len(self.running), ','.join( repr(LF.name) for LF in self.running ),
               len(self.pending), ','.join( repr(LF.name) for LF in self.pending ),
               len(self.delayed),
               int(self._busy), self._busy,
               self.closed
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

  def shutdown(self):
    ''' Shut down the Later instance:
        - close the TimerQueue, if any, and wait for it to complete
        - close the request queue
        - wait for the job dispatcher to finish
	- close the worker thread pool, which waits for any of its
          outstanding threads to complete
    '''
    with Pfx("%s.shutdown()" % (self,)):
      if not self.closed:
        error("NOT CLOSED")
      if self.finished:
        warning("_finish: finished=%r, early return", self.finished)
        return
      self.finished = True
      if self._timerQ:
        self._timerQ.close()
        self._timerQ.join()
      self._pendingq.close()              # prevent further submissions
      self._workers.close()           # wait for all worker threads to complete
      self._dispatchThread.join()     # wait for all functions to be dispatched
      self._finished.acquire()
      self._finished.notify_all()
      self._finished.release()

  @locked
  def is_idle(self):
    with self._lock:
      status = not self._busy and not self.delayed and not self.pending and not self.running
    return status

  def quiesce(self):
    ''' Block until there are no jobs queued or active.
    '''
    self._quiescing = True
    self._busy.wait(0)
    self._quiescing = False

  @locked
  def is_finished(self):
    return self.closed and self.is_idle()

  def wait(self):
    ''' Wait for all active and pending jobs to complete, including
        any jobs they may themselves queue.
    '''
    if self.finished:
      debug("%s.wait: already finished - return immediately", self)
      pass
    else:
      self._finished.acquire()
      self._finished.wait()

  def _track(self, tag, LF, fromset, toset):
    def SN(s):
      if s is None: return "None"
      if s is self.delayed: return "delayed"
      if s is self.pending: return "pending"
      if s is self.running: return "running"
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
    debug("%s: __enter__", self)
    global default
    L = NestingOpenCloseMixin.__enter__(self)
    default.push(L)
    return L

  def __exit__(self, exc_type, exc_val, exc_tb):
    ''' Exit handler: release the "complete" lock; the placeholder
        function is blocking on this, and will return on its release.
    '''
    debug("%s: __exit__: exc_type=%s", self, exc_type)
    NestingOpenCloseMixin.__exit__(self, exc_type, exc_val, exc_tb)
    global default
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
      self._close()

  def _dispatcher(self):
    ''' Read LateFunctions from the inbound queue as capacity is available
        and dispatch them. The LateFunction's ._dispatch() method will
        release the capacity on completion.
    '''
    while True:
      self.capacity.acquire()   # will be released by the LateFunction
      try:
        pri_entry = self._pendingq.next()
      except StopIteration:
        self.capacity.release() # end of queue, not calling the handler
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

  def bg(self, func, *a, **kw):
    ''' Queue a function to run right now, ignoring the Later's capacity and
        priority system. This is really an easy way to utilise the Later's
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
    '''
    if not self.submittable:
      raise RuntimeError("%s.submit(...) but not self.submittable" % (self,))
    return self._submit(func, priority=priority, delay=delay, when=when, name=name, pfx=pfx)

  def _submit(self, func, priority=None, delay=None, when=None, name=None, pfx=None):
    if delay is not None and when is not None:
      raise ValueError("you can't specify both delay= and when= (%s, %s)" % (delay, when))
    if priority is None:
      priority = self._priority
    elif type(priority) is int:
      priority = (priority,)
    if pfx is not None:
      func = pfx.partial(func)
    LF = LateFunction(self, func, name=name)
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
          self._timerQ = TimerQueue(name="<TimerQueue %s._timerQ>"%(self.name))
      self.debug("delay %s until %s", LF, when)
      self._track("_submit: delay", LF, None, self.delayed)
      self._timerQ.add(when, queueFunc)

    return LF

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
    if not self.submittable:
      raise RuntimeError("%s.after(...) but not self.submittable" % (self,))
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
    put_func.__name__ = "%s._after(%r)[func=%s]" % (self, LFs, funcname(func))

    if count == 0:
      # nothing to wait for - queue the function immediately
      debug("Later.after: len(LFs) == 0, func=%s", funcname(func))
      self._defer(put_func)
    else:
      # create a notification function which submits put_func
      # after sufficient notifications have been received
      self._busy.inc("Later._after")
      L = self.open()
      countery = [count]  # to stop "count" looking like a local var inside the closure
      def submit_func(LF):
        ''' Notification function to submit `func` after sufficient invocations.
        '''
        countery[0] -= 1
        if countery[0] != 0:
          return
        self._defer(put_func)
        L.close()
        self._busy.dec("Later._after")
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

  def defer_iterable(self, I, outQ):
    ''' Submit an iterable `I` for asynchronous stepwise iteration
        to return results via the queue `outQ`.
        `outQ` must have a .put method to accept items and a .close method to
        indicate the end of items.
        When the iteration is complete, call outQ.close().
    '''
    if not self.submittable:
      raise RuntimeError("%s.defer_iterable(...) but not self.submittable" % (self,))
    return self._defer_iterable(I, outQ=outQ)

  def _defer_iterable(self, I, outQ):
    iterate = partial(next, iter(I))

    @logexc
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
    if not self.submittable:
      raise RuntimeError("%s.pipeline(...) but not self.submittable" % (self,))
    return self._pipeline(filter_funcs, inputs, outQ=outQ, name=name)

  def _pipeline(self, filter_funcs, inputs=None, outQ=None, name=None):
    filter_funcs = list(filter_funcs)
    debug("%s._pipeline: filter_funcs=%r", self, filter_funcs)
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

if __name__ == '__main__':
  import cs.later_tests
  cs.later_tests.selftest(sys.argv)
