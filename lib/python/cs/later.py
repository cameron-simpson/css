#!/usr/bin/python
#

from contextlib import contextmanager
from functools import partial
import sys
from collections import deque
from thread import allocate_lock
from threading import Thread, Condition
from Queue import Queue
import time
import unittest
from cs.threads import AdjustableSemaphore, IterablePriorityQueue, \
                       Channel, WorkerThreadPool, TimerQueue
from cs.misc import seq

class LateFunction(object):
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
        except SomeException, e:
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

  def __init__(self, later, func):
    self.later = later
    self.func = func
    self.done = False
    self._join_lock = allocate_lock()
    self._join_cond = Condition()
    self._join_notifiers = []

  def _dispatch(self):
    ''' ._dispatch() is called by the Later class instance's worker thread.
        It causes the function to be handed to a thread for execution.
    '''
    assert not self.done
    self.later._workers.dispatch(self.func, deliver=self.__getResult)

  def __getResult(self, result):
    # collect the result and release the capacity
    with self._join_lock:
      self.result = result
      self.done = True
      notifiers = list(self._join_notifiers)
    self.later.capacity.release()
    for notify in notifiers:
      notify(self)
    with self._join_cond:
      self._join_cond.notify_all()

  def __call__(self, *args, **kwargs):
    ''' Calling the object waits for the function to run to completion
        and returns the function return value.
        If the function threw an exception that exception is reraised.
    '''
    assert not args
    assert not kwargs
    result, exc_info = self.wait()
    if exc_info:
      exc_type, exc_value, exc_traceback = exc_info
      raise exc_type, exc_value, exc_traceback
    return result

  def wait(self):
    ''' Calling the .wait() method waits for the function to run to
        completion and returns a tuple as for the WorkerThreadPool's
        .dispatch() return queue:
        On completion the sequence:
          func_result, None, None, None
        is returned.
        On an exception the sequence:
          None, exec_type, exc_value, exc_traceback
        is returned.
    '''
    self.join()
    return self.result

  def join(self):
    ''' Wait for the function to complete.
        The function return value is available as self.result.
    '''
    with self._join_cond:
      with self._join_lock:
        if self.done:
          return
      self._join_cond.wait()
      assert self.done

  def report(self, notify):
    ''' After the function completes, run notify(self).
        If the function has already completed this will happen immediately.
        Note: if you'd rather `self` got put on some Queue `Q`, supply `Q.put`.
    '''
    with self._join_lock:
      if not self.done:
        self._join_notifiers.append(notify)
        notify = None
    if notify is not None:
      notify(self)

class Later(object):
  ''' A management class to queue function calls for later execution.
      If `capacity` is an int, it is used to size a Semaphore to constrain
      the number of dispatched functions which may be in play at a time.
      If `capacity` is not an int it is presumed to already be a
      suitable Semaphore-like object.
      `inboundCapacity` can be specified to limit the number of undispatched
      functions that may be queued up; the default is 0 (no limit).
      Calls to submit functions when the inbound limit is reached block
      until some functions are dispatched.
      The `name` paraeter may be used to supply an identifying name
      for this instance.
  '''
  def __init__(self, capacity, inboundCapacity=0, name=None):
    if type(capacity) is int:
      capacity = AdjustableSemaphore(capacity)
    self.capacity = capacity
    self.inboundCapacity = inboundCapacity
    if name is None:
      name = "Later-%d" % (seq(),)
    self.name = name
    self.closed = False
    self._priority = (0,)
    self._timerQ = None                    # queue for delayed requests
    # inbound requests queue
    self._LFPQ = IterablePriorityQueue(inboundCapacity)
    self._workers = WorkerThreadPool()
    self._dispatchThread = Thread(target=self._dispatcher)
    self._lock = allocate_lock()
    self._dispatchThread.start()

  def __str__(self):
    return self.name

  def __del__(self):
    if not self.closed:
      self.close()

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    self.close()
    return False

  def close(self):
    assert not self.closed
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
      latefunc = pri_entry[-1]
      latefunc._dispatch()

  def submit(self, func, priority=None, delay=None, when=None):
    ''' Submit a function for later dispatch.
        Return the corresponding LateFunction for result collection.
	If the parameter `priority` not None then use it as the priority
        otherwise use the default priority.
        If the parameter `delay` is not None, delay consideration of
        this function until `delay` seconds from now.
        If the parameter `when` is not None, delay consideration of
        this function until the time `when`.
        It is an error to specify both `when` and `delay`.
    '''
    assert delay is None or when is None, \
           "you can't specify both delay= and when= (%s, %s)" % (delay, when)
    if priority is None:
      priority = self._priority
    elif type(priority) is int:
      priority = (priority,)
    LF = LateFunction(self, func)
    pri_entry = list(priority)
    pri_entry.append(seq())     # ensure FIFO servicing of equal priorities
    pri_entry.append(LF)

    now = time.time()
    if delay is not None:
      when = now + delay
    if when is None or when <= now:
      # queue the request now
      self._LFPQ.put( pri_entry )
    else:
      # queue the request at a later time
      def queueFunc(func):
        self._LFPQ.put( pri_entry )
      with self._lock:
        if self._timerQ is None:
          self._timerQ = TimerQueue(name="<TimerQueue %s._timerQ>"%(self.name))
      self._timerQ.add(when, partial(queueFunc, func))

    return LF

  def submitargs(self, d, func, *args, **kwargs):
    ''' Submit a function with arguments for later dispatch.
        Return the corresponding LateFunction for result collection.
        The `d` parameter is a dictionary whose members correspond to the
        `priority`, `delay`, `when` parameters of submit().
        For example:
          LF = L.submitargs( {'priority': 2, 'delay': 3},
                             func, 1, 2, 3, d=4, e=5 )
        is equivalent to:
          LF = L.submit(partial(func, 1, 2, 3, d=4, e=5),
                        priority=2, delay=3)
        Each results in a call to:
          func(1, 2, 3, d=4, e=5)
        at least 3 seconds from now.
    '''
    return self.submit(partial(func, *args, **kwargs), **d)

  def pdefer(self, priority, func):
    ''' Queue a function for later dispatch.
        Return the corresponding LateFunction for result collection.
        Equivalent to:
          submit(func, priority=priority)
    '''
    return self.submit(func, priority=priority)

  def defer(self, func):
    ''' Queue a function for later dispatch using the default priority.
        Return the corresponding LateFunction for result collection.
    '''
    return self.submit(func)

  def ppartial(self, priority, func, *args, **kwargs):
    ''' Queue a function for later dispatch using the specified priority.
        Return the corresponding LateFunction for result collection.
    '''
    return self.submit(partial(func, *args, **kwargs), priority=priority)

  def partial(self, func, *args, **kwargs):
    ''' Queue a function for later dispatch using the default priority.
        Return the corresponding LateFunction for result collection.
    '''
    return self.submit(partial(func, *args, **kwargs))

  @contextmanager
  def priority(self, pri):
    ''' A context manager to temporarily set the default priority.
        Example:
          L = Later(4)
          with L.priority(1):
            L.defer(f)  # queue f() with priority 1
          with L.priority(2):
            L.partial(f,3)  # queue f(3) with priority 2
	This is most useful with the .partial() method, which has
	no priority parameter.
        WARNING: this is NOT thread safe!
        TODO: is a thread safe version even a sane idea without a
              per-thrad priority stack?
    '''
    oldpri = self._priority
    self._priority = pri
    yield
    self._priority = oldpri

  def report(self, LFs):
    ''' Report completed LateFunctions.
        This is a generator that yields LateFunctions as they complete,
        useful for waiting for a set of LateFunctions that may complete in
        an arbitrary order.
    '''
    Q = Queue()
    n = 0
    notify = Q.put
    for LF in LFs:
      n += 1
      LF.report(notify)
    for i in range(n):
      yield Q.get()

class TestLater(unittest.TestCase):

  @staticmethod
  def _f(x):
    return x*2
  @staticmethod
  def _delay(n):
    time.sleep(n)
    return n
  class _Bang(BaseException):
    pass
  @staticmethod
  def _bang():
    raise TestLater._Bang()

  def setUp(self):
    self.L = Later(2)

  def tearDown(self):
    self.L.close()

  def test00one(self):
    # compute 3*2
    L = self.L
    F = partial(self._f, 3)
    LF = L.defer(F)
    x = LF()
    self.assertEquals(x, 6)

  def test01two(self):
    # two sleep(2) in parallel
    L = self.L
    F = partial(self._delay, 2)
    LF1 = L.defer(F)
    LF2 = L.defer(F)
    now = time.time()
    x = LF1()
    y = LF2()
    elapsed = time.time() - now
    self.assert_(elapsed < 3)

  def test02three(self):
    # three sleep(2), two in parallel, one delayed
    L = self.L
    F = partial(self._delay, 2)
    LF1 = L.defer(F)
    LF2 = L.defer(F)
    LF3 = L.defer(F)
    now = time.time()
    x = LF1()
    y = LF2()
    z = LF3()
    elapsed = time.time() - now
    self.assert_(elapsed >= 4)

  def test03calltwice(self):
    # compute once, get result twice
    L = self.L
    F = partial(self._f, 5)
    LF = L.defer(F)
    x = LF()
    self.assertEquals(x, 10)
    y = LF()
    self.assertEquals(y, 10)

  def test04raise(self):
    # raise exception
    LF = self.L.defer(self._bang)
    self.assertRaises(TestLater._Bang, LF)

  def test05raiseTwice(self):
    # raise exception again
    LF = self.L.defer(self._bang)
    self.assertRaises(TestLater._Bang, LF)
    self.assertRaises(TestLater._Bang, LF)

  def test06partial(self):
    # compute 7*2 using .partial()
    LF = self.L.partial(self._f, 7)
    x = LF()
    self.assertEquals(x, 14)

  def test07report(self):
    with Later(3) as L3:
      LF1 = L3.partial(self._delay, 3)
      LF2 = L3.partial(self._delay, 2)
      LF3 = L3.partial(self._delay, 1)
      results = [ LF() for LF in self.L.report( (LF1, LF2, LF3) ) ]
      self.assertEquals(results, [1, 2, 3])

  def test08delay(self):
    with Later(3) as L3:
      LF1 = L3

if __name__ == '__main__':
  unittest.main()
