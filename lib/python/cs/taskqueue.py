#!/usr/bin/env python3

''' A general purpose task queue for running tasks in parallel with
    dependencies and failure/retry.
'''

import sys
from threading import RLock
import time

from cs.deco import decorator
from cs.fsm import FSM
from cs.logutils import warning, debug
from cs.py.func import funcname
from cs.resources import RunState, RunStateMixin
from cs.result import BaseResult, CancellationError
from cs.seq import Seq
from cs.threads import bg as bg_thread, locked, State as ThreadState


class TaskError(FSMError):
  ''' Raised by `Task` related errors.
  '''

  @typechecked
  def __init__(self, msg: str, task: 'TaskSubType'):
    super().__init__(msg, task)

class BlockedError(TaskError):
  ''' Raised by a blocked `Task` if attempted.
  '''

  @typechecked
  def __init__(self, msg: str, task: 'TaskSubType', blocking_task: 'TaskSubType'):
    super().__init__(msg, task)
    self.blocking_task = blocking_task

class Task(FSM, RunStateMixin):
  ''' A task which may require the completion of other tasks.

      The model here may not be quite as expected; it is aimed at
      tasks which can be repaired and rerun.
      As such, if `self.run(func,...)` raises an exception from
      `func` then this `Task` will still block dependent `Task`s.
      Dually, a `Task` which completes without an exception is
      considered complete and does not block dependent `Task`s.

      Keyword parameters:
      * `cancel_on_exception`: if true, cancel this `Task` if `.run`
        raises an exception; the default is `False`, allowing repair
        and retry
      * `cancel_on_result`: optional callable to test the `Task.result`
        after `.run`; if the callable returns `True` the `Task` is marked
        as cancelled, allowing repair and retry
      * `func`: the function to call to complete the `Task`;
        it will be called as `func(*func_args,**func_kwargs)`
      * `func_args`: optional positional arguments, default `()`
      * `func_kwargs`: optional keyword arguments, default `{}`
      * `lock`: optional lock, default an `RLock`
      Other arguments are passed to the `Result` initialiser.

      Example:

          t1 = Task(name="task1")
          t1.bg(time.sleep, 10)
          t2 = Task(name="task2")
          # prevent t2 from running until t1 completes
          t2.require(t1)
          # try to run sleep(5) for t2 immediately after t1 completes
          t1.notify(t2.call, sleep, 5)

      Users wanting more immediate semantics can supply
      `cancel_on_exception` and/or `cancel_on_result` to control
      these behaviours.

      Example:

          t1 = Task(name="task1")
          t1.bg(time.sleep, 2)
          t2 = Task(name="task2")
          # prevent t2 from running until t1 completes
          t2.require(t1)
          # try to run sleep(5) for t2 immediately after t1 completes
          t1.notify(t2.call, sleep, 5)
  '''

  FSM_TRANSITIONS = {
        'PREPARE': {
            'ready': 'PENDING',
        },
        'PENDING': {
            'dispatch': 'RUNNING',
            ##'cancel': 'CANCELLED',
            'abort': 'ABORT',
        },
        'RUNNING': {
            'success': 'SUCCEEDED',
            'error': 'FAILED',
            'cancel': 'CANCELLED',
        },
        'CANCELLED': {
            'requeue': 'PENDING',
            'abort': 'ABORT',
        },
        'FAILED': {
            'retry': 'PENDING',
            'abort': 'ABORT',
        },
    }

  PREPARE = 'PREPARE'
  PENDING = 'PENDING'
  RUNNING = 'RUNNING'
  SUCCEEDED = 'SUCCEEDED'
  CANCELLED = 'CANCELLED'
  FAILED = 'FAILED'
  ABORT = 'ABORT'

  _seq = Seq()

  _state = ThreadState(current_task=None)

  def __init__(
      self,
      *a,
      lock=None,
      cancel_on_exception=False,
      cancel_on_result=None,
      func,
      func_args=(),
      func_kwargs=None,
      runstate=None,
      **kw
  ):
    if lock is None:
      lock = RLock()
    if func_kwargs is None:
      func_kwargs = {}
    FSM.__init__(self, 'PREPARE')
    BaseResult.__init__(self, *a, lock=lock, **kw)
    if runstate is None:
      runstate = RunState(self.name)
    RunStateMixin.__init__(self, runstate)
    self._required = set()
    self.cancel_on_exception = cancel_on_exception
    self.cancel_on_result = cancel_on_result
    self.func = func
    self.func_args = func_args
    self.func_kwargs = func_kwargs

  def __hash__(self):
    return id(self)

  def __eq__(self, otask):
    return self is otask

  @property
  def ready(self):
    return self.fsm_state in ('SUCCEEDED', 'FAILED', 'CANCELLED')

  @locked
  def cancel(self):
    ''' Transition this `Task` to `CANCELLED` state.
        Return whether the transition succeeded.

        If the `Task` is in `PENDING`, `RUNNING` or `CANCELLED`
        the resulting state is `CANCELLED` and this method returns `True`.

        If the `Task` is in `SUCCEEDED` or `ABORTED`
        the resulting state is unchanged and this method returns `False`.
    '''
    if self.running:
      super().cancel()

  @classmethod
  def current_task(cls):
    ''' The current `Task`, valid while the task is running.
        This allows the function called by the `Task` to access the
        task, typically to poll its `.runstate` attribute.
        This is a `Thread` local value.
    '''
    return cls._state.current_task  # pylint: disable=no-member

  def required(self):
    ''' Return a `set` containing any required tasks.
    '''
    with self._lock:
      return set(self._required)

  def require(self, otask):
    ''' Add a requirement that `otask` be complete before we proceed.
    '''
    assert otask is not self
    assert self.is_prepare or self.is_pending
    with self._lock:
      self._required.add(otask)

  def block(self, otask):
    ''' Block another task until we are complete.
    '''
    otask.require(self)

  def then(self, func, *a, **kw):
    ''' Queue a call to `func(*a,**kw)` to run after the completion of
        this task.

        This supports a chain of actions:

            >>> t = Task(func=lambda: 1)
            >>> final_t = t.then(print,1).then(print,2)
            >>> final_t.ready   # the final task has not yet run
            False
            >>> # finalise t, wait for final_t (which runs immediately)
            >>> t.run(); print(final_t.join())
            1
            2
            (None, None)
            >>> final_t.ready
            True
    '''
    post_task = type(self)(func=func, func_args=a, func_kwargs=kw)
    post_task.require(self)
    self.notify(lambda _: post_task.bg())
    return post_task

  def blockers(self):
    ''' A generator yielding tasks from `self.required`
        which should block this task.
        Aborted tasks are not blockers
        but if we encounter one we do abort the current task.
    '''
    for otask in self.required():
      if otask.cancelled:
        warning("%s cancelled because %s is also cancelled" % (self, otask))
        self.cancel()
        continue
      if not otask.ready:
        yield otask
        continue
      if otask.exc_info:
        yield otask
        continue

  def run_func(self, func, *a, **kw):
    raise RuntimeError(
        "%s function is predefined, .run_func() is forbidden, use unadorned .run() instead"
        % type(self)
    )

  # pylint: disable=arguments-differ
  def dispatch(self):
    ''' Dispatch the `Task`:
        If the task is blocked, raise `BlockedError`.
        If a prerequisite is aborted, fire the 'abort' method.
        Otherwise fire the `'dispatch'` event and then run the
        task's function via the `.run()` method.
    '''
    for otask in self.blockers():
      raise BlockedError("%s blocked by %s" % (self, otask))
    with self._lock:
      self.fsm_event('dispatch')
      state = type(self)._state
      with state(current_task=self):
          try:
            with self.runstate:
              r = self.func(*self.func_args, **self.func_kwargs)
          except CancellationError:
            self.fsm_event('cancel')
          except BaseException:
            if self.cancel_on_exception:
              self.fsm_event('cancel')
            else:
              self.fsm_event('error')
            # store the exception regardless
            self.exc_info = sys.exc_info()
          else:
            if self.cancel_on_result and self.cancel_on_result(r):
              self.fsm_event('cancel')
            else:
              self.fsm_state('success')
            # store the result regardless
            self.result = r
    if self.fsm_state == 'CANCELLED':
      raise CancellationError

  # pylint: disable=arguments-differ
  def bg(self):
    ''' Dispatch a function to complete the `Task` in a separate `Thread`,
        returning the `Thread`.
        It is forbidden to dispatch a `Task` not in `PENDING` state.

        This dispatches a `Thread` to run `self.run()`
        and as such the `Task` must be in "pending" state,
        and transitions to "running".
    '''
    if not self.is_pending:
      raise RuntimeError("attempt to run Task when self.state is not PENDING")
    return bg_thread(self.run, name=self.name)

  def callif(self):
    ''' Trigger a call to the `Task` function if we're pending.
    '''
    with self._lock:
      if self.is_prepare:
        warning("%s.callif: ignoring call while self.state is PREPARE", self)
      elif self.is_pending:
        try:
          self.run()
        except (BlockedError, CancellationError) as e:
          debug("%s.callif: %s", self, e)

@decorator
def task(func, task_class=Task):
  ''' Decorator for a function which runs it as a `Task`.
      The function may still be called directly.
      The function should accept a `Task` as its first argument.

      The following function attributes are provided:
      * `dispatch(after=(),deferred=False,delay=0.0)`: run this function
        after the completion of the tasks specified by `after`
        and after at least `delay` seconds;
        return the `Task` for the queued function

      Examples:

          >>> import time
          >>> @task
          ... def f(x):
          ...     return x * 2
          ...
          >>> print(f(3))  # call the function normally
          6
          >>> # dispatch f(5) after 0.5s, get Task
          >>> t0 = time.time()
          >>> ft = f.dispatch((5,), delay=0.5)
          >>> # calling a Task, as with a Result, is like calling the function
          >>> print(ft())
          10
          >>> # check that we were blocked for 0.5s
          >>> now = time.time()
          >>> now - t0 >= 0.5
          True
  '''

  def make_task(func, a, kw):
    ft_name = funcname(func) + '-task'
    if a:
      ft_name = ft_name + ':' + repr(a)
    if kw:
      ft_name = ft_name + ':' + repr(kw)
    return task_class(name=ft_name, func=func, func_args=a, func_kwargs=kw)

  def task_func_wrapper(*a, **kw):
    ''' Run the function via a `Task`.
    '''
    ft = make_task(func, a, kw)
    ft.run()
    return ft()

  # pylint: disable=redefined-outer-name
  def dispatch(a=None, kw=None, after=(), deferred=False, delay=0.0):
    ''' Dispatch the function asynchronously.
        Return the `Task`.

        Optional positional parameters:
        * `a`: an iterable of positional arguments for `func`
        * `kw`: a mapping of keyword arguments for `func`

        Keyword parameters:
        * `after`: optional iterable of `Task`s;
          `func` will not be dispatched until these are complete.
        * `deferred`: (default `False`); if true,
          block the task on `after` but do not trigger a call on
          their completion.
          This thus creates the `Task` but does not dispatch it.
        * `delay`: delay the dispatch of `func` by at least `delay` seconds,
          default `0.0`s.
    '''
    if a is None:
      a = ()
    if kw is None:
      kw = {}
    if not isinstance(after, list):
      after = list(after)
    if delay > 0.0:
      delay_task = task_class(
          name="sleep(%s)" % (delay,), func=time.sleep, func_args=(delay,)
      )
      delay_task.bg()
      after.append(delay_task)
    ft = make_task(func, a, kw)
    if after:
      for otask in after:
        ft.require(otask)
    if not deferred:
      if after:
        for otask in after:
          otask.notify(lambda _: ft.callif())
      else:
        # dispatch the task immediately, but in another Thread
        ft.bg(ft.call, func, *a, **kw)
    return ft

  task_func_wrapper.dispatch = dispatch
  return task_func_wrapper
