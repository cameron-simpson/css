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

from queue import Queue
import sys
from threading import Lock, RLock, Thread
from typing import Callable, Optional

from cs.deco import OBSOLETE, decorator
from cs.fsm import FSM, CancellationError
from cs.gimmicks import exception, warning
from cs.mappings import AttrableMapping
from cs.pfx import pfx_method
from cs.py.func import funcname, func_a_kw_fmt
from cs.seq import seq, Seq
from cs.threads import bg as bg_thread

__version__ = '20250103'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.fsm',
        'cs.gimmicks',
        'cs.mappings',
        'cs.pfx',
        'cs.py.func',
        'cs.seq',
        'cs.threads',
    ],
}

@decorator
def not_cancelled(method):
  ''' A decorator for methods to raise `CancellationError` if `self.cancelled`.
  '''

  def if_not_cancelled(self, *a, **kw):
    if self.cancelled:
      raise CancellationError(
          f'{self}.cancelled, not calling {funcname(method)}'
      )
    return method(self, *a, **kw)

  return if_not_cancelled

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
      'PENDING': {
          'dispatch': 'RUNNING',
          'cancel': 'CANCELLED',
          'complete': 'DONE',
      },
      'RUNNING': {
          'cancel': 'CANCELLED',
          'complete': 'DONE',
      },
      'CANCELLED': {
          'cancel': 'CANCELLED',
      },
      'DONE': {
          'cancel': 'DONE',
      },
  }

  # pylint: disable=too-many-arguments
  def __init__(
      self,
      name=None,
      *,
      lock=None,
      result=None,
      state=None,
      extra=None,
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
    self.name = name
    self.extra = AttrableMapping()
    if extra:
      self.extra.update(extra)
    FSM.__init__(self, state, lock=lock)
    self.collected = False
    # the result collection lock
    self._get_lock = Lock()
    self._get_lock.acquire()  # pylint: disable=consider-using-with
    self.__lock = lock
    # internal fields
    self._result = None
    self._exc_info = None
    self._cancel_msg = None
    if result is not None:
      self.result = result

  def __str__(self):
    return "%s[%r:%s]" % (
        type(self).__name__,
        self.__dict__.get('name', 'NO_NAME'),
        self.__dict__.get('fsm_state', 'NO_FSM_STATE'),
    )

  __repr__ = __str__

  def __del__(self):
    if not getattr(self, 'collected', False):
      if self.is_done:
        exc_info = self.exc_info
        if exc_info:
          warning("UNREPORTED EXCEPTION at __del__: %r", exc_info)

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

  def _complete(self, result, exc_info):
    ''' Set the result or exception.
        Alert people to completion.
    '''
    if result is not None and exc_info is not None:
      raise ValueError(
          "one of (result, exc_info) must be None, got (%r, %r)" %
          (result, exc_info)
      )
    with self.__lock:
      if self.ready:
        raise RuntimeError(f'%s already completed')
      self._result = result  # pylint: disable=attribute-defined-outside-init
      self._exc_info = exc_info  # pylint: disable=attribute-defined-outside-init
      self.fsm_event('complete')
      self._get_lock.release()

  @property
  def ready(self):
    ''' True if the `Result` state is `DONE` or `CANCELLED`..
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

  def cancel(self, msg: Optional[str] = None):
    ''' Cancel this `Result`.
    '''
    self._cancel_msg = msg
    self.fsm_event('cancel')

  @property
  def result(self):
    ''' The result.
        This property is not available before completion.
        Accessing this on a cancelled `Result` raises `CancellationError`.
    '''
    state = self.fsm_state
    if state == 'DONE':
      self.collected = True
      return self._result
    if state == 'CANCELLED':
      self.collected = True
      raise CancellationError(
          self._cancel_msg or ".result: cancelled", fsm=self
      )
    raise AttributeError(f'.result: {self} not ready')

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
        Accessing this on a cancelled `Result` raises `CancellationError`.
    '''
    state = self.fsm_state
    if state == 'DONE':
      self.collected = True
      return self._exc_info
    if state == 'CANCELLED':
      self.collected = True
      raise CancellationError(".exc_info: cancelled", fsm=self)
    raise AttributeError(f'.exc_info: {self} not ready')

  @exc_info.setter
  def exc_info(self, exc_info):
    self._complete(None, exc_info)

  def raise_(self, exc=None):
    ''' Convenience wrapper for `self.exc_info` to store an exception result `exc`.
        If `exc` is omitted or `None`, uses `sys.exc_info()`.

        Examples:

            # complete the result using the current exception state
            R.raise_()

            # complete the result with an exception type
            R.raise_(RuntimeError)

            # complete the result with an exception
            R.raise_(ValueError("bad value!"))
    '''
    if exc is None:
      self.exc_info = sys.exc_info()
    else:
      try:
        raise exc
      except:  # pylint: disable=bare-except
        self.exc_info = sys.exc_info()

  def run_func(self, func, *a, **kw):
    ''' Fulfil the `Result` by running `func(*a,**kw)`.
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

        Keyword arguments for `cs.threads.bg` may be supplied by
        prefixing their names with an underscore, for example:

            T = R.bg(mainloop, _pre_enter_objects=(S, fs))

        This dispatches a `Thread` to run `self.run_func(func,*a,**kw)`
        and as such the `Result` must be in "pending" state,
        and transitions to "running".
    '''
    bg_kw = {}
    for k in list(kw.keys()):
      if k.startswith('_'):
        bg_kw[k[1:]] = kw.pop(k)
    return bg_thread(
        self.run_func,
        name=self.name,
        args=[func] + list(a),
        kwargs=kw,
        **bg_kw,
    )

  def run_func_in_thread(self, func, *a, **kw):
    ''' Fulfil the `Result` by running `func(*a,**kw)`
        in a separate `Thread`.

        This exists to step out of the current `Thread's` thread
        local context, such as a database transaction associated
        with Django's implicit per-`Thread` database context.
    '''
    T = self.bg(func, *a, **kw)
    T.join()
    return self()

  @pfx_method
  def join(self):
    ''' Calling the `.join()` method waits for the function to run to
        completion and returns a tuple of `(result,exc_info)`.

        On completion the sequence `(result,None)` is returned.
        If an exception occurred computing the result the sequence
        `(None,exc_info)` is returned
        where `exc_info` is a tuple of `(exc_type,exc_value,exc_traceback)`.
        If the function was cancelled the sequence `(None,None)`
        is returned.
    '''
    self._get_lock.acquire()  # pylint: disable=consider-using-with
    self._get_lock.release()
    return self._result, self._exc_info

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

        Basic example:

            R = Result()
            ... hand R to something which will fulfil it later ...
            x = R() # wait for fulfilment - value lands in x

        Direct call:

            R = Result()
            ... pass R to something which wants the result ...
            # call func(1,2,z=3), save result in R
            # ready for collection by whatever received R
            R(func,1,2,z=3)
    '''
    if a:
      self.run_func(*a, **kw)
    if self.is_cancelled:
      raise CancellationError(self, fsm=self)
    result, exc_info = self.join()
    if exc_info:
      _, exc_value, exc_traceback = exc_info
      raise exc_value.with_traceback(exc_traceback)
    return result

  def notify(self, notifier: Callable[["Result"], None]):
    ''' After the `Result` completes, call `notifier(self)`.

        If the `Result` has already completed this will happen immediately.
        If you'd rather `self` got put on some queue `Q`, supply `Q.put`.
    '''

    # TODO: adjust all users of .notify() to use fsm_callback and
    # accept a transition object?
    # pylint: disable=unused-argument
    def result_callback_wrapper(fsm, fsm_transition):
      ''' `FSM.fsm_callback` shim for plain `notify(Result)` notifier functions.
      '''
      self.collected = True
      return notifier(fsm)

    with self.__lock:
      self.fsm_callback('CANCELLED', result_callback_wrapper)
      self.fsm_callback('DONE', result_callback_wrapper)
      state = self.fsm_state
    # already cancelled or done? call the notifier immediately
    if state in (self.CANCELLED, self.DONE):
      self.collected = True
      notifier(self)

  def post_notify(self, post_func) -> "Result":
    ''' Return a secondary `Result` which processes the result of `self`.

        After the `self` completes, call `post_func(retval)` where
        `retval` is the result of `self`, and use that to complete
        the secondary `Result`.

        *Important note*: because the completion lock object is
        released after the internal `FSM.fsm_event` call, the
        callback used to implement `.post_notify` is fired before
        the lock object is released. As such, it would deadlock as
        it waits for completion of `self` by using that lock.
        Therefore the callback dispatches a separate `Thread` to
        wait for `self` and then run `post_func`.

        Example:

            # submit packet to data stream
            R = submit_packet()
            # arrange that when the response is received, decode the response
            R2 = R.post_notify(lambda response: decode(response))
            # collect decoded response
            decoded = R2()

        If the `Result` has already completed this will happen immediately.
    '''
    post_R = Result(f'post_notify({self.name}):{post_func}')

    def post_notify_notifier(preR):
      '''Run `post_func(self())`.'''
      retval = preR()
      post_R.run_func(post_func, retval)

    # We dispatch the post_func in a separate Thread because it
    # will deadlock on the completion of self.
    self.notify(
        lambda preR: Thread(
            name=f'{self}.post_notify({post_func})',
            target=post_notify_notifier,
            args=(preR,)
        ).start()
    )
    return post_R

def in_thread(func):
  ''' Decorator to evaluate `func` in a separate `Thread`.
      Return or exception is as for the original function.

      This exists to step out of the current `Thread's` thread
      local context, such as a database transaction associated
      with Django's implicit per-`Thread` database context.
  '''

  def run_in_thread(*a, **kw):
    ''' Create a `Result`, fulfil it by running `func(*a,**kw)`
        in a separate `Thread`, return the function result (or exception).
    '''
    desc_fmt, desc_fmt_args = func_a_kw_fmt(func, *a, **kw)
    R = Result(name=desc_fmt % tuple(desc_fmt_args))
    return R.run_func_in_thread(func, *a, **kw)

  # expose a reference to the original function
  run_in_thread.direct = func
  return run_in_thread

def call_in_thread(func, *a, **kw):
  ''' Run `func(*a,**kw)` in a separate `Thread` via the `@in_thread` decorator.
      Return or exception is as for the original function.
  '''
  return in_thread(func)(*a, **kw)

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
  for LF in list(LFs):
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
    with self._Result__lock:
      if self.is_pending:
        self.run_func(self.func, *self.fargs, **self.fkwargs)
    return super().__call__()

OnDemandFunction = OnDemandResult

if __name__ == '__main__':
  import cs.result_tests
  cs.result_tests.selftest(sys.argv)
