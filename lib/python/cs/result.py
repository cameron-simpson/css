#!/usr/bin/python
#
# Result and related classes for asynchronous dispatch and collection.
#       - Cameron Simpson <cs@cskk.id.au>
#

r'''
Result and friends: various subclassable classes for deferred delivery of values.

A `Result` is the base class for several callable subclasses
which will receive values at a later point in time,
and can also be used standalone without subclassing.

A call to a `Result` will block until the value is received or the `Result` is cancelled,
which will raise an exception in the caller.
A `Result` may be called by multiple users, before or after the value has been delivered;
if the value has been delivered the caller returns with it immediately.
A `Result`'s state may be inspected (pending, running, ready, cancelled).
Callbacks can be registered via a `Result`'s .notify method.

An incomplete `Result` can be told to call a function to compute its value;
the function return will be stored as the value unless the function raises an exception,
in which case the exception information is recorded instead.
If an exception occurred, it will be reraised for any caller of the `Result`.

Trite example:

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

You can also collect multiple `Result`s in completion order using the `report()` function:

    Rs = [ ... list of Results of whatever type ... ]
    ...
    for R in report(Rs):
        x = R()     # collect result, will return immediately because
                    # the Result is complete
        print(x)    # print result
'''

import sys
from threading import Lock, RLock

from icontract import require

from cs.deco import OBSOLETE
from cs.fsm import FSM
from cs.gimmicks import exception, warning
from cs.mappings import AttrableMapping
from cs.pfx import pfx_method
from cs.py.func import funcname
from cs.py3 import Queue, raise3, StringTypes
from cs.seq import seq, Seq
from cs.threads import bg as bg_thread

__version__ = '20220805-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.fsm',
        'cs.gimmicks',
        'cs.mappings',
        'cs.pfx',
        'cs.py.func',
        'cs.py3',
        'cs.seq',
        'cs.threads',
        'icontract',
    ],
}

class CancellationError(Exception):
  ''' Raised when accessing `result` or `exc_info` after cancellation.
  '''

  def __init__(self, msg=None):
    if msg is None:
      msg = "cancelled"
    elif not isinstance(msg, StringTypes):
      msg = "%s: cancelled" % (msg,)
    Exception.__init__(self, msg)

# pylint: disable=too-many-instance-attributes
class Result(FSM):
  ''' Base class for asynchronous collection of a result.
      This is used to make `Result`, `OnDemandFunction`s, `LateFunction`s
      and other objects with asynchronous termination.

      In addition to the methods below, for each state value such
      as `self.PENDING` there is a corresponding attribute `is_pending`
      testing whether the `Result` is in that state.
  '''

  _seq = Seq()

  FSM_TRANSITIONS = {
      'PREPARE': {
          'prepared': 'PENDING',
      },
      'PENDING': {
          'dispatch': 'RUNNING',
          'cancel': 'CANCELLED',
          'complete': 'DONE',
      },
      'RUNNING': {
          'cancel': 'CANCELLED',
          'complete': 'DONE',
      },
      'CANCELLED': {},
      'DONE': {},
  }

  # pylint: disable=too-many-arguments
  def __init__(
      self, name=None, lock=None, result=None, state=None, extra=None
  ):
    ''' Base initialiser for `Result` objects and subclasses.

        Parameter:
        * `name`: optional parameter naming this object.
        * `lock`: optional locking object, defaults to a new `threading.Lock`.
        * `result`: if not `None`, prefill the `.result` property.
        * `extra`: an optional mapping of extra information to
          associate with the `Result`, useful to provide context
          when collecting the result; the `Result` has a public
          attribute `.extra` which is an `AttrableMapping` to hold
          this information.
    '''
    if lock is None:
      lock = RLock()
    if name is None:
      name = "%s-%d" % (type(self).__name__, next(self._seq))
    if state is None:
      state = self.PENDING
    self.name = name
    self.extra = AttrableMapping()
    if extra:
      self.extra.update(extra)
    FSM.__init__(self, state, lock=lock)
    self.collected = False
    self._get_lock = Lock()
    self._get_lock.acquire()  # pylint: disable=consider-using-with
    self._lock = lock
    if result is not None:
      self.result = result

  def __str__(self):
    return "%s[%r:%s]" % (type(self).__name__, self.name, self.fsm_state)

  __repr__ = __str__

  def __del__(self):
    if not self.collected:
      if self.is_done:
        exc_info = self.exc_info
        if exc_info:
          raise RuntimeError("UNREPORTED EXCEPTION: %r" % (exc_info,))

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  @property
  @OBSOLETE("fsm_state")
  def state(self):
    ''' The `FSM` state (obsolete).
        Obsolete: use `.fsm_state`.
    '''
    return self.fsm_state

  @property
  def ready(self):
    ''' True if `Result` state is `DONE` or `CANCLLED`..
    '''
    return self.fsm_state in (self.DONE, self.CANCELLED)

  @property
  @OBSOLETE("is_cancelled")
  def cancelled(self):
    ''' Test whether this `Result` has been cancelled.
        Obsolete: use `.is_cancelled`.
    '''
    return self.fsm_state == self.CANCELLED

  @property
  @OBSOLETE("is_pending")
  def pending(self):
    ''' Whether the `Result` is pending.
        Obsolete: use `.is_pending`.
    '''
    return self.fsm_state == self.PENDING

  def empty(self):
    ''' Analogue to `Queue.empty()`.
    '''
    return not self.ready

  def cancel(self):
    ''' Cancel this function.
        If `self.fsm_state` is `PENDING`` or `'CANCELLED'`, return `True`.
        Otherwise return `False` (too late to cancel).
    '''
    with self._lock:
      state = self.fsm_state
      if state == self.CANCELLED:
        # already cancelled - this is ok, no call to ._complete
        return True
      if state == self.DONE:
        # completed - "fail" the cancel, no call to ._complete
        return False
      self.fsm_event('cancel')
    return True

  @property
  def result(self):
    ''' The result.
        This property is not available before completion.
    '''
    state = self.fsm_state
    if state not in (self.CANCELLED, self.DONE):
      raise AttributeError("%s not ready: no .result attribute" % (self,))
    self.collected = True
    if state == self.CANCELLED:
      raise CancellationError
    return self._result

  @result.setter
  def result(self, new_result):
    ''' Set the `.result` attribute, completing the `Result`.
    '''
    self._complete(new_result, None)

  def put(self, value):
    ''' Store the value. `Queue`-like idiom.
    '''
    self.result = value

  @property
  def exc_info(self):
    ''' The exception information from a completed `Result`.
        This is not available before completion.
    '''
    state = self.fsm_state
    if state not in (self.CANCELLED, self.DONE):
      raise AttributeError("%s not ready: no .exc_info attribute" % (self,))
    self.collected = True
    if state == self.CANCELLED:
      self.collected = True
      raise CancellationError
    return self._exc_info

  @exc_info.setter
  def exc_info(self, exc_info):
    self._complete(None, exc_info)

  def raise_(self, exc=None):
    ''' Convenience wrapper for `self.exc_info` to store an exception result `exc`.
        If `exc` is omitted or `None`, uses `sys.exc_info()`.
    '''
    if exc is None:
      self.exc_info = sys.exc_info()
    else:
      try:
        raise exc
      except:  # pylint: disable=bare-except
        self.exc_info = sys.exc_info()

  def run_func(self, func, *a, **kw):
    ''' Have the `Result` run `func(*a,**kw)` and store its return value as
        `self.result`.
        If `func` raises an exception, store it as `self.exc_info`.
    '''
    self.fsm_event('dispatch')
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
    ''' Submit a function to compute the result in a separate `Thread`,
        returning the `Thread`.

        This dispatches a `Thread` to run `self.run_func(func,*a,**kw)`
        and as such the `Result` must be in "pending" state,
        and transitions to "running".
    '''
    return bg_thread(
        self.run_func, name=self.name, args=[func] + list(a), kwargs=kw
    )

  @require(
      lambda self: self.fsm_state in
      (self.PENDING, self.RUNNING, self.CANCELLED)
  )
  def _complete(self, result, exc_info):
    ''' Set the result.
        Alert people to completion.
        Expect to be called _inside_ `self._lock`.
    '''
    if result is not None and exc_info is not None:
      raise ValueError(
          "one of (result, exc_info) must be None, got (%r, %r)" %
          (result, exc_info)
      )
    with self._lock:
      state = self.fsm_state
      if state in (self.PENDING, self.RUNNING, self.CANCELLED):
        self._result = result  # pylint: disable=attribute-defined-outside-init
        self._exc_info = exc_info  # pylint: disable=attribute-defined-outside-init
        if state != self.CANCELLED:
          self.fsm_event('complete')
      elif state == self.DONE:
        warning(
            "<%s>: state is %s, ignoring result=%r, exc_info=%r",
            self,
            self.fsm_state,
            result,
            exc_info,
        )
      else:
        raise RuntimeError(
            "<%s>: state:%s is not one of (PENDING, RUNNING, CANCELLED, DONE)"
            % (self, self.fsm_state)
        )
      self._get_lock.release()

  @pfx_method
  def join(self):
    ''' Calling the `.join()` method waits for the function to run to
        completion and returns a tuple as for the `WorkerThreadPool`'s
        `.dispatch()` return queue, a tuple of `(result,exc_info)`.

        On completion the sequence `(result,None)` is returned.
        If an exception occurred computing the result the sequence
        `(None,exc_info)` is returned
        where `exc_info` is a tuple of `(exc_type,exc_value,exc_traceback)`.
        If the function was cancelled the sequence `(None,None)`
        is returned.
    '''
    self._get_lock.acquire()  # pylint: disable=consider-using-with
    self._get_lock.release()
    return self.result, self.exc_info

  def get(self, default=None):
    ''' Wait for readiness; return the result if `self.exc_info` is `None`,
        otherwise `default`.
    '''
    result, exc_info = self.join()
    if not self.is_cancelled and exc_info is None:
      return result
    return default

  def __call__(self, *a, **kw):
    ''' Call the `Result`: wait for it to be ready and then return or raise.

        You can optionally supply a callable and arguments,
        in which case `callable(*args,**kwargs)` will be called
        via `Result.call` and the results applied to this `Result`.
    '''
    if a:
      self.run_func(*a, **kw)
    if self.is_cancelled:
      raise CancellationError(self)
    result, exc_info = self.join()
    if exc_info:
      raise3(*exc_info)
    return result

  def notify(self, notifier):
    ''' After the function completes, call `notifier(self)`.

        If the function has already completed this will happen immediately.
        example: if you'd rather `self` got put on some Queue `Q`, supply `Q.put`.
    '''

    # TODO: adjust all users of .notify() to use fsm_callback and
    # accept a transition object?
    # pylint: disable=unused-argument
    def callback(fsm, fsm_transition):
      ''' `FSM.fsm_callback` shim for plain `notify(Result)` notifier functions.
      '''
      self.collected = True
      return notifier(fsm)

    with self._lock:
      self.fsm_callback('CANCELLED', callback)
      self.fsm_callback('DONE', callback)
      if self.fsm_state in (self.CANCELLED, self.DONE):
        notifier(self)

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
  ''' A `set` subclass containing `Result`s,
      on which one may iterate as `Result`s complete.
  '''

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
    for _ in self:
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
    R.run_func(func, *a, **kw)
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
        R.run_func(func, *a, **kw)
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
      if self.is_pending:
        self.run_func(self.func, *self.fargs, **self.fkwargs)
    return super().__call__()

OnDemandFunction = OnDemandResult

if __name__ == '__main__':
  import cs.result_tests
  cs.result_tests.selftest(sys.argv)
