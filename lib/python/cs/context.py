#!/usr/bin/env python

''' Assorted context managers.
'''

from contextlib import contextmanager
import threading
try:
  from contextlib import nullcontext  # pylint: disable=unused-import,ungrouped-imports
except ImportError:

  @contextmanager
  def nullcontext():
    ''' A simple `nullcontext` for older Pythons
    '''
    yield None

__version__ = '20211115.1-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
}

def pushattrs(o, **attr_values):
  ''' The "push" part of `stackattrs`.
      Push `attr_values` onto `o` as attributes,
      return the previous attribute values in a dict.

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
      Attributes not present are not present in the mapping.

      Restoration includes deleting attributes which were not present
      initially.

      This makes it easy to adjust temporarily some shared context object
      without having to pass it through the call stack.

      See `stackkeys` for a flavour of this for mappings.

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

class StackableState(threading.local):
  ''' An object which can be called as a context manager
      to push changes to its attributes.

      Example:

          >>> state = StackableState(a=1, b=2)
          >>> state.a
          1
          >>> state.b
          2
          >>> state
          StackableState(a=1,b=2)
          >>> with state(a=3, x=4):
          ...     print(state)
          ...     print("a", state.a)
          ...     print("b", state.b)
          ...     print("x", state.x)
          ...
          StackableState(a=3,b=2,x=4)
          a 3
          b 2
          x 4
          >>> state.a
          1
          >>> state
          StackableState(a=1,b=2)
  '''

  def __init__(self, **kw):
    super().__init__()
    for k, v in kw.items():
      setattr(self, k, v)

  def __str__(self):
    return "%s(%s)" % (
        type(self).__name__,
        ','.join(["%s=%s" % (k, v) for k, v in sorted(self.__dict__.items())])
    )

  __repr__ = __str__

  @contextmanager
  def __call__(self, **kw):
    ''' Calling an instance is a context manager yielding `self`
        with attributes modified by `kw`.
    '''
    with stackattrs(self, **kw):
      yield self

def pushkeys(d, **key_values):
  ''' The "push" part of `stackkeys`.
      Push `key_values` onto `d` as key values.
      return the previous key values in a dict.

      This can be useful in hooks/signals/callbacks,
      where you cannot inline a context manager.
  '''
  old_values = {}
  for key, value in key_values.items():
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
def stackkeys(d, **key_values):
  ''' Context manager to push new values for the key values of `d`
      and to restore them afterward.
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
  old_values = pushkeys(d, **key_values)
  try:
    yield old_values
  finally:
    popkeys(d, key_values.keys(), old_values)

def twostep(cmgr):
  ''' Return a generator which operates the context manager `cmgr`.

      The first iteration performs the "enter" phase and yields the result.
      The second iteration performs the "exit" phase and yields `None`.

      See also the `push_cmgr(obj,attr,cmgr)` function
      and its partner `pop_cmgr(obj,attr)`
      which form a convenience wrapper for this low level generator.

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

      then the correct use of `twostep()` is:

          cmgr_iter = twostep(my_cmgr_func(...))
          next(cmgr_iter)   # set up
          next(cmgr_iter)   # tear down

      and _not_:

          cmgr_iter = twostep(my_cmgr_func)
          next(cmgr_iter)   # set up
          next(cmgr_iter)   # tear down

      Example use in a class (but really, use `push_cmgr`/`pop_cmgr` instead):

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
  return lambda: next(cmgr_twostep)

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

      *However*, and _unlike_ an `@contextmanager` method,
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
                  # Note that ifyou want `self`, you can just use a bare yield
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
    try:
      if exc_type:
        exit_result = eegen.throw(exc_type, exc_value, traceback)
      else:
        exit_result = next(eegen)
    except StopIteration:
      # there was no optional extra yield
      exit_result = None
    else:
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
