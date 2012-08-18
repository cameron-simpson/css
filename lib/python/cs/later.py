#!/usr/bin/python
#

from contextlib import contextmanager
from functools import partial
import sys
from collections import deque
from threading import Lock
from threading import Thread, Condition
from Queue import Queue
import time
from cs.threads import AdjustableSemaphore, IterablePriorityQueue, \
                       WorkerThreadPool, TimerQueue
from cs.misc import seq
from cs.logutils import Pfx, info, warning, debug, D

STATE_PENDING = 0       # function not yet dispatched
STATE_RUNNING = 1       # function executing
STATE_DONE = 2          # function complete
STATE_CANCELLED = 3     # function cancelled

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

class PendingFunction(object):
  ''' Common state for LateFunctions and OnDemandFunctions.
  '''

  def __init__(self, func, *a, **kw):
    if a or kw:
      func = partial(func, *a, **kw)
    self.func = func
    self.state = STATE_PENDING
    self.result = None
    self._lock = Lock()
    self.join_cond = Condition()
    self.notifiers = []

  @property
  def ready(self):
    return self.state == STATE_DONE or self.state == STATE_CANCELLED
  done = ready

  @property
  def cancelled(self):
    ''' Test whether this PendingFunction has been cancelled.
    '''
    return self.state == STATE_CANCELLED

  def cancel(self):
    ''' Cancel this function.
        If self.state is STATE_PENDING or STATE_CANCELLED, return True.
        Otherwise return False (too late to cancel).
    '''
    with self._lock:
      state = self.state
      if state == STATE_PENDING:
        self.state = STATE_CANCELLED
        self.func = None
        self.set_result( (None, None) )
      elif state == STATE_RUNNING or state == STATE_DONE:
        return False
    return True

  def set_result(self, result):
    ''' set_result() is called by a worker thread to report completion of the
        function.
        The argument `result` is a 2-tuple as produced by cs.threads.WorkerThreadPool:
          func_result, None
        or:
          None, exc_info
        where exc_info is (exc_type, exc_value, exc_traceback).
    '''
    # collect the result and release the capacity
    with self._lock:
      if self.state != STATE_CANCELLED:
        self.state = STATE_DONE
        self.result = result
      notifiers = list(self.notifiers)
    for notifier in notifiers:
      notifier(self)
    with self.join_cond:
      self.join_cond.notify_all()

  def wait(self):
    ''' Calling the .wait() method waits for the function to run to
        completion and returns a tuple as for the WorkerThreadPool's
        .dispatch() return queue.
        On completion the sequence:
          func_result, None
        is returned.
        On an exception the sequence:
          None, exc_info
        is returned where exc_info is a tuple of (exc_type, exc_value, exc_traceback).
    '''
    self.join()
    return self.result

  def join(self):
    ''' Wait for the function to complete.
        The function return value is available as self.result.
    '''
    with self.join_cond:
      if self.done:
        return
      self.join_cond.wait()
      assert self.done

  def notify(self, notifier):
    ''' After the function completes, run notifier(self).
        If the function has already completed this will happen immediately.
        Note: if you'd rather `self` got put on some Queue `Q`, supply `Q.put`.
    '''
    with self._lock:
      if not self.done:
        self.notifiers.append(notifier)
        notifier = None
    if notifier is not None:
      notifier(self)

class OnDemandFunction(PendingFunction):
  ''' Wrap a callable, call it later.
      Like a LateFunction, you can call the wrapper many times; the
      inner function will only run once.
  '''

  def __init__(self, func, *a, **kw):
    PendingFunction.__init__(self, func, *a, **kw)

  def __call__(self):
    do_func = False
    with self._lock:
      if self.state == STATE_PENDING:
        do_func = True
        self.state = STATE_RUNNING
    if do_func:
      result, exc_info = None, None
      try:
        result = self.func()
      except:
        exc_info = sys.exc_info()
      self.set_result( (result, exc_info) )
    result, exc_info = self.result
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
  F.set_result( (value, None) )
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
    with self._lock:
      assert self.state == STATE_PENDING
      self.state = STATE_RUNNING
      self.later._workers.dispatch(self.func, deliver=self.set_result)
      self.func = None

  def __call__(self):
    ''' Calling the LateFunction waits for the function to run to completion
        and returns the function return value.
        If the function threw an exception that exception is reraised.
    '''
    result, exc_info = self.wait()
    if exc_info:
      exc_type, exc_value, exc_traceback = exc_info
      raise exc_type(exc_value).with_traceback(exc_traceback)
    return result

  def set_result(self, result):
    self.later.capacity.release()
    self.later.running.remove(self)
    self.later.debug("completed %s", self)
    PendingFunction.set_result(self, result)

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
    self._timerQ = None                    # queue for delayed requests
    # inbound requests queue
    self._LFPQ = IterablePriorityQueue(inboundCapacity)
    self._workers = WorkerThreadPool()
    self._dispatchThread = Thread(target=self._dispatcher)
    self._lock = Lock()
    self._dispatchThread.start()

  def __repr__(self):
    return '<%s "%s" running=%d pending=%d delayed=%d closed=%s>' \
           % ( self.__class__, self.name,
               len(self.running),
               len(self.pending),
               len(self.delayed),
               self.closed
             )

  def __str__(self):
    return "<%s pending=%d running=%d delayed=%d>" \
           % (self.name,
              len(self.pending), len(self.running), len(self.delayed))

  def __enter__(self):
    debug("%s: __enter__", self)

  def __exit__(self, exc_type, exc_val, exc_tb):
    ''' Exit handler: release the "complete" lock; the placeholder
        function is blocking on this, and will return on its release.
    '''
    debug("%s: __exit__: exc_type=%s", self, exc_type)
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

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    self.close()
    return False

  def close(self):
    with Pfx("%s.close()" % (self,)):
      if self.closed:
        warning("close of closed Later %r", self)
      else:
        self.closed = True
        if self._timerQ:
          self._timerQ.close()
        self._LFPQ.close()
        self._dispatchThread.join()
        self._workers.close()

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
    '''
    if a or kw:
      func = partial(func, *a, **kw)
    LF = LateFunction(self, func)
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
        `func` may optionally be preceeded by a mapping `params` containing
        parameters for `priority`, `delay`, and `when`.
        Equivalent to:
          submit(functools.partial(func, *a, **kw), **params)
    '''
    if callable(func):
      params = {}
    else:
      params = func
      func = a.pop(0)
      if not callable(func):
        raise RuntimeError('defer: neither params nor func is callable')
    if a or kw:
      func = partial(func, *a, **kw)
    return self.submit(func, **params)

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

def report(LFs):
  ''' Report completed LateFunctions.
      This is a generator that yields LateFunctions as they complete, useful
      for waiting for a sequence of LateFunctions that may complete in an
      arbitrary order.
  '''
  Q = Queue()
  n = 0
  notify = Q.put
  for LF in LFs:
    n += 1
    LF.notify(notify)
  for i in range(n):
    yield Q.get()

if __name__ == '__main__':
  import cs.later_tests
  cs.later_tests.selftest(sys.argv)
