#!/usr/bin/python
#
# Result and related classes for asynchronous dispatch and collection.
#       - Cameron Simpson <cs@cskk.id.au>
#

r'''
Result and friends: various classable classes for deferred delivery of values.

A Result is the base class for several callable subclasses
which will receive values at a later point in time,
and can also be used standalone without subclassing.

A call to a Result will block until the value is received or the Result is cancelled,
which will raise an exception in the caller.
A Result may be called by multiple users, before or after the value has been delivered;
if the value has been delivered the caller returns with it immediately.
A Result's state may be inspected (pending, running, ready, cancelled).
Callbacks can be registered via a Result's .notify method.

An incomplete Result can be told to call a function to compute its value;
the function return will be stored as the value unless the function raises an exception,
in which case the exception information is recorded instead.
If an exception occurred, it will be reraised for any caller of the Result.

Trite example::

  R = Result(name="my demo")

  Thread 1:
    # this blocks until the Result is ready
    value = R()
    print(value)
    # prints 3 once Thread 2 (below) assigns to it

  Thread 2:
    R.result = 3

  Thread 3:
    value = R()
    # returns immediately with 3

You can also collect multiple Results in completion order using the report() function::

  Rs = [ ... list of Results of whatever type ... ]
  ...
  for R in report(Rs):
    x = R()     # collect result, will return immediately because
                # the Result is complete
    print(x)    # print result
'''

try:
  from enum import Enum
except ImportError:
  from enum34 import Enum  # type: ignore
import sys
from threading import Lock, RLock
from icontract import require
from cs.logutils import exception, error, warning, debug
from cs.mappings import AttrableMapping
from cs.pfx import Pfx, pfx_method
from cs.py.func import funcname
from cs.py3 import Queue, raise3, StringTypes
from cs.seq import seq
from cs.threads import bg as bg_thread

__version__ = '20210123-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.logutils', 'cs.pfx', 'cs.py.func', 'cs.py3', 'cs.seq',
        'cs.threads', 'icontract'
    ],
}

class ResultState(Enum):
  ''' State tokens for `Result`s.
  '''
  pending = 'pending'
  running = 'running'
  ready = 'ready'
  cancelled = 'cancelled'

# compatability name
AsynchState = ResultState

class CancellationError(Exception):
  ''' Raised when accessing result or exc_info after cancellation.
  '''

  def __init__(self, msg=None):
    if msg is None:
      msg = "cancelled"
    elif not isinstance(msg, StringTypes):
      msg = "%s: cancelled" % (msg,)
    Exception.__init__(self, msg)

# pylint: disable=too-many-instance-attributes
class Result(object):
  ''' Basic class for asynchronous collection of a result.
      This is also used to make `OnDemandFunction`s, `LateFunction`s and other
      objects with asynchronous termination.
  '''

  def __init__(self, name=None, lock=None, result=None, extra=None):
    ''' Base initialiser for `Result` objects and subclasses.

        Parameter:
        * `name`: optional parameter naming this object.
        * `lock`: optional locking object, defaults to a new `threading.Lock`.
        * `result`: if not `None`, prefill the `.result` property.
        * `extra`: a mapping of extra information to associate with the `Result`,
          useful to provide context when collecting the result;
          the `Result` has a public attribute `.extra`
          which is an `AttrableMapping` to hold this information.
    '''
    if lock is None:
      lock = RLock()
    if name is None:
      name = "%s-%d" % (type(self).__name__, seq())
    self.name = name
    self.extra = AttrableMapping()
    if extra:
      self.extra.update(extra)
    self.state = ResultState.pending
    self.notifiers = []
    self.collected = False
    self._get_lock = Lock()
    self._get_lock.acquire()
    self._lock = lock
    if result is not None:
      self.result = result

  def __str__(self):
    return "%s[%r:%s]" % (type(self).__name__, self.name, self.state)

  __repr__ = __str__

  def __del__(self):
    if not self.collected:
      if self.ready:
        exc_info = self.exc_info
        if exc_info:
          raise RuntimeError("UNREPORTED EXCEPTION: %r" % (exc_info,))

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  @property
  def ready(self):
    ''' Whether the Result state is ready or cancelled.
    '''
    return self.state in (ResultState.ready, ResultState.cancelled)

  @property
  def cancelled(self):
    ''' Test whether this Result has been cancelled.
    '''
    return self.state == ResultState.cancelled

  @property
  def pending(self):
    ''' Whether the Result is pending.
    '''
    return self.state == ResultState.pending

  def empty(self):
    ''' Analogue to Queue.empty().
    '''
    return not self.ready

  def cancel(self):
    ''' Cancel this function.
        If self.state is pending or cancelled, return True.
        Otherwise return False (too late to cancel).
    '''
    with self._lock:
      state = self.state
      if state == ResultState.cancelled:
        # already cancelled - this is ok, no call to ._complete
        return True
      if state == ResultState.ready:
        # completed - "fail" the cancel, no call to ._complete
        return False
      if state in (ResultState.running, ResultState.pending):
        # in progress or not commenced - change state to cancelled and fall through to ._complete
        self.state = ResultState.cancelled
      else:
        # state error
        raise RuntimeError(
            "<%s>.state not one of (pending, cancelled, running, ready): %r" %
            (self, state)
        )
      self._complete(None, None)
    return True

  @property
  def result(self):
    ''' The result.
        This property is not available before completion.
    '''
    state = self.state
    if state == ResultState.cancelled:
      self.collected = True
      raise CancellationError()
    if state == ResultState.ready:
      self.collected = True
      return self._result
    raise AttributeError("%s not ready: no .result attribute" % (self,))

  @result.setter
  def result(self, new_result):
    ''' Set the .result attribute, completing the Result.
    '''
    with self._lock:
      self._complete(new_result, None)

  def put(self, value):
    ''' Store the value. Queue-like idiom.
    '''
    self.result = value

  @property
  def exc_info(self):
    ''' The exception information from a completed Result.
        This is not available before completion.
    '''
    state = self.state
    if state == ResultState.cancelled:
      self.collected = True
      raise CancellationError()
    if state == ResultState.ready:
      self.collected = True
      return self._exc_info
    raise AttributeError("%s not ready: no .exc_info attribute" % (self,))

  @exc_info.setter
  def exc_info(self, exc_info):
    with self._lock:
      self._complete(None, exc_info)

  def raise_(self, exc=None):
    ''' Convenience wrapper for `self.exc_info` to store an exception result `exc`.
        If `exc` is omitted or `None`, use `sys.exc_info()`.
    '''
    if exc is None:
      self.exc_info = sys.exc_info()
    else:
      try:
        raise exc
      except:  # pylint: disable=bare-except
        self.exc_info = sys.exc_info()

  @require(lambda self: self.state == ResultState.pending)
  def call(self, func, *a, **kw):
    ''' Have the `Result` call `func(*a,**kw)` and store its return value as
        `self.result`.
        If `func` raises an exception, store it as `self.exc_info`.
    '''
    self.state = ResultState.running
    try:
      r = func(*a, **kw)
    except BaseException:
      self.exc_info = sys.exc_info()
    except:  # pylint: disable=bare-except
      exception("%s: unexpected exception: %r", func, sys.exc_info())
      self.exc_info = sys.exc_info()
    else:
      self.result = r

  def bg(self, func, *a, **kw):
    ''' Submit a function to compute the result in a separate `Thread`;
        returning the `Thread`.

        This dispatches a `Thread` to run `self.call(func,*a,**kw)`
        and as such the `Result` must be in "pending" state,
        and transitions to "running".
    '''
    return bg_thread(
        self.call, name=self.name, args=[func] + list(a), kwargs=kw
    )

  @require(
      lambda self: self.state in
      (ResultState.pending, ResultState.running, ResultState.cancelled)
  )
  def _complete(self, result, exc_info):
    ''' Set the result.
        Alert people to completion.
        Expect to be called _inside_ self._lock.
    '''
    if result is not None and exc_info is not None:
      raise ValueError(
          "one of (result, exc_info) must be None, got (%r, %r)" %
          (result, exc_info)
      )
    state = self.state
    if state in (ResultState.cancelled, ResultState.running,
                 ResultState.pending):
      self._result = result
      self._exc_info = exc_info
      if state != ResultState.cancelled:
        self.state = ResultState.ready
    else:
      if state == ResultState.ready:
        warning(
            "<%s>.state is ResultState.ready, ignoring result=%r, exc_info=%r",
            self, result, exc_info
        )
        raise RuntimeError(
            "REPEATED _COMPLETE of %s: result=%r, exc_info=%r" %
            (self, result, exc_info)
        )
      raise RuntimeError(
          "<%s>.state is not one of (cancelled, running, pending, ready): %r" %
          (self, state)
      )
    self._get_lock.release()
    notifiers = self.notifiers
    del self.notifiers
    for notifier in notifiers:
      debug("%s._complete: notify via %r", self, notifier)
      try:
        notifier(self)
      except Exception as e:  # pylint: disable=broad-except
        exception(
            "%s._complete: calling notifier %s: exc=%s", self, notifier, e
        )
      else:
        self.collected = True

  @pfx_method
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
    return self.result, self.exc_info

  def get(self, default=None):
    ''' Wait for readiness; return the result if exc_info is None, otherwise `default`.
    '''
    result, exc_info = self.join()
    if not self.cancelled and exc_info is None:
      return result
    return default

  def __call__(self, *a, **kw):
    ''' Call the result: wait for it to be ready and then return or raise.

        You can optionally supply a callable and arguments,
        in which case `callable(*args,**kwargs)` will be called
        via `Result.call` and the results applied to this Result.
    '''
    if a:
      if not self.pending:
        raise RuntimeError("calling complete %s" % (type(self).__name__,))
      self.call(*a, **kw)
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
      self.collected = True

  def with_result(self, submitter, prefix=None):
    ''' On completion without an exception, call `submitter(self.result)` or report exception.
    '''

    def notifier(R):
      ''' Wrapper for `submitter`.
      '''
      exc_info = R.exc_info
      if exc_info is None:
        return submitter(R.result)
      # report error
      if prefix:
        with Pfx(prefix):
          error("exception: %r", exc_info)
      else:
        error("exception: %r", exc_info)
      return None

    self.notify(notifier)

def bg(func, *a, **kw):
  ''' Dispatch a `Thread` to run `func`, return a `Result` to collect its value.

      Parameters:
      * `_name`: optional name for the `Result`, passed to the initialiser
      * `_extra`: optional extra data for the `Result`, passed to the initialiser

      Other parameters are passed to `func`.
  '''
  name = kw.pop('_name', None)
  extra = kw.pop('_extra', None)
  R = Result(name=name, extra=extra)
  R.bg(func, *a, **kw)
  return R

def report(LFs):
  ''' Generator which yields completed `Result`s.

      This is a generator that yields `Result`s as they complete,
      useful for waiting for a sequence of `Result`s
      that may complete in an arbitrary order.
  '''
  Q = Queue()
  n = 0
  notify = Q.put
  for LF in LFs:
    n += 1
    LF.notify(notify)
  for _ in range(n):
    yield Q.get()

class ResultSet(set):

  def __enter__(self):
    return self

  def __exit__(self, *_):
    pass

  def __iter__(self):
    ''' Iterating on a `ResultSet` yields `Result`s as they complete.
    '''
    for R in report(super().__iter__()):
      yield R

  def wait(self):
    ''' Convenience function to wait for all the `Result`s.
    '''
    for R in self:
      pass

def after(Rs, R, func, *a, **kw):
  ''' After the completion of `Rs` call `func(*a,**kw)` and return
      its result via `R`; return the `Result` object.

      Parameters:
      * `Rs`: an iterable of Results.
      * `R`: a `Result` to collect to result of calling `func`.
        If `None`, one will be created.
      * `func`, `a`, `kw`: a callable and its arguments.
  '''
  if R is None:
    R = Result("after-%d" % (seq(),))
  elif not isinstance(R, Result):
    raise TypeError(
        "after(Rs, R, func, ...): expected Result for R, got %r" % (R,)
    )
  lock = Lock()
  Rs = list(Rs)
  count = len(Rs)
  if count == 0:
    R.call(func, *a, **kw)
  else:
    countery = [
        count
    ]  # to stop "count" looking like a local var inside the closure

    def count_down(_):
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
        raise RuntimeError("count < 0: %d" % (count,))

    # submit the notifications
    for subR in Rs:
      subR.notify(count_down)
  return R

class OnDemandResult(Result):
  ''' Wrap a callable, run it when required.
  '''

  def __init__(self, func, *fargs, **fkwargs):
    Result.__init__(self)
    self.func = func
    self.fargs = fargs
    self.fkwargs = fkwargs

  def __str__(self):
    s = super().__str__() + ":func=%s" % (funcname(self.func),)
    if self.fargs:
      s += ":fargs=%r" % (self.fargs,)
    if self.fkwargs:
      s += ":fkwargs=%r" % (self.fkwargs,)
    return s

  def __call__(self, *a, **kw):
    if a or kw:
      raise ValueError(
          "%s.__call__: no parameters expected, received: *%r, **%r" %
          (self, a, kw)
      )
    with self._lock:
      if self.state == ResultState.pending:
        self.call(self.func, *self.fargs, **self.fkwargs)
    return super().__call__()

OnDemandFunction = OnDemandResult

if __name__ == '__main__':
  import cs.result_tests
  cs.result_tests.selftest(sys.argv)
