#!/usr/bin/env python3

''' A general purpose Task and TaskQueue for running tasks with
    dependencies and failure/retry, potentially in parallel.
'''

from itertools import chain
import sys
from threading import RLock
import time
from typing import Callable, TypeVar, Union

from icontract import require
from typeguard import typechecked

from cs.deco import decorator
from cs.fsm import FSM, FSMError
from cs.gvutils import quote as gvq, gvprint, gvsvg
from cs.logutils import warning
from cs.pfx import Pfx
from cs.py.func import funcname
from cs.queues import ListQueue
from cs.resources import RunState, RunStateMixin
from cs.result import Result, CancellationError
from cs.seq import Seq, unrepeated
from cs.threads import bg as bg_thread, locked, State as ThreadState

__version__ = '20221207'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 3 - Alpha",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.fsm',
        'cs.gvutils',
        'cs.logutils',
        'cs.pfx',
        'cs.py.func',
        'cs.queues',
        'cs.resources',
        'cs.result',
        'cs.seq',
        'cs.threads',
        'icontract',
        'typeguard',
    ],
}

def main(argv):
  ''' Dummy main programme to exercise something.
  '''
  cmd = argv.pop(0)
  layout = argv.pop(0) if argv else 'dot'
  t1 = Task("t1", lambda: print("t1"), track=True)
  t2 = t1.then("t2", lambda: print("t2"), track=True)
  t3 = t1.then("t3", lambda: print("t3"), track=True)
  t2b = t2.then("t2b", lambda: print("t2b"), track=True)
  ##q = TaskQueue(t1, t2)
  q = TaskQueue(t1, t2, t3, t2b, run_dependent_tasks=True)
  t1.dispatch()
  dot = t1.as_dot("T1", follow_blocking=True)
  print(dot)
  gvprint(dot, layout=layout, fmt='svg')
  gvprint(dot, layout=layout, fmt='cmapx')
  gvprint(dot, layout=layout)
  ##for t in q.run():
  ##  print("completed", t)

class TaskError(FSMError):
  ''' Raised by `Task` related errors.
  '''

  # pylint: disable=redefined-outer-name
  @typechecked
  def __init__(self, msg: str, task: 'BaseTaskSubType'):
    super().__init__(msg, task)

class BlockedError(TaskError):
  ''' Raised by a blocked `Task` if attempted.
  '''

  # pylint: disable=redefined-outer-name
  @typechecked
  def __init__(
      self, msg: str, task: 'BaseTaskSubType', blocking_task: 'BaseTaskSubType'
  ):
    super().__init__(msg, task)
    self.blocking_task = blocking_task

class BaseTask(FSM, RunStateMixin):
  ''' A base class subclassing `cs.fsm.FSM` with a `RunStateMixin`.

      Note that this class and the `FSM` base class does not provide
      a `FSM_DEFAULT_STATE` attribute; a default `state` value of
      `None` will leave `.fsm_state` _unset_.

      This behaviour is is chosen mostly to support subclasses
      with unusual behaviour, particularly Django's `Model` class
      whose `refresh_from_db` method seems to not refresh fields
      which already exist, and setting `.fsm_state` from a
      `FSM_DEFAULT_STATE` class attribute thus breaks this method.
      Subclasses of this class and `Model` should _not_ provide a
      `FSM_DEFAULT_STATE` attribute, instead relying on the field
      definition to provide this default in the usual way.
  '''

  def __init__(self, *, state=None, runstate=None):
    FSM.__init__(self, state)
    RunStateMixin.__init__(self, runstate)

  @classmethod
  def tasks_as_dot(
      cls,
      tasks,
      name=None,
      *,
      follow_blocking=False,
      sep=None,
  ):
    ''' Return a DOT syntax digraph of the iterable `tasks`.
        Nodes will be coloured according to `DOT_NODE_FILLCOLOR_PALETTE`
        based on their state.

        Parameters:
        * `tasks`: an iterable of `Task`s to populate the graph
        * `name`: optional graph name
        * `follow_blocking`: optional flag to follow each `Task`'s
          `.blocking` attribute recursively and also render those
          `Task`s
        * `sep`: optional node seprator, default `'\n'`
    '''
    if sep is None:
      sep = '\n'
    digraph = [
        f'digraph {gvq(name)} {{' if name else 'digraph {',
        ##'graph[orientation=land]',
    ]
    q = ListQueue(unrepeated(tasks))
    for qtask in unrepeated(q):
      nodedef = qtask.dot_node()
      digraph.append(f'  {nodedef};')
      blocking = sorted(qtask.blocking, key=lambda t: t.name)
      for subt in blocking:
        digraph.append(f'  {gvq(qtask.dot_node_id)}->{gvq(subt.dot_node_id)};')
      if follow_blocking:
        q.extend(blocking)
    digraph.append('}')
    return sep.join(digraph)

  @classmethod
  def tasks_as_svg(cls, tasks, name=None, **kw):
    ''' Return an SVG diagram of the iterable `tasks`.
        This takes the same parameters as `tasks_as_dot`.
    '''
    return gvsvg(cls.tasks_as_dot(tasks, name=name, **kw))

  def as_dot(self, name=None, **kw):
    ''' Return a DOT syntax digraph starting at this `Task`.
        Parameters are as for `Task.tasks_as_dot`.
    '''
    return self.tasks_as_dot(
        [self],
        name=name,
        **kw,
    )

  def dot_node_label(self):
    ''' The default DOT node label.
    '''
    return f'{self.name}\n{self.fsm_state}'

BaseTaskSubType = TypeVar('BaseTaskSubType', bound=BaseTask)

# pylint: disable=too-many-instance-attributes
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
          'queue': 'QUEUED',
          'cancel': 'CANCELLED',
          'error': 'FAILED',
          'abort': 'ABORT',
      },
      'QUEUED': {
          'cancel': 'CANCELLED',
          'dispatch': 'RUNNING',
      },
      'RUNNING': {
          'done': 'DONE',
          'except': 'FAILED',
          'cancel': 'CANCELLED',
      },
      'CANCELLED': {
          'retry': 'PENDING',
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

  DOT_NODE_FILLCOLOR_PALETTE = {
      'RUNNING': 'yellow',
      'DONE': 'green',
      'FAILED': 'red',
      'CANCELLED': 'gray',
      'ABORT': 'darkred',
      None: 'white',
  }

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
          "unexpected positional parameters after func:%r: %r" % (func, a)
      )
    if state is None:
      state = type(self)._state.initial_state
    if func_kwargs is None:
      func_kwargs = {}
    self._lock = RLock()
    super().__init__(state=state, runstate=RunState(name))
    self.required = set()
    self.blocking = set()
    self.cancel_on_exception = cancel_on_exception
    self.cancel_on_result = cancel_on_result
    self.func = func
    self.func_args = func_args
    self.func_kwargs = func_kwargs
    self.result = Result()
    if track:
      if track is True:
        track = lambda t, tr: print(
            f'{t.name} {tr.old_state}->{tr.event}->{tr.new_state}'
        )
      self.fsm_callback(FSM.FSM_ANY_STATE, track)

  def __str__(self):
    return f'{type(self).__name__}:{self.name}:{self.fsm_state}'

  def __repr__(self):
    return f'{type(self).__name__}({self.name!r},{self.func!r},state={self.fsm_state!r})'

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

            >>> t_root = Task("t_root", lambda: 0)
            >>> t_leaf = t_root.then(lambda: 1).then(lambda: 2)
            >>> t_root.iscompleted()   # the root task has not yet run
            False
            >>> t_leaf.iscompleted()   # the final task has not yet run
            False
            >>> # t_leaf is blocked by t_root
            >>> t_leaf.dispatch()      # doctest: +ELLIPSIS
            Traceback (most recent call last):
              ...
            cs.taskqueue.BlockedError: ...
            >>> t_leaf.make()          # make the leaf, but make t_root first
            True
            >>> t_root.iscompleted()   # implicitly completed by make
            True
            >>> t_leaf.iscompleted()
            True
    '''
    if isinstance(func, Task):
      if func_args or func_kwargs:
        raise ValueError("may not supply arguments when func is a Task")
      post_task = func
    else:
      # optional name
      if func is None or isinstance(func, str):
        name = func
        a = list(a)
        func = a.pop(0)
      else:
        name = None
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
        This is the simple `Task` only version of `.then()`.
    '''
    with self._lock:
      self.required.add(otask)
      otask.blocking.add(self)

  def block(self, otask):
    ''' Block another task until we are complete.
        The converse of `.require()`.
    '''
    otask.require(self)

  def iscompleted(self):
    ''' This task is completed (even if failed) and does not block contingent tasks.
    '''
    return self.fsm_state in (self.DONE, self.FAILED, self.ABORT)

  def isblocked(self):
    ''' A task is blocked if any prerequisite is not complete.
    '''
    return any(not prereq.iscompleted() for prereq in self.required)

  def blockers(self):
    ''' A generator yielding tasks from `self.required`
        which should block this task.
        Aborted tasks are not blockers
        but if we encounter one we do abort the current task.
    '''
    for otask in self.required:
      if not otask.iscompleted():
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
    with self._lock:
      for otask in self.blockers():
        if otask.is_abort:
          warning("%s requires %s which is aborted: aborting", self, otask)
          self.fsm_event('abort')
        raise BlockedError("%s blocked by %s" % (self, otask), self, otask)
      self.fsm_event('dispatch')
      return self.run()

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
        if self.runstate.cancelled or (self.cancel_on_result
                                       and self.cancel_on_result(func_result)):
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
    ''' Complete `self` and its prerequisites.
        This calls the global `make()` function with `self`.
        It returns a Boolean indicating whether this task completed.
    '''
    for t in make(self, fail_fast=fail_fast):
      assert t is self
    return self.iscompleted()

  def join(self):
    ''' Wait for this task to complete.
    '''
    self.result.join()

TaskSubType = TypeVar('TaskSubType', bound=Task)

# pylint: disable=too-many-branches
def make(*tasks, fail_fast=False, queue=None):
  ''' Generator which completes all the supplied `tasks` by dispatching them
      once they are no longer blocked.
      Yield each task from `tasks` as it completes (or becomes cancelled).

      Parameters:
      * `tasks`: `Task`s as positional parameters
      * `fail_fast`: default `False`; if true, cease evaluation as soon as a
        task completes in a state with is not `DONE`
      * `queue`: optional callable to submit a task for execution later
        via some queue such as `Later` or celery

      The following rules are applied by this function:
      - if a task is being prepared, raise an `FSMError`
      - if a task is already running or queued, wait for its completion
      - if a task is pending:
        * if any prerequisite has failed, fail this task
        * if any prerequisite is cancelled, cancel this task
        * if any prerequisite is pending, make it first
        * if any prerequisite is not done, fail this task
        * otherwise dispatch this task and then yield it
      - if `fail_fast` and the task is not done, return

      Examples:

          >>> t1 = Task('t1', lambda: print('doing t1'), track=True)
          >>> t2 = t1.then('t2', lambda: print('doing t2'), track=True)
          >>> list(make(t2))    # doctest: +ELLIPSIS
          t1 PENDING->dispatch->RUNNING
          doing t1
          t1 RUNNING->done->DONE
          t2 PENDING->dispatch->RUNNING
          doing t2
          t2 RUNNING->done->DONE
          [Task('t2',<function <lambda> at ...>,state='DONE')]
  '''
  tasks0 = set(tasks)
  q = ListQueue(tasks)
  for qtask in q:
    with Pfx(qtask):
      if qtask.is_prepare:
        raise FSMError(f'cannot make a {qtask.fsm_state} task', qtask)
      if qtask.is_running or qtask.is_queued:
        # task is already running, or queued and will be run
        qtask.join()
        assert qtask.iscompleted()
      elif qtask.is_pending:
        failed = [
            prereq for prereq in qtask.required
            if prereq.is_failed or prereq.is_abort
        ]
        if failed:
          qtask.fsm_event(
              'error',
              because='failed prerequisites',
              failed=failed,
          )
        else:
          cancelled = [
              prereq for prereq in qtask.required if prereq.is_cancelled
          ]
          if cancelled:
            qtask.fsm_event(
                'cancel',
                because='cancelled prerequisites',
                cancelled=cancelled,
            )
          else:
            pending = [
                prereq for prereq in qtask.required if prereq.is_pending
            ]
            if pending:
              # queue the pending tasks and qtask behind them
              pending.append(qtask)
              q.prepend(pending)
              continue
            # see if any prerequisiters are some unhanlded not-DONE state
            undone = [
                prereq for prereq in qtask.required if not prereq.is_done
            ]
            if undone:
              qtask.fsm_event(
                  'error',
                  because='some prerequisites are not done',
                  undone=undone,
              )
            else:
              # prerequsites all done, run the task
              if queue is None:
                # run the task directly
                qtask.dispatch()
              else:
                # queue the task via some runner such as a Later
                qtask.queue()
                queue(qtask.dispatch)
                q.append(qtask)
                continue
      assert qtask.iscompleted() or qtask.is_cancelled()
      if qtask in tasks0:
        yield qtask
        tasks0.remove(qtask)
      if fail_fast and not qtask.is_done:
        return

def make_now(*tasks, fail_fast=False, queue=None):
  ''' Run the generator `make(*tasks)` to completion and return the
      list of completed tasks.
  '''
  return list(make(*tasks, fail_fast=fail_fast, queue=queue))

def make_later(L, *tasks, fail_fast=False):
  ''' Dispatch the `tasks` via `L:Later` for asynchronous execution
      if it is not already completed.
      The caller can wait on `t.result` for completion.

      This calls `make_now()` in a thread and uses `L.defer` to
      queue the task and its prerequisites for execution.
  '''
  bg_thread(
      make_now,
      name=f'make({",".join(t.name for t in tasks)})',
      args=tasks,
      kwargs=dict(fail_fast=fail_fast, queue=L.defer),
  )

class TaskQueue:
  ''' A task queue for managing and running a set of related tasks.

      Unlike `make` and `Task.make`, this is aimed at a "dispatch" worker
      which dispatches individual tasks as required.

      Example 1, put 2 dependent tasks in a queue and run:

           >>> t1 = Task("t1", lambda: print("t1"))
           >>> t2 = t1.then("t2", lambda: print("t2"))
           >>> q = TaskQueue(t1, t2)
           >>> for _ in q.run(): pass
           ...
           t1
           t2

      Example 2, put 1 task in a queue and run.
      The queue only runs the specified tasks:

           >>> t1 = Task("t1", lambda: print("t1"))
           >>> t2 = t1.then("t2", lambda: print("t2"))
           >>> q = TaskQueue(t1)
           >>> for _ in q.run(): pass
           ...
           t1

      Example 2, put 1 task in a queue with `run_dependent_tasks=True` and run.
      The queue pulls in the dependencies of completed tasks and also runs those:

           >>> t1 = Task("t1", lambda: print("t1"))
           >>> t2 = t1.then("t2", lambda: print("t2"))
           >>> q = TaskQueue(t1, run_dependent_tasks=True)
           >>> for _ in q.run(): pass
           ...
           t1
           t2
  '''

  def __init__(self, *tasks, run_dependent_tasks=False):
    ''' Initialise the queue with the supplied `tasks`.
    '''
    self.run_dependent_tasks = run_dependent_tasks
    self._tasks = set()
    self._up = set()  # unblocked pending
    self._ready = set()  # completed tasks
    self._unready = set()  # not unblocked pending and not completed
    self._lock = RLock()
    for t in tasks:
      self.add(t)

  def as_dot(self, name=None, **kw):
    ''' Compute a DOT syntax graph description of the tasks in the queue.
    '''
    return Task.tasks_as_dot(
        chain(
            sorted(self._ready, key=lambda t: t.name),
            sorted(self._up, key=lambda t: t.name),
            sorted(self._unready, key=lambda t: t.name),
        ),
        name,
        **kw,
    )

  # pylint: disable=redefined-outer-name
  @locked
  def add(self, task):
    ''' Add a task to the tasks managed by this queue.
    '''
    self._tasks.add(task)
    self._update_sets(task)

  @staticmethod
  def _set_add(taskset, task):
    ''' Add `task` to `taskset`, return whether it was new.
    '''
    if task in taskset:
      return False
    taskset.add(task)
    return True

  @staticmethod
  def _set_discard(taskset, task):
    ''' Discard `task` to `taskset`, return whether it removed.
    '''
    if task not in taskset:
      return False
    taskset.remove(task)
    return True

  def _update_sets(self, task):
    ''' Update the queue set membership for `task`.
    '''
    changed = False
    if task not in self._tasks:
      warning("%s._update_sets: ignoring untracked task %s", self, task)
    else:
      if task.iscompleted():
        # completed
        changed |= self._set_discard(self._up, task)
        changed |= self._set_add(self._ready, task)
        changed |= self._set_discard(self._unready, task)
      elif task.is_pending and not task.isblocked():
        # pending unblocked
        changed |= self._set_add(self._up, task)
        changed |= self._set_discard(self._ready, task)
        changed |= self._set_discard(self._unready, task)
      else:
        # unready
        changed |= self._set_discard(self._up, task)
        changed |= self._set_discard(self._ready, task)
        changed |= self._set_add(self._unready, task)
    return changed

  # pylint: disable=redefined-outer-name
  @locked
  def _on_state_change(self, *tasks):
    ''' Update task state sets based on task state transitions.
    '''
    q = ListQueue(tasks)
    for task in unrepeated(q):
      changed = self._update_sets(task)
      if changed:
        q.extend(task.blocking)

  # pylint: disable=redefined-outer-name
  def get(self):
    ''' Pull a completed or an unblocked pending task from the queue.
        Return the task or `None` if nothing is available.

        The returned task is no longer tracked by this queue.
    '''
    try:
      task = self._ready.pop()
    except KeyError:
      try:
        task = self._up.pop()
      except KeyError:
        return None
    self._tasks.remove(task)
    return task

  # pylint: disable=redefined-outer-name
  def run(self, runstate=None, once=False):
    ''' Process tasks in the queue until the queue has no completed tasks,
        yielding each task, immediately if `task.iscompleted()`
        otherwise after `taks.dispatch()`.

        An optional `RunState` may be provided to allow early termination
        via `runstate.cancel()`.

        An incomplete task is `dispatch`ed before `yield`;
        ideally it will be complete when the yield happens,
        but its semantics might mean it is in another state such as `CANCELLED`.
        The consumer of `run` must handle these situations.
    '''
    while runstate is None or not runstate.cancelled:
      task = self.get()
      if task is None:
        break
      if not task.iscompleted():
        task.dispatch()
        # update the state of tasks we are blocking
        if self.run_dependent_tasks:
          # add these tasks to the queue
          self._tasks |= set(task.blocking)
        self._on_state_change(*task.blocking)
      yield task
      if once:
        break

##################################################
#
# decorator under development
#

@decorator
def _task(func, task_class=Task):
  ''' Decorator for a function which runs it as a `Task`.
      The function may still be called directly.

      The following function attributes are provided:
      * `dispatch(after=(),deferred=False,delay=0.0)`: run this function
        after the completion of the tasks specified by `after`
        and after at least `delay` seconds;
        return the `Task` for the queued function

      ##Examples:

      ##    >>> import time
      ##    >>> @task
      ##    ... def f(x):
      ##    ...     return x * 2
      ##    ...
      ##    >>> print(f(3))  # call the function normally
      ##    6
      ##    >>> # dispatch f(5) after 0.5s, get Task
      ##    >>> t0 = time.time()
      ##    >>> ft = f.dispatch((5,), delay=0.5)
      ##    >>> # calling a Task, as with a Result, is like calling the function
      ##    >>> print(ft())
      ##    10
      ##    >>> # check that we were blocked for 0.5s
      ##    >>> now = time.time()
      ##    >>> now - t0 >= 0.5
      ##    True
  '''

  def make_task(func, a, kw):
    ft_name = f'{task_class.__name__}({funcname(func)})'
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

if __name__ == '__main__':
  sys.exit(main(sys.argv))
