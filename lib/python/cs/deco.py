#!/usr/bin/python
#
# Decorators.
#   - Cameron Simpson <cs@cskk.id.au> 02jul2017
#

r'''
Assorted decorator functions.
'''

from collections import defaultdict
from contextlib import contextmanager
from inspect import isgeneratorfunction
import sys
import time
import traceback
from cs.gimmicks import warning

__version__ = '20210823'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.gimmicks'],
}

def fmtdoc(func):
  ''' Decorator to replace a function's docstring with that string
      formatted against the function's module `__dict__`.

      This supports simple formatted docstrings:

          ENVVAR_NAME = 'FUNC_DEFAULT'

          @fmtdoc
          def func():
              """Do something with os.environ[{ENVVAR_NAME}]."""
              print(os.environ[ENVVAR_NAME])

      This gives `func` this docstring:

          Do something with os.environ[FUNC_DEFAULT].

      *Warning*: this decorator is intended for wiring "constants"
      into docstrings, not for dynamic values. Use for other types
      of values should be considered with trepidation.
  '''
  func.__doc__ = func.__doc__.format(**sys.modules[func.__module__].__dict__)
  return func

def decorator(deco):
  ''' Wrapper for decorator functions to support optional arguments.

      The actual decorator function ends up being called as:

          mydeco(func, *da, **dkw)

      allowing `da` and `dkw` to affect the behaviour of the decorator `mydeco`.

      Examples:

          @decorator
          def mydeco(func, *da, kw=None):
            ... decorate func subject to the values of da and kw

          @mydeco
          def func1(...):
            ...

          @mydeco('foo', arg2='bah')
          def func2(...):
            ...
  '''

  def metadeco(*da, **dkw):
    ''' Compute either the wrapper function for `func`
        or a decorator expecting to get `func` when used.

        If there is at least one positional parameter
        and it is callable the it is presumed to be the function to decorate;
        decorate it directly.

        Otherwise return a decorator using the provided arguments,
        ready for the subsequent function.
    '''
    if len(da) >= 1 and callable(da[0]):
      # `func` is already supplied, decorate it now.
      func = da[0]
      da = tuple(da[1:])
      decorated = deco(func, *da, **dkw)
      if decorated is not func:
        # pretty up the returned wrapper
        try:
          decorated.__name__ = getattr(func, '__name__', str(func))
        except AttributeError:
          pass
        if not getattr(decorated, '__doc__', None):
          decorated.__doc__ = getattr(func, '__doc__', '')
        func_module = getattr(func, '__module__', None)
        try:
          decorated.__module__ = func_module
        except AttributeError:
          pass
      return decorated

    # `func` is not supplied, collect the arguments supplied and return a
    # decorator which takes the subsequent callable and returns
    # `deco(func, *da, **kw)`.
    def overdeco(func):
      decorated = deco(func, *da, **dkw)
      decorated.__doc__ = getattr(func, '__doc__', '')
      func_module = getattr(func, '__module__', None)
      try:
        decorated.__module__ = func_module
      except AttributeError:
        pass
      return decorated

    return overdeco

  metadeco.__doc__ = getattr(deco, '__doc__', '')
  metadeco.__module__ = getattr(deco, '__module__', None)
  return metadeco

@decorator
def contextdecorator(cmgrfunc):
  ''' A decorator for a context manager function `cmgrfunc`
      which turns it into a decorator for other functions.

      This supports easy implementation of "setup" and "teardown"
      code around other functions without the tedium of defining
      the wrapper function itself. See the examples below.

      The resulting context manager accepts an optional keyword
      parameter `provide_context`, default `False`. If true, the
      context returned from the context manager is provided as the
      first argument to the call to the wrapped function.

      Note that the context manager function `cmgrfunc`
      has _not_ yet been wrapped with `@contextmanager`,
      that is done by `@contextdecorator`.

      This decorator supports both normal functions and generator functions.

      With a normal function the process is:
      * call the context manager with `(func,a,kw,*da,**dkw)`,
        returning `ctxt`,
        where `da` and `dkw` are the positional and keyword parameters
        supplied when the decorator was defined.
      * within the context
        return the value of `func(ctxt,*a,**kw)` if `provide_context` is true
        or the value of `func(*a,**kw)` if not (the default)

      With a generator function the process is:
      * obtain an iterator by calling `func(*a,**kw)`
      * for iterate over the iterator, yielding its results,
        by calling the context manager with `(func,a,kw,**da,**dkw)`,
        around each `next()`
      Note that it is an error to provide a true value for `provide_context`
      if the decorated function is a generator function.

      Some examples follow.

      Trace the call and return of a specific function:

          @contextdecorator
          def tracecall(func, a, kw):
              """ Trace the call and return from some function.
                  This can easily be adapted to purposes such as timing a
                  function call or logging use.
              """
              print("call %s(*%r,**%r)" % (func, a, kw))
              try:
                yield
              except Exception as e:
                print("exception from %s(*%r,**%r): %s" % (func, a, kw, e))
                raise
              else:
                print("return from %s(*%r,**%r)" % (func, a, kw))

          @tracecall
          def f():
              """ Some function to trace.
              """

          @tracecall(provide_context=True):
          def f(ctxt, *a, **kw):
              """ A function expecting the context object as its first argument,
                  ahead of whatever other arguments it would normally require.
              """

      See who is making use of a generator's values,
      when a generator might be invoked in one place and consumed elsewhere:

          from cs.py.stack import caller

          @contextdecorator
          def genuser(genfunc, *a, **kw):
              user = caller(-4)
              print(f"iterate over {genfunc}(*{a!r},**{kw!r}) from {user}")
              yield

          @genuser
          def linesof(filename):
              with open(filename) as f:
                  yield from f

          # obtain a generator of lines here
          lines = linesof(__file__)

          # perhaps much later, or in another function
          for lineno, line in enumerate(lines, 1):
              print("line %d: %d words" % (lineno, len(line.split())))

      Turn on "verbose mode" around a particular function:

          import sys
          import threading
          from cs.context import stackattrs

          class State(threading.local):
              def __init__(self):
                  # verbose if stderr is on a terminal
                  self.verbose = sys.stderr.isatty()

          # per thread global state
          state = State()

          @contextdecorator
          def verbose(func):
              with stackattrs(state, verbose=True) as old_attrs:
                  if not old_attrs['verbose']:
                      print(f"enabled verbose={state.verbose} for function {func}")
                  # yield the previous verbosity as the context
                  yield old_attrs['verbose']

          # turn on verbose mode
          @verbose
          def func(x, y):
              if state.verbose:
                  # print if verbose
                  print("x =", x, "y =", y)

          # turn on verbose mode and also pass in the previous state
          # as the first argument
          @verbose(provide_context=True):
          def func2(old_verbose, x, y):
              if state.verbose:
                  # print if verbose
                  print("old_verbosity =", old_verbose, "x =", x, "y =", y)
  '''
  # turn the function into a context manager
  cmgr = contextmanager(cmgrfunc)

  # prepare a new decorator which wraps functions in a context
  # manager using `cmgrfunc`
  @decorator
  def cmgrdeco(func, *da, provide_context=False, **dkw):
    ''' Decorator for functions which wraps calls to the function
        in a context manager, optionally supplying the context
        as the first argument to the called function.
    '''
    if isgeneratorfunction(func):
      if provide_context:
        raise ValueError(
            "provide_context may not be true when func:%s is a generator" %
            (func,)
        )

      def wrapped(*a, **kw):
        ''' Wrapper function:
            * obtain an iterator by calling `func(*a,**kw)`
            * iterate over the iterator, yielding its results,
              by calling the context manager with `(func,a,kw,**da,**dkw)`,
              around each `next()`
        '''
        it = func(*a, **kw)
        while True:
          with cmgr(func, a, kw, *da, **dkw):
            try:
              value = next(it)
            except StopIteration:
              break
          yield value

    else:

      def wrapped(*a, **kw):
        ''' Wrapper function:
            * call the context manager with `(func,a,kw,**da,**dkw)`,
              returning `ctxt`
            * within the context
              return the value of `func(ctxt,*a,**kw)`
              if `provide_context` is true
              or the value of `func(*a,**kw)` if not (the default)
        '''
        with cmgr(func, a, kw, *da, **dkw) as ctxt:
          if provide_context:
            a = a.insert(0, ctxt)
          return func(*a, **kw)

    return wrapped

  return cmgrdeco

@decorator
def logging_wrapper(log_call, stacklevel_increment=1):
  ''' Decorator for logging call shims
      which bumps the `stacklevel` keyword argument so that the logging system
      chooses the correct frame to cite in messages.

      Note: has no effect on Python < 3.8 because `stacklevel` only
      appeared in that version.
  '''
  if (sys.version_info.major, sys.version_info.minor) < (3, 8):
    # do not wrap older Python log calls, no stacklevel keyword argument
    return log_call

  def log_func_wrapper(*a, **kw):
    stacklevel = kw.pop('stacklevel', 1)
    return log_call(*a, stacklevel=stacklevel + stacklevel_increment + 1, **kw)

  log_func_wrapper.__name__ = log_call.__name__
  log_func_wrapper.__doc__ = log_call.__doc__
  return log_func_wrapper

@decorator
def cachedmethod(
    method, attr_name=None, poll_delay=None, sig_func=None, unset_value=None
):
  ''' Decorator to cache the result of an instance or class method
      and keep a revision counter for changes.

      The cached values are stored on the instance (`self`).
      The revision counter supports the `@revised` decorator.

      This decorator may be used in 2 modes.
      Directly:

          @cachedmethod
          def method(self, ...)

      or indirectly:

          @cachedmethod(poll_delay=0.25)
          def method(self, ...)

      Optional keyword arguments:
      * `attr_name`: the basis name for the supporting attributes.
        Default: the name of the method.
      * `poll_delay`: minimum time between polls; after the first
        access, subsequent accesses before the `poll_delay` has elapsed
        will return the cached value.
        Default: `None`, meaning no poll delay.
      * `sig_func`: a signature function, which should be significantly
        cheaper than the method. If the signature is unchanged, the
        cached value will be returned. The signature function
        expects the instance (`self`) as its first parameter.
        Default: `None`, meaning no signature function;
        the first computed value will be kept and never updated.
      * `unset_value`: the value to return before the method has been
        called successfully.
        Default: `None`.

      If the method raises an exception, this will be logged and
      the method will return the previously cached value,
      unless there is not yet a cached value
      in which case the exception will be reraised.

      If the signature function raises an exception
      then a log message is issued and the signature is considered unchanged.

      An example use of this decorator might be to keep a "live"
      configuration data structure, parsed from a configuration
      file which might be modified after the program starts. One
      might provide a signature function which called `os.stat()` on
      the file to check for changes before invoking a full read and
      parse of the file.

      *Note*: use of this decorator requires the `cs.pfx` module.
  '''
  from cs.pfx import Pfx
  if poll_delay is not None and poll_delay <= 0:
    raise ValueError("poll_delay <= 0: %r" % (poll_delay,))
  if poll_delay is not None and poll_delay <= 0:
    raise ValueError(
        "invalid poll_delay, should be >0, got: %r" % (poll_delay,)
    )

  attr = attr_name if attr_name else method.__name__
  val_attr = '_' + attr
  sig_attr = val_attr + '__signature'
  rev_attr = val_attr + '__revision'
  lastpoll_attr = val_attr + '__lastpoll'

  # pylint: disable=too-many-branches
  def wrapper(self, *a, **kw):
    with Pfx("%s.%s", self, attr):
      now = None
      value0 = getattr(self, val_attr, unset_value)
      sig0 = getattr(self, sig_attr, None)
      sig = getattr(self, sig_attr, None)
      if value0 is unset_value:
        # value unknown, needs compute
        pass
      # we have a cached value for return in the following logic
      elif poll_delay is None:
        # always poll
        pass
      else:
        lastpoll = getattr(self, lastpoll_attr)
        now = time.time()
        if now - lastpoll < poll_delay:
          # reuse cache
          return value0
      # not too soon, try to update the value
      # update the poll time if we use it
      if poll_delay is not None:
        now = now or time.time()
        setattr(self, lastpoll_attr, now)
      # check the signature if provided
      # see if the signature is unchanged
      if sig_func is not None:
        try:
          sig = sig_func(self)
        except Exception as e:  # pylint: disable=broad-except
          # signature function fails, use the cache
          warning("sig func %s(self): %s", sig_func, e, exc_info=True)
          return value0
        if sig0 is not None and sig0 == sig:
          # signature unchanged
          return value0
        # update signature
        setattr(self, sig_attr, sig)
      # compute the current value
      try:
        value = method(self, *a, **kw)
      except Exception as e:  # pylint: disable=broad-except
        # computation fails, return cached value
        if value0 is unset_value:
          # no cached value
          raise
        warning("exception calling %s(self): %s", method, e, exc_info=True)
        return value0
      # update the cache
      setattr(self, val_attr, value)
      # bump revision if the value changes
      # noncomparable values are always presumed changed
      changed = value0 is unset_value or value0 is not value
      if not changed:
        try:
          changed = value0 != value
        except TypeError:
          changed = True
      if changed:
        setattr(self, rev_attr, getattr(self, rev_attr, 0) + 1)
      return value

  return wrapper

@decorator
def OBSOLETE(func, suggestion=None):
  ''' Decorator for obsolete functions.

      Use:

          @OBSOLETE
          def func(...):

      This emits a warning log message before calling the decorated function.
  '''

  def wrapped(*args, **kwargs):
    ''' Wrap `func` to emit an "OBSOLETE" warning before calling `func`.
    '''
    frame = traceback.extract_stack(None, 2)[0]
    caller = frame[0], frame[1]
    try:
      callers = func._OBSOLETE_callers
    except AttributeError:
      callers = func._OBSOLETE_callers = set()
    if caller not in callers:
      callers.add(caller)
      warning(
          "OBSOLETE call to %s:%d %s(), called from %s:%d %s",
          func.__code__.co_filename, func.__code__.co_firstlineno,
          func.__name__, frame[0], frame[1], frame[2]
      )
    return func(*args, **kwargs)

  funcname = getattr(func, '__name__', str(func))
  funcdoc = getattr(func, '__doc__', None) or ''
  doc = "OBSOLETE FUNCTION " + funcname
  if suggestion:
    doc += ' suggestion: ' + suggestion
  wrapped.__name__ = '@OBSOLETE(%s)' % (funcname,)
  wrapped.__doc__ = doc + '\n\n' + funcdoc
  return wrapped

@OBSOLETE(suggestion='cachedmethod')
def cached(*a, **kw):
  ''' Former name for @cachedmethod.
  '''
  return cachedmethod(*a, **kw)

def contextual(func):
  ''' Wrap a simple function as a context manager.

      This was written to support users of `@strable`,
      which requires its `open_func` to return a context manager;
      this turns an arbitrary function into a context manager.

      Example promoting a trivial function:

          >>> f = lambda: 3
          >>> cf = contextual(f)
          >>> with cf() as x: print(x)
          3
  '''

  @contextmanager
  def cmgr(*a, **kw):
    ''' Wrapper for `func` as a context manager.
    '''
    yield func(*a, **kw)

  func_name = getattr(func, '__name__', str(func))
  cmgr.__name__ = '@contextual(%s)' % func_name
  cmgr.__doc__ = func.__doc__
  return cmgr

@decorator
def strable(func, open_func=None):
  ''' Decorator for functions which may accept a `str`
      instead of their core type.

      Parameters:
      * `func`: the function to decorate
      * `open_func`: the "open" factory to produce the core type
        if a string is provided;
        the default is the builtin "open" function.
        The returned value should be a context manager.
        Simpler functions can be decorated with `@contextual`
        to turn them into context managers if need be.

      The usual (and default) example is a function to process an
      open file, designed to be handed a file object but which may
      be called with a filename. If the first argument is a `str`
      then that file is opened and the function called with the
      open file.

      Examples:

          @strable
          def count_lines(f):
            return len(line for line in f)

          class Recording:
            "Class representing a video recording."
            ...
          @strable(open_func=Recording)
          def process_video(r):
            ... do stuff with `r` as a Recording instance ...

      *Note*: use of this decorator requires the `cs.pfx` module.
  '''
  from cs.pfx import Pfx
  if open_func is None:
    open_func = open

  if isgeneratorfunction(func):

    def accepts_str(arg, *a, **kw):
      if isinstance(arg, str):
        with Pfx(arg):
          with open_func(arg) as opened:
            for item in func(opened, *a, **kw):
              yield item
      else:
        for item in func(arg, *a, **kw):
          yield item
  else:

    def accepts_str(arg, *a, **kw):
      if isinstance(arg, str):
        with Pfx(arg):
          with open_func(arg) as opened:
            return func(opened, *a, **kw)
      return func(arg, *a, **kw)

  return accepts_str

def observable_class(property_names, only_unequal=False):
  ''' Class decorator to make various instance attributes observable.

      Parameters:
      * `property_names`:
        an interable of instance property names to set up as
        observable properties. As a special case a single `str` can
        be supplied if only one attribute is to be observed.
      * `only_unequal`:
        only call the observers if the new property value is not
        equal to the previous proerty value. This requires property
        values to be comparable for inequality.
        Default: `False`, meaning that all updates will be reported.
  '''

  if isinstance(property_names, str):
    property_names = (property_names,)

  def make_observable_class(cls):
    ''' Annotate the class `cls` with observable properties.
    '''

    # push the per instance initialisation
    old_init = cls.__init__

    def new_init(self, *a, **kw):
      ''' New init, much like the old init...
      '''
      self._observable_class__observers = defaultdict(set)
      old_init(self, *a, **kw)

    cls.__init__ = new_init

    def add_observer(self, attr, observer):
      ''' Add an observer on `.attr` to this instance.
      '''
      self._observable_class__observers[attr].add(observer)

    cls.add_observer = add_observer

    def remove_observer(self, attr, observer):
      ''' Remove an observer on `.attr` from this instance.
      '''
      self._observable_class__observers[attr].remove(observer)

    cls.remove_observer = remove_observer

    def report_observation(self, attr):
      ''' Notify all the observers of the current value of `attr`.
      '''
      val_attr = '_' + attr
      value = getattr(self, val_attr, None)
      for observer in self._observable_class__observers[attr]:
        try:
          observer(self, attr, value)
        except Exception as e:  # pylint: disable=broad-except
          warning(
              "%s.%s=%r: observer %s(...) raises: %s",
              self,
              val_attr,
              value,
              observer,
              e,
              exc_info=True
          )

    cls.report_observation = report_observation

    def make_property(cls, attr):
      ''' Make `cls.attr` into a property which reports setattr events.
      '''
      val_attr = '_' + attr

      def getter(self):
        return getattr(self, val_attr)

      getter.__name__ = attr
      get_prop = property(getter)
      setattr(cls, attr, get_prop)

      def setter(self, new_value):
        ''' Set the attribute value and tell all the observers.
        '''
        old_value = getattr(self, val_attr, None)
        setattr(self, val_attr, new_value)
        if not only_unequal or old_value != new_value:
          self.report_observation(attr)

      setter.__name__ = attr
      set_prop = get_prop.setter(setter)
      setattr(cls, attr, set_prop)

    for property_name in property_names:
      if hasattr(cls, property_name):
        raise ValueError("%s.%s already exists" % (cls, property_name))
      make_property(cls, property_name)

    return cls

  return make_observable_class

def _teststuff():

  @contextdecorator
  def tracecall(func, a, kw):
    print("call %s(*%r,**%r)" % (func, a, kw))
    yield 9
    print("return from %s(*%r,**%r)" % (func, a, kw))

  @tracecall
  def f(*a, **kw):
    print("hello from f: a=%r, kw=%r" % (a, kw))
    return "V"

  @tracecall
  def g(r):
    yield from range(r)

  @tracecall(provide_context=True)
  def f2(ctxt, *a, **kw):
    print("hello from f2: ctxt=%s, a=%r, kw=%r" % (ctxt, a, kw))
    return "V2"

  v = f("abc", y=1)
  print("v =", v)
  v = f2("abc2", y=1)
  print("v2 =", v)
  gg = g(9)
  for i in gg:
    print("i =", i)
  sys.exit(1)

  # pylint: disable=too-few-public-methods
  class Foo:
    ''' Dummy class.
    '''

    @cachedmethod(poll_delay=2)
    def x(self, arg):
      ''' Dummy `x` method.
      '''
      return str(self) + str(arg)

  F = Foo()
  y = F.x(1)
  print("F.x() ==>", y)
  y = F.x(1)
  print("F.x() ==>", y)
  y = F.x(2)
  print("F.x() ==>", y)
  time.sleep(3)
  y = F.x(3)
  print("F.x() ==>", y)

if __name__ == '__main__':
  _teststuff()
