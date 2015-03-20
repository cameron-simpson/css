#!/usr/bin/python
#
# Asynchron and related classes.
#       - Cameron Simpson <cs@zip.com.au>
#

DISTINFO = {
    'description': "Asynchron and friends: callable objects which will receive a value at a later point in time.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'requires': ['cs.obj', 'cs.seq', 'cs.py3'],
}

import sys
from cs.debug import Lock
from cs.logutils import error, exception, warning, debug, D
from cs.obj import O
from cs.seq import seq
from cs.py3 import Queue, raise3

ASYNCH_PENDING = 0       # result not ready or considered
ASYNCH_RUNNING = 1       # result function running
ASYNCH_READY = 2         # result computed
ASYNCH_CANCELLED = 3     # result computation cancelled

class CancellationError(RuntimeError):

  ''' Raised when accessing result or exc_info after cancellation.
  '''

  def __init__(self, msg="cancelled"):
    RuntimeError.__init__(msg)

class Asynchron(O):

  ''' Common functionality for Results, LateFunctions and other
      objects with asynchronous termination.
  '''

  def __init__(self, name=None, final=None):
    ''' Base initialiser for Asynchron objects and subclasses.
        `name`: optional paramater to name this object.
        `final`: a function to run after completion of the asynchron,
                 regardless of the completion mode (result, exception,
                 cancellation).
    '''
    O.__init__(self)
    self._O_omit.extend(['result', 'exc_info'])
    if name is None:
      name = "%s-%d" % (self.__class__.__name__, seq(),)
    self.name = name
    self.final = final
    self.state = ASYNCH_PENDING
    self.notifiers = []
    self._get_lock = Lock()
    self._get_lock.acquire()
    self._lock = Lock()

  def __repr__(self):
    return str(self)

  @property
  def ready(self):
    state = self.state
    return state == ASYNCH_READY or state == ASYNCH_CANCELLED

  @property
  def cancelled(self):
    ''' Test whether this Asynchron has been cancelled.
    '''
    return self.state == ASYNCH_CANCELLED

  @property
  def pending(self):
    return self.state == ASYNCH_PENDING

  def empty(self):
    ''' Analogue to Queue.empty().
    '''
    return not self.ready

  def cancel(self):
    ''' Cancel this function.
        If self.state is ASYNCH_PENDING or ASYNCH_CANCELLED, return True.
        Otherwise return False (too late to cancel).
    '''
    with self._lock:
      state = self.state
      if state == ASYNCH_CANCELLED:
        return True
      if state == ASYNCH_READY:
        return False
      if state == ASYNCH_RUNNING or state == ASYNCH_PENDING:
        state = ASYNCH_CANCELLED
      else:
        raise RuntimeError(
            "<%s>.state not one of (ASYNCH_PENDING, ASYNCH_CANCELLED, ASYNCH_RUNNING, ASYNCH_READY): %r", self, state)
    self._complete(None, None)
    return True

  @property
  def result(self):
    with self._lock:
      state = self.state
      if state == ASYNCH_CANCELLED:
        raise CancellationError()
      if state == ASYNCH_READY:
        return self._result
    raise AttributeError("%s not ready: no .result attribute" % (self,))

  @result.setter
  def result(self, new_result):
    self._complete(new_result, None)

  def put(self, value):
    ''' Store the value. Queue-like idiom.
    '''
    self.result = value

  @property
  def exc_info(self):
    with self._lock:
      state = self.state
      if state == ASYNCH_CANCELLED:
        raise CancellationError()
      if state == ASYNCH_READY:
        return self._exc_info
    raise AttributeError("%s not ready: no .exc_info attribute" % (self,))

  @exc_info.setter
  def exc_info(self, exc_info):
    self._complete(None, exc_info)

  def call(self, func, *a, **kw):
    ''' Have the Asynchron call `func(*a,**kw)` and store its values as
        self.result.
        If `func` raises an exception, store it as self.exc_info.
    '''
    try:
      r = func(*a, **kw)
    except:
      self.exc_info = sys.exc_info
    else:
      self.result = r

  def _complete(self, result, exc_info):
    ''' Set the result.
        Alert people to completion.
    '''
    if result is not None and exc_info is not None:
      raise ValueError(
          "one of (result, exc_info) must be None, got (%r, %r)" % (result, exc_info))
    with self._lock:
      state = self.state
      if state == ASYNCH_CANCELLED or state == ASYNCH_RUNNING or state == ASYNCH_PENDING:
        self._result = result
        self._exc_info = exc_info
        if state != ASYNCH_CANCELLED:
          self.state = ASYNCH_READY
      else:
        if state == ASYNCH_READY:
          warning("<%s>.state is ASYNCH_READY, ignoring result=%r, exc_info=%r",
                  self, result, exc_info)
          return
        else:
          raise RuntimeError("<%s>.state is not one of (ASYNCH_CANCELLED, ASYNCH_RUNNING, ASYNCH_PENDING, ASYNCH_READY): %r"
                             % (self, state))
        return
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
    ''' Wait for the readiness.
        Return the result if exc_info is None, otherwise `default`.
    '''
    result, exc_info = self.join()
    if not self.cancelled and exc_info is None:
      return result
    return default

  def __call__(self):
    result, exc_info = self.join()
    if self.cancelled:
      raise RuntimeError("%s: cancelled", self)
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
  ''' Report completed Asynchrons.
      This is a generator that yields Asynchrons as they complete, useful
      for waiting for a sequence of Asynchrons that may complete in an
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

Result = Asynchron

if __name__ == '__main__':
  import cs.asynchron_tests
  cs.asynchron_tests.selftest(sys.argv)
