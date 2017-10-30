#!/usr/bin/python
#
# Result and related classes for asynchronous dispatch and collection.
#       - Cameron Simpson <cs@cskk.id.au>
#

r'''
Result and friends.

A Result is the base class for several callable subclasses
which will receive values at a later point in time,
and can also be used standalone without subclassing.

A call to a Result will block until the value is received or the Result is cancelled,
which will raise an exception in the caller.
A Result may be called by multiple users, before or after the value has been delivered;
if the value has been delivered the caller returns with it immediately.
A Result's state may be inspected (pending, running, ready, cancelled).
Callbacks can be registered via an Asychron's .notify method.

An incomplete Result can be told to call a function to compute its value;
the function return will be stored as the value unless the function raises an exception,
in which case the exception information is recorded instead.
If an exception occurred, it will be reraised for any caller of the Result.

Trite example::

  R = Result(name="my demo")

  Thread 1:
    value = R()
    # blocks...
    print(value)
    # prints 3 once Thread 2 (below) assigns to it

  Thread 2:
    R.result = 3

  Thread 3:
    value = R()
    # returns immediately with 3

You can also collect multiple Results in completion order using the report() function::

  Rs = [ ... list of Results or whatever type ... ]
  ...
  for R in report(Rs):
    x = R()     # collect result, will return immediately
    print(x)    # print result
'''

from functools import partial
import sys
from threading import Lock, Thread
from cs.logutils import exception, warning, debug
from cs.obj import O
from cs.seq import seq
from cs.py3 import Queue, raise3, StringTypes

DISTINFO = {
    'description': "Result and friends: callable objects which will receive a value at a later point in time.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.obj', 'cs.seq', 'cs.py3'],
}

class AsynchState(object):
  ''' State tokens for Results.
  '''
  pending = 'pending'
  running = 'running'
  ready = 'ready'
  cancelled = 'cancelled'

class CancellationError(RuntimeError):
  ''' Raised when accessing result or exc_info after cancellation.
  '''

  def __init__(self, msg=None):
    if msg is None:
      msg = "cancelled"
    elif not isinstance(msg, StringTypes):
      msg = "%s: cancelled" % (msg,)
    RuntimeError.__init__(msg)

class Result(O):
  ''' Basic class for asynchronous collection of a result.
      This is also used to make OnDemandFunctions, LateFunctions and other
      objects with asynchronous termination.
  '''

  def __init__(self, name=None, final=None, lock=None, result=None):
    ''' Base initialiser for Result objects and subclasses.
        `name`: optional paramater to name this object.
        `final`: a function to run after completion of the asynchron,
                 regardless of the completion mode (result, exception,
                 cancellation).
        `lock`: optional locking object, defaults to a new Lock
        `result`: if not None, prefill the .result property
    '''
    O.__init__(self)
    self._O_omit.extend(['result', 'exc_info'])
    if lock is None:
      lock = Lock()
    if name is None:
      name = "%s-%d" % (self.__class__.__name__, seq(),)
    self.name = name
    self.final = final
    self.state = AsynchState.pending
    self.notifiers = []
    self._get_lock = Lock()
    self._get_lock.acquire()
    self._lock = lock
    if result is not None:
      self.result = result

  def __str__(self):
    return "%s[%s]{%s}" % (self.__class__.__name__, self.name, self.state)
  __repr__ = __str__

  @property
  def ready(self):
    state = self.state
    return state == AsynchState.ready or state == AsynchState.cancelled

  @property
  def cancelled(self):
    ''' Test whether this Result has been cancelled.
    '''
    return self.state == AsynchState.cancelled

  @property
  def pending(self):
    return self.state == AsynchState.pending

  def empty(self):
    ''' Analogue to Queue.empty().
    '''
    return not self.ready

  def cancel(self):
    ''' Cancel this function.
        If self.state is AsynchState.pending or AsynchState.cancelled, return True.
        Otherwise return False (too late to cancel).
    '''
    with self._lock:
      state = self.state
      if state == AsynchState.cancelled:
        # already cancelled - this is ok, no call to ._complete
        return True
      if state == AsynchState.ready:
        # completed - "fail" the cancel, no call to ._complete
        return False
      if state == AsynchState.running or state == AsynchState.pending:
        # in progress or not commenced - change state to cancelled and fall through to ._complete
        state = AsynchState.cancelled
      else:
        # state error
        raise RuntimeError(
            "<%s>.state not one of (AsynchState.pending, AsynchState.cancelled, AsynchState.running, AsynchState.ready): %r", self, state)
      self._complete(None, None)
    return True

  @property
  def result(self):
    state = self.state
    if state == AsynchState.cancelled:
      raise CancellationError()
    if state == AsynchState.ready:
      return self._result
    raise AttributeError("%s not ready: no .result attribute" % (self,))

  @result.setter
  def result(self, new_result):
    with self._lock:
      self._complete(new_result, None)

  def put(self, value):
    ''' Store the value. Queue-like idiom.
    '''
    self.result = value

  @property
  def exc_info(self):
    state = self.state
    if state == AsynchState.cancelled:
      raise CancellationError()
    if state == AsynchState.ready:
      return self._exc_info
    raise AttributeError("%s not ready: no .exc_info attribute" % (self,))

  @exc_info.setter
  def exc_info(self, exc_info):
    with self._lock:
      self._complete(None, exc_info)

  def raise_(self, exc=None):
    ''' Convenience wrapper for self.exc_info to store an exception result `exc`.
        If `exc` is omitted or None, use sys.exc_info().
    '''
    if exc is None:
      self.exc_info = sys.exc_info()
    else:
      try:
        raise exc
      except:
        self.exc_info = sys.exc_info()

  def call(self, func, *a, **kw):
    ''' Have the Result call `func(*a,**kw)` and store its values as
        self.result.
        If `func` raises an exception, store it as self.exc_info.
    '''
    try:
      r = func(*a, **kw)
    except Exception:
      self.exc_info = sys.exc_info()
    else:
      self.result = r

  def bg(self, func, *a, **kw):
    ''' Submit a function to compute the result in a separate Thread, returning the Thread.
        The Result must be in "pending" state, and transitions to "running".
    '''
    with self._lock:
      state = self.state
      if state != AsynchState.pending:
        raise RuntimeError("<%s>.state is not AsynchState.pending, rejecting background function call of %s" % (self, func))
      T = Thread(name="<%s>.bg(func=%s,...)" % (self, func), target=self.call, args=[func] + list(a), kwargs=kw)
      self.state = AsynchState.running
    T.start()
    return T

  def _complete(self, result, exc_info):
    ''' Set the result.
        Alert people to completion.
        Expect to be called _inside_ self._lock.
    '''
    if result is not None and exc_info is not None:
      raise ValueError(
          "one of (result, exc_info) must be None, got (%r, %r)" % (result, exc_info))
    state = self.state
    if state == AsynchState.cancelled or state == AsynchState.running or state == AsynchState.pending:
      self._result = result
      self._exc_info = exc_info
      if state != AsynchState.cancelled:
        self.state = AsynchState.ready
    else:
      if state == AsynchState.ready:
        warning("<%s>.state is AsynchState.ready, ignoring result=%r, exc_info=%r",
                self, result, exc_info)
        raise RuntimeError(
            "REPEATED _COMPLETE of %s: result=%r, exc_info=%r"
            % (self, result, exc_info)
        )
      raise RuntimeError(
          "<%s>.state is not one of (AsynchState.cancelled, AsynchState.running, AsynchState.pending, AsynchState.ready): %r"
          % (self, state)
      )
    if self.final is not None:
      try:
        final_result = self.final()
      except Exception as e:
        exception("%s: exception from .final(): %s", self.name, e)
      else:
        if final_result is not None:
          warning(
              "%s: discarding non-None result from .final(): %r", self.name, final_result)
    self._get_lock.release()
    notifiers = self.notifiers
    del self.notifiers
    for notifier in notifiers:
      debug("%s._complete: notify via %r", self, notifier)
      try:
        notifier(self)
      except Exception as e:
        exception(
            "%s._complete: calling notifier %s: exc=%s", self, notifier, e)

  def join(self):
    ''' Calling the .join() method waits for the function to run to
        completion and returns a tuple as for the WorkerThreadPool's
        .dispatch() return queue, a tuple of:
          result, exc_info
        On completion the sequence:
          result, None
        is returned.
        If an exception occurred computing the result the sequence:
          None, exc_info
        is returned where exc_info is a tuple of (exc_type, exc_value, exc_traceback).
        If the function was cancelled the sequence:
          None, None
        is returned.
    '''
    self._get_lock.acquire()
    self._get_lock.release()
    return (self._result, self._exc_info)

  def get(self, default=None):
    ''' Wait for readiness; return the result if exc_info is None, otherwise `default`.
    '''
    result, exc_info = self.join()
    if not self.cancelled and exc_info is None:
      return result
    return default

  def __call__(self):
    result, exc_info = self.join()
    if self.cancelled:
      raise CancellationError(self)
    if exc_info:
      raise3(*exc_info)
    return result

  def notify(self, notifier):
    ''' After the function completes, run notifier(self).
        If the function has already completed this will happen immediately.
        Note: if you'd rather `self` got put on some Queue `Q`, supply `Q.put`.
    '''
    with self._lock:
      if not self.ready:
        self.notifiers.append(notifier)
        notifier = None
    if notifier is not None:
      notifier(self)

def report(LFs):
  ''' Generator which yields completed Results.
      This is a generator that yields Results as they complete, useful
      for waiting for a sequence of Results that may complete in an
      arbitrary order.
  '''
  Q = Queue()
  n = 0
  notify = Q.put
  for LF in LFs:
    n += 1
    LF.notify(notify)
  for _ in range(n):
    yield Q.get()

def after(Rs, R, func, *a, **kw):
  ''' After the completion of `Rs` call `func(*a, **kw)` and return its result via `R`; return the Result object.
      `Rs`: an iterable of Results.
      `R`: a Result to collect to result of calling `func`. If None,
           one will be created.
      `func`, `a`, `kw`: a callable and its arguments.
  '''
  if R is None:
    R = Result("after-%d" % (seq(),))
  elif not isinstance(R, Result):
    raise TypeError("after(Rs, R, func, ...): expected Result for R, got %r" % (R,))
  lock = Lock()
  Rs = list(Rs)
  count = len(Rs)
  if count == 0:
    R.call(func, *a, **kw)
  else:
    countery = [count]  # to stop "count" looking like a local var inside the closure
    def count_down(subR):
      ''' Notification function to submit `func` after sufficient invocations.
      '''
      with lock:
        countery[0] -= 1
        count = countery[0]
      if count > 0:
        # not ready yet
        return
      if count == 0:
        R.call(func, *a, **kw)
      else:
        raise RuntimeError("count < 0: %d", count)
    # submit the notifications
    for subR in Rs:
      subR.notify(count_down)
  return R

class _PendingFunction(Result):
  ''' An Result with a callable used to obtain its result.
      Since nothing triggers the function call this is an abstract class.
  '''

  def __init__(self, func, *a, **kw):
    final = kw.pop('final', None)
    Result.__init__(self, final=final)
    if a or kw:
      func = partial(func, *a, **kw)
    self.func = func

class OnDemandFunction(_PendingFunction):
  ''' Wrap a callable, run it when required.
  '''

  def __call__(self):
    with self._lock:
      state = self.state
      if state == AsynchState.cancelled:
        raise CancellationError()
      if state == AsynchState.pending:
        self.state = AsynchState.running
      else:
        raise RuntimeError("state should be AsynchState.pending but is %s" % (self.state))
    result, exc_info = None, None
    try:
      result = self.func()
    except Exception:
      exc_info = sys.exc_info()
      self.exc_info = exc_info
      raise
    else:
      self.result = result
    return result

if __name__ == '__main__':
  import cs.result_tests
  cs.result_tests.selftest(sys.argv)
