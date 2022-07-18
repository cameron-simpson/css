#!/usr/bin/env python3

''' A general purpose task queue for running tasks in parallel with
    dependencies and failure/retry.
'''

import sys
from threading import RLock
import time
from typing import Callable, TypeVar, Union

from icontract import require
from typeguard import typechecked

from cs.deco import decorator
from cs.fsm import FSM, FSMError
from cs.logutils import warning
from cs.pfx import Pfx
from cs.py.func import funcname
from cs.queues import ListQueue
from cs.resources import RunState, RunStateMixin
from cs.result import Result, CancellationError
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
  def __init__(
      self, msg: str, task: 'TaskSubType', blocking_task: 'TaskSubType'
  ):
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
      * `state`: initial state, default from `self._state.initial_state`,
        which is initally '`PENDING`'
      * `track`: default `False`;
        if `True` then apply a callback for all states to print task transitions;
        otherwise it should be a callback function suitable for `FSM.fsm_callback`
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
          'prepared': 'PENDING',
      },
      'PENDING': {
          'dispatch': 'RUNNING',
          'cancel': 'CANCELLED',
          'error': 'FAILED',
          'abort': 'ABORT',
      },
      'RUNNING': {
          'done': 'DONE',
          'except': 'FAILED',
          'cancel': 'CANCELLED',
      },
      'CANCELLED': {
          'requeue': 'PENDING',
          'abort': 'ABORT',
      },
      'DONE': {},
      'FAILED': {
          'retry': 'PENDING',
          'abort': 'ABORT',
      },
      'ABORT': {},
  }

  FSM_DEFAULT_STATE = 'PENDING'

  _seq = Seq()

  _state = ThreadState(current_task=None, initial_state=FSM_DEFAULT_STATE)

  def __init__(
      self,
      func,
      *a,
      func_args=(),
      func_kwargs=None,
      state=None,
      cancel_on_exception=False,
      cancel_on_result=None,
      track=False,
  ):
    if func is None or isinstance(func, str):
      name = func
      a = list(a)
      func = a.pop(0)
    if name is None:
      name = funcname(func)
    self.name = name
    if a:
      raise ValueError(
          "unexpected positional parameters after func: %r" % (a,)
      )
    if state is None:
      state = type(self)._state.initial_state
    if func_kwargs is None:
      func_kwargs = {}
    self._lock = RLock()
    FSM.__init__(self, state)
    runstate = RunState(name)
    RunStateMixin.__init__(self, runstate)
    self.required = set()
    self.cancel_on_exception = cancel_on_exception
    self.cancel_on_result = cancel_on_result
    self.func = func
    self.func_args = func_args
    self.func_kwargs = func_kwargs
    self.result = Result()
    if track:
      if track is True:
        track = lambda t, tr: print(f'{t.name} {tr.old_state}->{tr.event}->{tr.new_state}')
      self.fsm_callback(FSM.FSM_ANY_STATE, track)

  def __str__(self):
    return f'{type(self).__name__}:{self.name}:{self.fsm_state}'

  def __hash__(self):
    return id(self)

  def __eq__(self, otask):
    return self is otask

  def __call__(self):
    ''' Block on `self.result` awaiting completion
        by calling `self.result()`.
    '''
    return self.result()

  @classmethod
  def current_task(cls):
    ''' The current `Task`, valid while the task is running.
        This allows the function called by the `Task` to access the
        task, typically to poll its `.runstate` attribute.
        This is a `Thread` local value.
    '''
    return cls._state.current_task  # pylint: disable=no-member

  @typechecked
  def then(
      self,
      func: Union[str, Callable, 'TaskSubType'],
      *a,
      func_args=(),
      func_kwargs=None,
      **task_kw,
  ):
    ''' Prepare a new `Task` or function which may not run before `self` completes.
        This may be called in two ways:
        - `task.then(some_Task): block the `Task` instance `some_Task` behind `self`
        - `task.then([name,]func[,func_args=][,func_kwargs=][,Task_kwargs...]):
          make a new `Task` to be blocked behind `self`
        Return the new `Task`.

        This supports preparing a chain of actions:

            >>> t = Task("t", lambda: 0)
            >>> final_t = t.then(lambda: 1).then(lambda: 2)
            >>> final_t.ready   # the final task has not yet run
            False
            >>> # finalise t, wait for final_t (which runs immediately)
            >>> t(); print(final_t.join())
            1
            2
            (None, None)
            >>> final_t.ready
            True
    '''
    if isinstance(func, Task):
      if func_args or func_kwargs:
        raise ValueError("may not supply arguments when func is a Task")
      post_task = func
    else:
      # optional name
      if isinstance(func, str):
        name = func
        a = list(a)
        func = a.pop(0)
      else:
        name = f'{self}.then({func},...)'
      if a:
        raise ValueError(
            "unexpected positional arguments after func: %r" % (a,)
        )
      if func_kwargs is None:
        func_kwargs = {}
      post_task = type(self)(
          name,
          func,
          func_args=func_args,
          func_kwargs=func_kwargs,
          **task_kw,
      )
    post_task.require(self)
    return post_task

  @typechecked
  @require(lambda self, otask: otask is not self)
  @require(lambda self: self.is_prepare or self.is_pending)
  def require(self, otask: 'TaskSubType'):
    ''' Add a requirement that `otask` be complete before we proceed.
        This si the simple `Task` only version of `.then()`.
    '''
    with self._lock:
      self.required.add(otask)

  def block(self, otask):
    ''' Block another task until we are complete.
        The converse of `.require()`.
    '''
    otask.require(self)

  def blockers(self):
    ''' A generator yielding tasks from `self.required`
        which should block this task.
        Aborted tasks are not blockers
        but if we encounter one we do abort the current task.
    '''
    unblocked_states = self.DONE, self.FAILED, self.ABORT
    for otask in self.required:
      if otask.fsm_state in unblocked_states:
        continue
      yield otask

  ##############################################################
  # Specific implementations for things which would otherwise be
  # state transition events.
  #
  @locked
  def cancel(self):
    ''' Transition this `Task` to `CANCELLED` state.
        If the task is running, set `.cancelled` on the `RunState`,
        allowing clean task cancellation and subsequent transition
        (mediated by the `.run()` method).
        Otherwise fire the `'cancel'` event directly.
    '''
    if self.is_running:
      self.runstate.cancel()
    else:
      FSM.event(self, 'cancel')

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

  def run(self):
    ''' Run the function associated with this task,
        completing the `self.result` `Result` appropriately when finished.

        *WARNING*: this _ignores_ the current state and any blocking `Task`s.
        You should usually use `dispatch` or `make`.

        During the run the thread local `self.state.current_task`
        will be `self` and the `self.runstate` will be running.

        Otherwise run `func_result=self.func(*self.func_args,**self.func_kwargs)`
        with the following effects:
        * if the function raises a `CancellationError`, cancel the `Task`
        * if the function raises another exception,
          if `self.cancel_on_exception` then cancel the task
          else complete `self.result` with the exception
          and fire the `'error'` `event
        * if `self.runstate.canceled` or `self.cancel_on_result`
          was provided and `self.cancel_on_result(func_result)` is
          true, cancel the task
        * otherwise complete `self.result` with `func_result`
          and fire the `'done'` event
    '''
    if not self.is_running:
      warning(f'.run() when state is not {self.RUNNING!r}')
    state = type(self)._state
    R = self.result
    with state(current_task=self):
      try:
        with self.runstate:
          func_result = self.func(*self.func_args, **self.func_kwargs)
      except CancellationError:
        # cancel the task, ready for retry
        self.fsm_event('cancel')
      except BaseException:
        if self.cancel_on_exception:
          # cancel the task, ready for retry
          self.fsm_event('cancel')
        else:
          # error->FAILED
          # complete self.result with the exception
          R.exc_info = sys.exc_info()
          self.fsm_event('error')
      else:
        # if the runstate was cancelled or the result indicates
        # cancellation cancel the task otherwise complete `self.result`
        if (self.runstate.cancelled
            or (self.cancel_on_result and self.cancel_on_result(func_result))):
          # cancel the task, ready for retry
          self.fsm_event('cancel')
        else:
          # 'done'->DONE
          # complete self.result with the function return value
          R.result = func_result
          self.fsm_event('done')

  # pylint: disable=arguments-differ
  def bg(self):
    ''' Dispatch a function to complete the `Task` in a separate `Thread`,
        returning the `Thread`.
        This raises `BlockedError` for a blocked task.
        otherwise the thread runs `self.dispatch()`.
    '''
    with self._lock:
      for otask in self.blockers():
        raise BlockedError("%s blocked by %s" % (self, otask), self, otask)
      return bg_thread(self.dispatch, name=self.name)

  def make(self, fail_fast=False):
    ''' Generator to complete `self` and its prerequisites.
        This calls the global `make()` function with `self`.
        It returns a Boolean indicating whether this task succeeded.
    '''
    for t in make(self, fail_fast=fail_fast):
      assert t is self
    return t.is_done

  def join(self):
    ''' Wait for this task to complete.
    '''
    self.result.join()

TaskSubType = TypeVar('TaskSubType', bound=Task)

def make(*tasks, fail_fast=False):
  ''' Generator which completes all the supplied `tasks` by dispatching them
      once they are no longer blocked.
      Yield each task from `tasks` as it completes.

      Parameters:
      * `tasks`: `Task`s as positional parameters
      * `fail_fast`: default `False`; if true, cease evaluation as soon as a
        task completes in a state with is not `DONE`

      The following rules are applied by this function:
      - if a task is being prepared, raise an `FSMError`
      - if a task is already running, wait for its completion
      - if a task is pending:
        * if any prerequisite has failed, fail this task
        * if any prerequisite is cancelled, cancel this task
        * if any prerequisite is pending, make it first
        * otherwise dispatch this task and then yield it

      Examples:

          >>> t1 = Task('t1', lambda: print('t1'))
          >>> t2 = t1.then(lambda: print('t2'))
          >>> list(make(t2))    # doctest: +ELLIPSIS
          t1
          t2
          [<cs.taskqueue.Task object at ...>]
  '''
  tasks0 = set(tasks)
  q = ListQueue(tasks)
  for qtask in q:
    with Pfx(qtask):
      if qtask in tasks0:
        do_yield = True
        tasks0.remove(qtask)
      else:
        do_yield = False
      if qtask.is_prepare:
        raise FSMError(f'cannot make a {qtask.fsm_state} task', qtask)
      if qtask.is_running:
        qtask.join()
        assert qtask.fsm_state not in (
            qtask.PREPARE,
            qtask.PENDING,
            qtask.RUNNING,
        )
      elif qtask.is_pending:
        failed = [prereq for prereq in qtask.required if prereq.is_failed]
        if failed:
          qtask.fsm_event(
              'error', failed_prereq_uuids=[task.uuid for task in failed]
          )
        else:
          cancelled = [
              prereq for prereq in qtask.required if prereq.is_cancelled
          ]
          if cancelled:
            qtask.fsm_event(
                'cancel',
                cancelled_prereq_uuids=[task.uuid for task in cancelled]
            )
          else:
            pending = [
                prereq for prereq in qtask.required if prereq.is_pending
            ]
            if pending:
              # queue the pending tasks and qtask behind them
              pending.append(qtask)
              q.prepend(pending)
              if do_yield:
                # suppress the yield
                tasks0.add(qtask)
              continue
            # in principle all prereqs are now done
            assert all(prereq.is_done for prereq in qtask.required)
            qtask.dispatch()
      assert qtask.fsm_state in (qtask.CANCELLED, qtask.DONE, qtask.FAILED)
      if do_yield:
        yield qtask
      if fail_fast and not qtask.is_done:
        return

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
