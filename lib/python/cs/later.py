#!/usr/bin/python
#

from __future__ import print_function
from contextlib import contextmanager
from functools import partial
import sys
from collections import deque
import threading
from cs.py3 import Queue, raise3
import time
from cs.debug import ifdebug, Lock, RLock, Thread
from cs.threads import AdjustableSemaphore, IterablePriorityQueue, \
                       WorkerThreadPool, TimerQueue, Result, \
                       Asynchron, ASYNCH_RUNNING
from cs.seq import seq
from cs.logutils import Pfx, info, warning, debug, D

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
    do_func = False
    with self._lock:
      if self.pending:
        do_func = True
        self.state = ASYNCH_RUNNING
      else:
        raise RuntimeError("state should be ASYNCH_PENDING but is %s" % (self.state))
    if do_func:
      result, exc_info = None, None
      try:
        result = self.func()
      except:
        exc_info = sys.exc_info()
        self.exc_info = exc_info
      else:
        self.result = result
    if exc_info:
      exc_type, exc_value, exc_traceback = exc_info
      raise exc_type(exc_value).with_traceback(exc_traceback)
    return result

def CallableValue(value):
  ''' Return a callable that returns the supplied value.
      This wraps the value in an OnDemandFunction for compatability
      with other PendingFunctions.
      Of course, if you don't need .wait() et al you can just use:
        lambda: value
      instead.
  '''
  F = OnDemandFunction(lambda: None)
  F.result = value
  return F

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

  def __call__(self):
    ''' Calling the LateFunction waits for the function to run to completion
        and returns the function return value.
        If the function threw an exception that exception is reraised.
    '''
    result, exc_info = self.wait()
    if exc_info:
      raise3(*exc_info)
    return result

  def _worker_complete(self, work_result):
    result, exc_info = work_result
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
    self._LFPQ = IterablePriorityQueue(inboundCapacity)
    self._workers = WorkerThreadPool()
    self._dispatchThread = Thread(name=self.name+'._dispatcher', target=self._dispatcher)
    self._lock = Lock()
    self._dispatchThread.start()

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
      raise RunTimError("%s.bg(...) after close()")
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
    ##D("%s.submit()...", self)
    if self.closed:
      raise RunTimError("%s.bg(...) after close()")
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

  def multisubmit(self, params, func, iter):
    ''' Generator that iterates over `iter`, submitting function calls and
        yielding the corresponding LateFunctions, specificly calling:
          self.defer(params, func, i)
        where `i` is an element of `iter`.
        Handy for submitting a batch of jobs.
        Caution: being a generator, the functions are not submitted
        until the caller iterates over the returned generator.
        Examples:
          L.multisubmit(f, xrange(100))
    '''
    for i in iter:
      yield self.defer(params, func, i)

  def defer(self, func, *a, **kw):
    ''' Queue the function `func` for later dispatch using the
        default priority with the spcified arguments `*a` and `**kw`.
        Return the corresponding LateFunction for result collection.
        `func` may optionally be preceeded by one or both of:
          a string specifying the function's descriptive name
	  a mapping containing parameters for `priority`,
            `delay`, and `when`.
        Equivalent to:
          submit(functools.partial(func, *a, **kw), **params)
    '''
    if self.closed:
      raise RunTimError("%s.bg(...) after close()")
    if a:
      a = list(a)
      params = {}
    funcname = None
    while not callable(func):
      D("SKIPPING func=%r, not callable", func)
      if isinstance(func, str):
        funcname = func
        func = a.pop(0)
        D("funcname = %r, func = %s", funcname, func)
    else:
      params = func
      func = a.pop(0)
    if funcname is not None:
      params['name'] = funcname
    if a or kw:
      func = partial(func, *a, **kw)
    MLF = self.submit(func, **params)
    return MLF

  def __call__(self, *a, **kw):
    return self.defer(*a, **kw)()

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
