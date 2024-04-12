#!/usr/bin/env python

''' Assorted context managers.
'''

from contextlib import contextmanager
import signal
try:
  from contextlib import nullcontext  # pylint: disable=unused-import,ungrouped-imports
except ImportError:

  @contextmanager
  def nullcontext():
    ''' A simple `nullcontext` for older Pythons
    '''
    yield None

from cs.gimmicks import error

__version__ = '20240316-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.gimmicks'],
}

@contextmanager
def contextif(cmgr, *cmgr_args, **cmgr_kwargs):
  ''' A context manager to use `cmgr` conditionally,
      with a flexible call signature.
      This yields the context manager if `cmgr` is used or `None`
      if it is not used, allowing the enclosed code to test whether
      the context is active.

      This is to ease uses where the context object is optional
      i.e. `None` if not present. Example from `cs.vt.stream`:

          @contextmanager
          def startup_shutdown(self):
            """ Open/close `self.local_store` if not `None`.
            """
            with super().startup_shutdown():
              with contextif(self.local_store):
                with self._packet_connection(self.recv, self.send) as conn:
                  with stackattrs(self, _conn=conn):
                    yield

      Here `self.local_store` might be `None` if there's no local
      store to present. We still want a nice nested `with` statement
      during the setup. By using `contextif` we run a context manager
      which behaves correctly when `self.local_store=None`.

      The signature is flexible, offering 2 basic modes of use.

      Flagged use: `contextif(flag,cmgr,*a,**kw)`: if `flag` is a
      Boolean then it governs whether the context manager `cmgr`
      is used. Historically the driving use case was verbosity
      dependent status lines or progress bars. Example:

          from cs.upd import run_task
          with contextif(verbose, run_task, ....) as proxy:
              ... do stuff, updating proxy if not None ...

      Unflagged use: `contextif(cmgr,*a,**kw)`: use `cmgr` as the
      flag: if false (eg `None`) then `cmgr` is not used.

      Additionally, `cmgr` may be a callable, in which case the
      context manager itself is obtained by calling
      `cmgr,*cmgr_args,**cmgr_kwargs)`. Otherwise `cmgr` is assumed
      to be a context manager already, and it is an error to provide
      `cmgr_args` or `cmgr_kwargs`.

      In the `cs.upd` example above, `run_task` is a context manager
      function which pops up an updatable status line, normally
      used as:

          with run_task("doing thing") as proxy:
              ... do the thing, setting proxy.text as needed ...
  '''
  if callable(cmgr):
    flag = True
  else:
    if isinstance(cmgr, bool):
      flag = cmgr
      cmgr = cmgr_args[0]
      cmgr_args = cmgr_args[1:]
    else:
      flag = bool(cmgr)
  if flag:
    if callable(cmgr):
      cmgr = cmgr(*cmgr_args, **cmgr_kwargs)
    elif cmgr_args or cmgr_kwargs:
      raise ValueError(
          "cmgr %s:%r is not callable but arguments were provided: cmgr_args=%r, cmgr_kwargs=%r"
          % (cmgr.__class__.__name__, cmgr, cmgr_args, cmgr_kwargs)
      )
    with cmgr as ctxt:
      yield ctxt
  else:
    yield

def pushattrs(o, **attr_values):
  ''' The "push" part of `stackattrs`.
      Push `attr_values` onto `o` as attributes,
      return the previous attribute values in a `dict`.

      This can be useful in hooks/signals/callbacks,
      where you cannot inline a context manager.
  '''
  old_values = {}
  for attr, value in attr_values.items():
    try:
      old_value = getattr(o, attr)
    except AttributeError:
      pass
    else:
      old_values[attr] = old_value
    setattr(o, attr, value)
  return old_values

def popattrs(o, attr_names, old_values):
  ''' The "pop" part of `stackattrs`.
      Restore previous attributes of `o`
      named by `attr_names` with previous state in `old_values`.

      This can be useful in hooks/signals/callbacks,
      where you cannot inline a context manager.
  '''
  for attr in attr_names:
    try:
      old_value = old_values[attr]
    except KeyError:
      try:
        delattr(o, attr)
      except AttributeError:
        pass
    else:
      setattr(o, attr, old_value)

@contextmanager
def stackattrs(o, **attr_values):
  ''' Context manager to push new values for the attributes of `o`
      and to restore them afterward.
      Returns a `dict` containing a mapping of the previous attribute values.
      Attributes not present are not present in the returned mapping.

      Restoration includes deleting attributes which were not present
      initially.

      This makes it easy to adjust temporarily some shared context object
      without having to pass it through the call stack.

      See `stackkeys` for a flavour of this for mappings.

      See `cs.threads.ThreadState` for a convenient wrapper class.

      Example of fiddling a programme's "verbose" mode:

          >>> class RunModes:
          ...     def __init__(self, verbose=False):
          ...         self.verbose = verbose
          ...
          >>> runmode = RunModes()
          >>> if runmode.verbose:
          ...     print("suppressed message")
          ...
          >>> with stackattrs(runmode, verbose=True):
          ...     if runmode.verbose:
          ...         print("revealed message")
          ...
          revealed message
          >>> if runmode.verbose:
          ...     print("another suppressed message")
          ...

      Example exhibiting restoration of absent attributes:

          >>> class O:
          ...     def __init__(self):
          ...         self.a = 1
          ...
          >>> o = O()
          >>> print(o.a)
          1
          >>> print(o.b)
          Traceback (most recent call last):
            File "<stdin>", line 1, in <module>
          AttributeError: 'O' object has no attribute 'b'
          >>> with stackattrs(o, a=3, b=4):
          ...     print(o.a)
          ...     print(o.b)
          ...     o.b = 5
          ...     print(o.b)
          ...     delattr(o, 'a')
          ...
          3
          4
          5
          >>> print(o.a)
          1
          >>> print(o.b)
          Traceback (most recent call last):
            File "<stdin>", line 1, in <module>
          AttributeError: 'O' object has no attribute 'b'
  '''
  old_values = pushattrs(o, **attr_values)
  try:
    yield old_values
  finally:
    popattrs(o, attr_values.keys(), old_values)

def pushkeys(d, kv=None, **kw):
  ''' The "push" part of `stackkeys`.
      Use the mapping provided as `kv` or `kw` to update `d`.
      Return the previous key values in a `dict`.

      This can be useful in hooks/signals/callbacks,
      where you cannot inline a context manager using `stackkeys`.
  '''
  if kv is None:
    kv = kw
  elif kw:
    raise ValueError("pushkeys: only one of kv or kw may be provided")
  old_values = {}
  for key, value in kv.items():
    try:
      old_value = d[key]
    except KeyError:
      pass
    else:
      old_values[key] = old_value
    d[key] = value
  return old_values

def popkeys(d, key_names, old_values):
  ''' The "pop" part of `stackkeys`.
      Restore previous key values of `d`
      named by `key_names` with previous state in `old_values`.

      This can be useful in hooks/signals/callbacks,
      where you cannot inline a context manager.
  '''
  for key in key_names:
    try:
      old_value = old_values[key]
    except KeyError:
      try:
        del d[key]
      except KeyError:
        pass
    else:
      d[key] = old_value

@contextmanager
def stackkeys(d, kv=None, **kw):
  ''' A context manager to push new values for the key values of `d`
      and to restore them afterward.
      The new values are provided as `kv` or `kw` as convenient.
      Returns a `dict` containing a mapping of the previous key values.
      Keys not present are not present in the mapping.

      Restoration includes deleting key values which were not present
      initially.

      This makes it easy to adjust temporarily some shared context object
      without having to pass it through the call stack.

      See `stackattrs` for a flavour of this for object attributes.

      Example of making log entries which may reference
      some higher level context log entry:

          >>> import time
          >>> global_context = {
          ...     'parent': None,
          ... }
          >>> def log_entry(desc, **kw):
          ...     print("log_entry: global_context =", repr(global_context))
          ...     entry = dict(global_context)
          ...     entry.update(desc=desc, when=time.time())
          ...     entry.update(kw)
          ...     return entry
          ...
          >>> log_entry("stand alone entry")    #doctest: +ELLIPSIS
          log_entry: global_context = {'parent': None}
          {'parent': None, 'desc': 'stand alone entry', 'when': ...}
          >>> context_entry = log_entry("high level entry")
          log_entry: global_context = {'parent': None}
          >>> context_entry                     #doctest: +ELLIPSIS
          {'parent': None, 'desc': 'high level entry', 'when': ...}
          >>> with stackkeys(global_context, parent=context_entry): #doctest: +ELLIPSIS
          ...     print(repr(log_entry("low level event")))
          ...
          log_entry: global_context = {'parent': {'parent': None, 'desc': 'high level entry', 'when': ...}}
          {'parent': {'parent': None, 'desc': 'high level entry', 'when': ...}, 'desc': 'low level event', 'when': ...}
          >>> log_entry("another standalone entry")    #doctest: +ELLIPSIS
          log_entry: global_context = {'parent': None}
          {'parent': None, 'desc': 'another standalone entry', 'when': ...}
  '''
  old_values = pushkeys(d, kv, **kw)
  kv = kv or kw
  try:
    yield old_values
  finally:
    popkeys(d, kv.keys(), old_values)

@contextmanager
def stackset(s, element, lock=None):
  ''' Context manager to add `element` to the set `s` and remove it on return.
      The element is neither added nor removed if it is already present.
  '''
  if element in s:
    yield
  else:
    if lock:
      with lock:
        s.add(element)
    else:
      s.add(element)
    try:
      yield
    finally:
      if lock:
        with lock:
          s.remove(element)
      else:
        s.remove(element)

def twostep(cmgr):
  ''' Return a generator which operates the context manager `cmgr`.

      The first iteration performs the "enter" phase and yields the result.
      The second iteration performs the "exit" phase and yields `None`.

      See also the `push_cmgr(obj,attr,cmgr)` function
      and its partner `pop_cmgr(obj,attr)`
      which form a convenient wrapper for this low level generator.

      The purpose of `twostep()` is to split any context manager's operation
      across two steps when the set up and tear down phases must operate
      in different parts of your code.
      A common situation is the `__enter__` and `__exit__` methods
      of another context manager class
      or the `setUp` and `tearDown` methods of a unit test case.

      *Note*:
      this function expects `cmgr` to be an existing context manager
      and _not_ the function which returns the context manager.

      In particular, if you define some function like this:

          @contextmanager
          def my_cmgr_func(...):
              ...
              yield
              ...

      then `my_cmgr_func(...)` returns a context manager instance
      and so the correct use of `twostep()` is like this:

          # steps broken out for clarity
          cmgr = my_cmgr_func(...)
          cmgr_iter = twostep(cmgr)
          next(cmgr_iter)   # set up
          next(cmgr_iter)   # tear down

      and _not_:

          cmgr_iter = twostep(my_cmgr_func)
          next(cmgr_iter)   # set up
          next(cmgr_iter)   # tear down

      Example use in a class (but really you should use
      `push_cmgr`/`pop_cmgr` instead):

          class SomeClass:
              def __init__(self, foo)
                  self.foo = foo
                  self._cmgr_ = None
              def __enter__(self):
                  self._cmgr_stepped = twostep(stackattrs(o, setting=foo))
                  self._cmgr = next(self._cmgr_stepped)
                  return self._cmgr
              def __exit__(self, *_):
                  next(self._cmgr_stepped)
                  self._cmgr = None
  '''
  with cmgr as enter:
    yield enter
  yield

def setup_cmgr(cmgr):
  ''' Run the set up phase of the context manager `cmgr`
      and return a callable which runs the tear down phase.

      This is a convenience wrapper for the lower level `twostep()` function
      which produces a two iteration generator from a context manager.

      Please see the `push_cmgr` function, a superior wrapper for `twostep()`.

      *Note*:
      this function expects `cmgr` to be an existing context manager.
      In particular, if you define some context manager function like this:

          @contextmanager
          def my_cmgr_func(...):
              ...
              yield
              ...

      then the correct use of `setup_cmgr()` is:

          teardown = setup_cmgr(my_cmgr_func(...))

      and _not_:

          cmgr_iter = setup_cmgr(my_cmgr_func)
          ...

      The purpose of `setup_cmgr()` is to split any context manager's operation
      across two steps when the set up and teardown phases must operate
      in different parts of your code.
      A common situation is the `__enter__` and `__exit__` methods
      of another context manager class.

      The call to `setup_cmgr()` performs the "enter" phase
      and returns the tear down callable.
      Calling that performs the tear down phase.

      Example use in a class:

          class SomeClass:
              def __init__(self, foo)
                  self.foo = foo
                  self._teardown = None
              def __enter__(self):
                  self._teardown = setup_cmgr(stackattrs(o, setting=foo))
              def __exit__(self, *_):
                  teardown, self._teardown = self._teardown, None
                  teardown()
  '''
  cmgr_twostep = twostep(cmgr)
  next(cmgr_twostep)

  def next2():
    try:
      next(cmgr_twostep)
    except StopIteration:
      pass

  return next2

def push_cmgr(o, attr, cmgr):
  ''' A convenience wrapper for `twostep(cmgr)`
      to run the `__enter__` phase of `cmgr` and save its value as `o.`*attr*`.
      Return the result of the `__enter__` phase.

      The `__exit__` phase is run by `pop_cmgr(o,attr)`,
      returning the return value of the exit phase.

      Example use in a unit test:

          class TestThing(unittest.TestCase):
              def setUp(self):
                  # save the temp dir path as self.dirpath
                  push_cmgr(self, 'dirpath', TemporaryDirectory())
              def tearDown(self):
                  # clean up the temporary directory, discard self.dirpath
                  pop_cmgr(self, 'dirpath')

      The `cs.testutils` `SetupTeardownMixin` class does this
      allowing the provision of a single `setupTeardown()` context manager method
      for test case setUp/tearDown.

      Doc test:

          >>> from os.path import isdir as isdirpath
          >>> from tempfile import TemporaryDirectory
          >>> from types import SimpleNamespace
          >>> obj = SimpleNamespace()
          >>> dirpath = push_cmgr(obj, 'path', TemporaryDirectory())
          >>> assert dirpath == obj.path
          >>> assert isdirpath(dirpath)
          >>> pop_cmgr(obj, 'path')
          >>> assert not hasattr(obj, 'path')
          >>> assert not isdirpath(dirpath)
  '''
  cmgr_twostep = twostep(cmgr)
  enter_value = next(cmgr_twostep)
  # pylint: disable=unnecessary-lambda-assignment
  pop_func = lambda: (popattrs(o, (attr,), pushed), next(cmgr_twostep))[1]
  pop_func_attr = '_push_cmgr__popfunc__' + attr
  pushed = pushattrs(o, **{attr: enter_value, pop_func_attr: pop_func})
  return enter_value

def pop_cmgr(o, attr):
  ''' Run the `__exit__` phase of a context manager commenced with `push_cmgr`.
      Restore `attr` as it was before `push_cmgr`.
      Return the result of `__exit__`.
  '''
  pop_func = getattr(o, '_push_cmgr__popfunc__' + attr)
  return pop_func()

@contextmanager
def stack_signals(signums, handler, additional=False):
  ''' Context manager to apply a handler function to `signums`
      using `signal.signal`.
      The old handlers are restored on exit from the context manager.

      If the optional `additional` argument is true,
      apply a handler which calls both the new handler and the old handler.
  '''
  stacked_signals = {}
  if additional:
    new_handler = handler

    # pylint: disable=function-redefined
    def handler(sig, frame):
      old_handler = stacked_signals[sig]
      new_handler(sig, frame)
      if old_handler:
        old_handler(sig, frame)

  if isinstance(signums, int):
    signums = [signums]
  try:
    for signum in signums:
      stacked_signals[signum] = signal.signal(signum, handler)
    yield
  finally:
    for signum, old_handler in stacked_signals.items():
      signal.signal(signum, old_handler)

class ContextManagerMixin:
  ''' A mixin to provide context manager `__enter__` and `__exit__` methods
      running the first and second steps of a single `__enter_exit__` generator method.

      *Note*: the `__enter_exit__` method is _not_ a context manager,
      but a short generator method.

      This makes it easy to use context managers inside `__enter_exit__`
      as the setup/teardown process, for example:

          def __enter_exit__(self):
              with open(self.datafile, 'r') as f:
                  yield f

      Like a context manager created via `@contextmanager`
      it performs the setup phase and then `yield`s the value for the `with` statement.
      If `None` is `yield`ed (as from a bare `yield`)
      then `self` is returned from `__enter__`.
      As with `@contextmanager`,
      if there was an exception in the managed suite
      then that exception is raised on return from the `yield`.

      *However*, and _unlike_ a `@contextmanager` method,
      the `__enter_exit__` generator _may_ also `yield`
      an additional true/false value to use as the result
      of the `__exit__` method, to indicate whether the exception was handled.
      This extra `yield` is _optional_ and if it is omitted the `__exit__` result
      will be `False` indicating that an exception was not handled.

      Here is a sketch of a method which can handle a `SomeException` specially:

          class CMgr(ContextManagerMixin):
              def __enter_exit__(self):
                  ... do some setup here ...
                  # Returning self is common, but might be any relevant value.
                  # Note that if you want `self`, you can just use a bare yield
                  # and ContextManagerMixin will provide `self` as the default.
                  enter_result = self
                  exit_result = False
                  try:
                      yield enter_result
                  except SomeException as e:
                      ... handle e ...
                      exit_result = True
                  finally:
                      ... do tear down here ...
                  yield exit_result
  '''

  def __enter__(self):
    ''' Run `super().__enter__` (if any)
        then the `__enter__` phase of `self.__enter_exit__()`.
    '''
    try:
      super_enter = super().__enter__
    except AttributeError:
      pass
    else:
      super_enter()
    eegen = self.__enter_exit__()
    enter_value = next(eegen)
    if enter_value is None:
      enter_value = self
    pushed = {}
    pushed.update(pushattrs(self, _ContextManagerMixin__state=(eegen, pushed)))
    return enter_value

  def __exit__(self, exc_type, exc_value, traceback):
    ''' Run the `__exit__` step of `self.__enter_exit__()`,
        then `super().__exit__` (if any).
    '''
    # get generator, restore attributes
    eegen, pushed = self._ContextManagerMixin__state
    popattrs(self, ('_ContextManagerMixin__state',), pushed)
    # return to the generator to run the __exit__ phase
    if exc_type:
      exit_result = eegen.throw(exc_type, exc_value, traceback)
    else:
      try:
        exit_result = next(eegen)
      except StopIteration:
        # there was no optional extra yield
        exit_result = None
    if exit_result:
      # exception handled, conceal it from the super method
      exc_type, exc_value, traceback = None, None, None
    try:
      super_exit = super().__exit__
    except AttributeError:
      # no super __exit__, skip
      pass
    else:
      if super_exit(exc_type, exc_value, traceback):
        exit_result = True
    return exit_result

  @classmethod
  @contextmanager
  def as_contextmanager(cls, self):
    ''' Run the generator from the `cls` class specific `__enter_exit__`
        method via `self` as a context manager.

        Example from `RunState` which subclasses `HasThreadState`,
        both of which are `ContextManagerMixin` subclasses:

            class RunState(HasThreadState):
                .....
                def __enter_exit__(self):
                    with HasThreadState.as_contextmanager(self):
                        ... RunState context manager stuff ...

        This runs the `HasThreadState` context manager
        around the main `RunState` context manager.
    '''
    eegen = cls.__enter_exit__(self)
    try:
      entered = next(eegen)
    except StopIteration as e:
      error("expected enter value from next(eegen): %s", e)
      return
    try:
      yield entered
    except Exception as e:  # pylint: disable=broad-exception-caught
      exit_result = eegen.throw(type(e), e, e.__traceback__)
      if not exit_result:
        raise
    else:
      try:
        exit_result = next(eegen)
      except StopIteration:
        pass

def withif(obj):
  ''' Return a context manager for `obj`.
      If `obj` has an `__enter__` attribute, return `obj`
      otherwise return `nullcontext()`.

      Example:

          with withif(inner_mapping):
            ... work with inner_mapping ...
  '''
  if hasattr(obj, '__enter__'):
    return obj
  return nullcontext()

def _withall(obj_it):
  ''' A generator to enter every object `obj` from the iterator
      `obj_it` using `with obj:`, then yield.
  '''
  try:
    obj = next(obj_it)
  except StopIteration:
    yield
  else:
    with obj:
      yield from _withall(obj_it)

@contextmanager
def withall(objs):
  ''' Enter every object `obj` in `obj_list` except those which are `None`
      using `with obj:`, then yield.
  '''
  yield from _withall(obj for obj in objs if obj is not None)

@contextmanager
def reconfigure_file(f, **kw):
  ''' Context manager flavour of `TextIOBase.reconfigure`.
  '''
  old = {k: getattr(f, k) for k in kw}
  try:
    f.reconfigure(**kw)
    yield
  finally:
    f.reconfigure(**old)
