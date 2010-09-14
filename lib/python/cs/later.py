#!/usr/bin/python
#

from contextlib import contextmanager
from functools import partial
import sys
from collections import deque
from thread import allocate_lock
from threading import Thread, Condition
import time
import unittest
from cs.threads import AdjustableSemaphore, IterablePriorityQueue, \
                       Channel, WorkerThreadPool
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
        x, exc_type, exc_value, exc_traceback = LF.wait()
        if exc_type is not None:
          # handle exception
        else:
          # use `x`

      TODO: .cancel()
            timeout for wait()
  '''

  def __init__(self, later, func):
    self.later = later
    self.func = func
    self.done = False
    self._join_lock = allocate_lock()
    self._join_cond = Condition()

  def _dispatch(self):
    ''' ._dispatch() is called by the Later's class instance's worker thread.
        It causes the function to be handed to a thread for execution.
    '''
    assert not self.done
    self.later._workers.dispatch(self.func, deliver=self.__getResult)

  def __getResult(self, result):
    # collect the result and release the capacity
    with self._join_lock:
      self.result = result
      self.done = True
    self.later.capacity.release()
    with self._join_cond:
      self._join_cond.notify_all()

  def __call__(self, *args, **kwargs):
    ''' Calling the object waits for the function to run to completion
        and returns the function return value.
        If the function threw an exception that exception is reraised.
    '''
    assert not args
    assert not kwargs
    result, exc_type, exc_value, exc_traceback = self.wait()
    if exc_type is not None:
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

class Later(object):
  ''' A management class to queue function calls for later execution.
  '''

  def __init__(self, capacity):
    if type(capacity) is int:
      capacity = AdjustableSemaphore(capacity)
    self.capacity = capacity
    self._priority = (0,)
    self._LFPQ = IterablePriorityQueue()   # inbound requests queue
    self._workers = WorkerThreadPool()
    self._dispatchThread = Thread(target=self._dispatcher)
    self._dispatchThread.start()

  def close(self):
    self._LFPQ.close()
    self._dispatchThread.join()

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

  def pdefer(self, priority, func):
    ''' Queue a function for later dispatch.
        Return the corresponding LateFunction for result collection.
	If the parameter `priority` not None then use it as the priority
        otherwise use the default priority.
    '''
    if priority is None:
      priority = self._priority
    elif type(priority) is int:
      priority = (priority,)
    LF = LateFunction(self, func)
    pri_entry = list(priority)
    pri_entry.append(seq())     # ensure FIFO servicing of equal priorities
    pri_entry.append(LF)
    self._LFPQ.put( pri_entry )
    return LF

  def defer(self, func):
    ''' Queue a function for later dispatch using the default priority.
        Return the corresponding LateFunction for result collection.
    '''
    return self.pdefer(None, func)

  def ppartial(self, priority, func, *args, **kwargs):
    ''' Queue a function for later dispatch using the specified priority.
        Return the corresponding LateFunction for result collection.
    '''
    return self.pdefer(priority, partial(func, *args, **kwargs))

  def partial(self, func, *args, **kwargs):
    ''' Queue a function for later dispatch using the default priority.
        Return the corresponding LateFunction for result collection.
    '''
    return self.ppartial(None, func, *args, **kwargs)

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
        TODO: is a thread safe version even a sane idea?
    '''
    oldpri = self._priority
    self._priority = pri
    yield
    self._priority = oldpri

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

if __name__ == '__main__':
  unittest.main()
