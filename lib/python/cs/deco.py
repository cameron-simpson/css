#!/usr/bin/python
#
# Decorators.
# - Cameron Simpson <cs@cskk.id.au> 02jul2017
#

r'''
Assorted function decorators.
'''

from collections import defaultdict
from contextlib import contextmanager
from inspect import isgeneratorfunction, ismethod, signature, Parameter
import sys
import traceback
import typing

from cs.gimmicks import warning
from cs.typingutils import is_optional

__version__ = '20250531'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.gimmicks', 'cs.typingutils'],
}

def ALL(func):
  ''' Include this function's name in its module's `__all__` list.

      Example:

          from cs.deco import ALL

          __all__ = []

          def obscure_function(...):
              ...

          @ALL
          def well_known_function(...):
              ...
  '''
  sys.modules[func.__module__].__all__.append(func.__name__)
  return func

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
  ''' A decorator for decorator functions to support optional arguments
      and also to allow specifying additional attributes to apply
      to the decorated function.

      The decorated function is updated by `functools.update_wrapper`
      and also has its `__name__` and `__doc__` set to those from
      before the decoration.

      The return of such a decorator is _usually_ the conventional
      decorated function, but may alternatively be a `(decorated,attrs)`
      2-tuple where `attrs` is a mapping of additional attributes
      to apply to `decorated`.
      This allows overriding the restored `__name__` and `__doc__`
      from above, and potentially other useful attributes.

      The actual decorator function (eg `mydeco`) ends up being called as:

          mydeco(func, *da, **dkw)

      allowing `da` and `dkw` to affect the behaviour of the decorator `mydeco`.

      Examples:

          # define your decorator as if always called with func and args
          @decorator
          def mydeco(func, *da, arg2=None):
            ... decorate func subject to the values of da and arg2

          # mydeco called with defaults
          @mydeco
          def func1(...):
            ...

          @ mydeco called with nondefault arguments
          @mydeco('foo', arg2='bah')
          def func2(...):
            ...
  '''

  def decorate(func, *dargs, **dkwargs):
    ''' Final decoration when we have the function and the decorator arguments.
    '''
    # First, collect the attributes of the function/class before deco() has at it.
    func_doc = getattr(func, '__doc__', None) or ''
    func_module = getattr(func, '__module__', None)
    func_name = getattr(func, '__name__', str(func))
    # Now decorate func.
    decorated = deco(func, *dargs, **dkwargs)
    # accept either the a function or a (function,attrmap) 2-tuple
    try:
      decorated, func_attrs = decorated
    except TypeError:
      func_attrs = {}
    # Catch mucked decorators which forget to return the new function.
    assert decorated is not None, f'@decorator @{deco=} of {func=} returned None'
    if decorated is not func:
      # We got a wrapper function back, pretty up the returned wrapper.
      try:
        from functools import update_wrapper
      except ImportError:
        pass
      else:
        try:
          update_wrapper(decorated, func)
        except AttributeError:
          pass
      try:
        decorated.__name__ = func_name
      except AttributeError:
        pass
      try:
        decorated.__doc__ = func_doc
      except AttributeError:
        warning("cannot set __doc__ on %r", decorated)
      try:
        decorated.__module__ = func_module
      except AttributeError:
        pass
    # apply any func_attrs
    for attr, value in func_attrs.items():
      setattr(decorated, attr, value)
    return decorated

  def metadeco(*da, **dkw):
    ''' Compute either the wrapper function for `func`
        or a decorator expecting to get `func` when used.

        If there is at least one positional parameter
        and it is callable the it is presumed to be the function to decorate;
        decorate it directly.

        Otherwise return a decorator using the provided arguments,
        ready for the subsequent function.
    '''
    if len(da) > 0 and callable(da[0]):
      # `func` is already supplied, pop it off and decorate it now.
      func = da[0]
      da = tuple(da[1:])
      return decorate(func, *da, **dkw)

    # `func` is not supplied, collect the arguments supplied and return a
    # decorator which takes the subsequent callable and returns
    # `deco(func, *da, **kw)`.
    return lambda func: decorate(func, *da, **dkw)

  metadeco.__name__ = getattr(deco, '__name__', repr(deco))
  metadeco.__doc__ = getattr(deco, '__doc__', '')
  metadeco.__module__ = getattr(deco, '__module__', None)
  return metadeco

@decorator
def attr(func, **attrs):
  ''' A decorator to set attributes on a function.
      Example:

          @attr(hook_names=('hook1', 'hook2'))
          def func():
              .....

      This is just a more overt and clear form of:

          def func():
              .....

          func.hook_names=('hook1', 'hook2')
  '''
  if not attrs:
    raise ValueError('@attr: no attributes upplied')
  for attr, value in attrs.items():
    setattr(func, attr, value)
  return func

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
            a = [ctxt] + list(a)
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
def OBSOLETE(func, suggestion=None):
  ''' A decorator for obsolete functions or classes.

      Use:

          @OBSOLETE
          def func(...):

      or

          @OBSOLETE("new_func_name")
          def func(...):

      This emits a warning log message before calling the decorated function.
      Only one warning is emitted per calling location.
  '''

  callers = set()

  def OBSOLETE_func_wrapper(*args, **kwargs):
    ''' Wrap `func` to emit an "OBSOLETE" warning before calling `func`.
    '''
    frame = traceback.extract_stack(None, 2)[0]
    caller = frame[0], frame[1]
    if caller not in callers:
      callers.add(caller)
      prefix = (
          "OBSOLETE call" if suggestion is None else
          ("OBSOLETE (suggest %r) call" % suggestion)
      )
      fmt = "%s to %s:%d:%s(), called from %s:%d:%s"
      fmtargs = [
          prefix, func.__code__.co_filename, func.__code__.co_firstlineno,
          func.__name__, frame[0], frame[1], frame[2]
      ]
      warning(fmt, *fmtargs)
    return func(*args, **kwargs)

  funcname = getattr(func, '__name__', str(func))
  funcdoc = getattr(func, '__doc__', None) or ''
  doc = "OBSOLETE " + funcname
  func.__doc__ = doc + '\n\n' + funcdoc
  if suggestion:
    doc += ' suggestion: ' + suggestion
  OBSOLETE_func_wrapper.__name__ = '@OBSOLETE(%s)' % (funcname,)
  return OBSOLETE_func_wrapper

@OBSOLETE(suggestion='cs.cache.cachedmethod')
def cached(*a, **kw):
  ''' Former name for @cachedmethod.
  '''
  from cs.cache import cachedmethod
  return cachedmethod(*a, **kw)

@OBSOLETE(suggestion='cs.cache.cachedmethod')
def cachedmethod(*a, **kw):
  ''' @cachedmethod is now in cs.cache.
  '''
  from cs.cache import cachedmethod
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
  from cs.pfx import Pfx  # pylint: disable=import-outside-toplevel
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

  # pylint: disable=protected-access
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

@decorator
def default_params(func, _strict=False, **param_defaults):
  ''' Decorator to provide factory functions for default parameters.

      This decorator accepts the following keyword parameters:
      * `_strict`: default `False`; if true only replace genuinely
        missing parameters; if false also replace the traditional
        `None` placeholder value
      The remaining keyword parameters are factory functions
      providing the respective default values.

      Atypical one off direct use:

          @default_params(dbconn=open_default_dbconn,debug=lambda:settings.DB_DEBUG_MODE)
          def dbquery(query, *, dbconn):
              dbconn.query(query)

      Typical use as a decorator factory:

          # in your support module
          uses_ds3 = default_params(ds3client=get_ds3client)

          # calling code which needs a ds3client
          @uses_ds3
          def do_something(.., *, ds3client, ...):
              ... make queries using ds3client ...

      This replaces the standard boilerplate and avoids replicating
      knowledge of the default factory as exhibited in this legacy code:

          def do_something(.., *, ds3client=None, ...):
              if ds3client is None:
                  ds3client = get_ds3client()
              ... make queries using ds3client ...
  '''
  if not param_defaults:
    raise ValueError("@default_params(%s): no defaults?" % (func,))

  def update_kw(kw):
    ''' Update keyword parameters `kw` from the `param_defaults`.
    '''
    for param_name, param_default in param_defaults.items():
      try:
        v = kw[param_name]
      except KeyError:
        kw[param_name] = param_default()
      else:
        if v is None and not _strict:
          kw[param_name] = param_default()

  if isgeneratorfunction(func):

    def defaulted_func(*a, **kw):
      update_kw(kw)
      yield from func(*a, **kw)
  else:

    def defaulted_func(*a, **kw):
      update_kw(kw)
      return func(*a, **kw)

  defaulted_func.__name__ = func.__name__
  # TODO: get the indent from some aspect of stripped_dedent
  defaulted_func.__doc__ = '\n      '.join(
      [
          getattr(func, '__doc__', '') or '',
          '',
          'This function also accepts the following optional keyword parameters:',
          *[
              '* `%s`: default from `%s()`' % (param_name, param_default)
              for param_name, param_default in sorted(param_defaults.items())
          ],
      ]
  )
  sig0 = signature(func)
  sig = sig0
  modified_params = []
  for param in sig0.parameters.values():
    modified_param = None
    try:
      param_default = param_defaults[param.name]
    except KeyError:
      pass
    else:
      modified_param = param.replace(
          annotation=typing.Optional[param.annotation],
          default=None if param_default is param.empty else param_default,
      )
    if modified_param is None:
      modified_param = param.replace()
    modified_params.append(modified_param)
  sig = sig.replace(parameters=modified_params)
  defaulted_func.__signature__ = sig
  return defaulted_func

@decorator
def uses_cmd_options(
    func, _strict=False, _options_param_name='options', **option_defaults
):
  ''' A decorator to provide default keyword arguments
      from the prevailing `cs.cmdutils.BaseCommandOptions`
      if available, otherwise from `option_defaults`.

      This exists to provide plumbing free access to options set
      up by a command line invocation using `cs.cmdutils.BaseCommand`.

      If no `option_defaults` are provided, a single `options`
      keyword argument is provided which is the prevailing
      `BaseCommand.Options` instance.

      The decorator accepts two optional "private" keyword arguments
      commencing with underscores:
      * `_strict`: default `False`; if true then an `option_defaults`
        will only be applied if the argument is _missing_ from the
        function arguments, otherwise it will be applied if the
        argument is missing or `None`
      * `_options_param_name`: default `'options'`; this is the
        name of the single `options` keyword argument which will be
        supplied if there are no `option_defaults`

      Examples:

          @uses_cmd_options(doit=True, quiet=False)
          def func(x, *, doit, quiet, **kw):
              if not quiet:
                  print("something", x, kw)
              if doit:
                 ... do the thing ...
              ... etc ...

          @uses_cmd_options()
          def func(x, *, options, **kw):
              if not options.quiet:
                  print("something", x, kw)
              if options.doit:
                 ... do the thing ...
              ... etc ...
  '''

  def uses_cmd_wrapper(*func_a, **func_kw):
    # fill in the func_kw from the defaults
    # and keep a record of the chosen values
    # run with the prevailing BaseCommand suitably updated
    try:
      from cs.cmdutils import BaseCommand
      from cs.context import stackattrs
    except ImportError:
      # missing cs.cmdutils or cs.context,
      # make an options with no attributes
      class Options:
        '''Dummy options object for accruing attributes.'''

      options = Options()
    else:
      options_class = BaseCommand.Options
      options = options_class.default() or options_class()
    option_updates = {}
    if not option_defaults:
      option_defaults[_options_param_name] = options
    for option_name, option_default in option_defaults.items():
      if _strict:
        # skip if the option is not provided by the caller
        if option_name in func_kw:
          continue
      elif func_kw.get(option_name) is not None:
        # skip if the option is not provided by the caller
        # or is provided as None
        continue
      option_value = getattr(options, option_name, None)
      if option_value is None:
        option_value = option_default
      option_updates[option_name] = option_value
    func_kw.update(option_updates)
    with stackattrs(options, **option_updates):
      with options:
        return func(*func_a, **func_kw)

  return uses_cmd_wrapper

uses_doit = uses_cmd_options(doit=True)
uses_force = uses_cmd_options(force=False)
uses_quiet = uses_cmd_options(quiet=False)
uses_verbose = uses_cmd_options(verbose=False)

# pylint: disable=too-many-statements
@decorator
def promote(func, params=None, types=None):
  ''' A decorator to promote argument values automatically in annotated functions.

      If the annotation is `Optional[some_type]` or `Union[some_type,None]`
      then the promotion will be to `some_type` but a value of `None`
      will be passed through unchanged.

      The decorator accepts optional parameters:
      * `params`: if supplied, only parameters in this list will
        be promoted
      * `types`: if supplied, only types in this list will be
        considered for promotion

      For any parameter with a type annotation, if that type has a
      `.promote(value)` class method and the function is called with a
      value not of the type of the annotation, the `.promote` method
      will be called to promote the value to the expected type.

      Note that the `Promotable` mixin provides a `.promote()`
      method which promotes `obj` to the class if the class has a
      factory class method `from_`*typename*`(obj)` where *typename*
      is `obj.__class__.__name__`.
      A common case for me is lexical objects which have a `from_str(str)`
      factory to produce an instance from its textual form.

      Additionally, if the `.promote(value)` class method raises a `TypeError`
      and `value` has a `.as_`*typename* attribute
      (where *typename* is the name of the type annotation),
      if that attribute is an instance method of `value`
      then promotion will be attempted by calling `value.as_`*typename*`()`
      otherwise the attribute will be used directly
      on the presumption that it is a property.

      A typical `promote(cls, obj)` method looks like this:

          @classmethod
          def promote(cls, obj):
              if isinstance(obj, cls):
                  return obj
              ... recognise various types ...
              ... and return a suitable instance of cls ...
              raise TypeError(
                  "%s.promote: cannot promote %s:%r",
                  cls.__name__, obj.__class__.__name__, obj)

      Example:

          >>> from cs.timeseries import Epoch
          >>> from typeguard import typechecked
          >>>
          >>> @promote
          ... @typechecked
          ... def f(data, epoch:Epoch=None):
          ...     print("epoch =", type(epoch), epoch)
          ...
          >>> f([1,2,3], epoch=12.0)
          epoch = <class 'cs.timeseries.Epoch'> Epoch(start=0, step=12)

      Example using a class with an `as_P` instance method:

          >>> class P:
          ...   def __init__(self, x):
          ...     self.x = x
          ...   @classmethod
          ...   def promote(cls, obj):
          ...     raise TypeError("dummy promote method")
          ...
          >>> class C:
          ...   def __init__(self, n):
          ...     self.n = n
          ...   def as_P(self):
          ...     return P(self.n + 1)
          ...
          >>> @promote
          ... def p(p: P):
          ...   print("P =", type(p), p.x)
          ...
          >>> c = C(1)
          >>> p(c)
          P = <class 'cs.deco.P'> 2

      *Note*: one issue with this is due to the conflict in name
      between this decorator and the method it looks for in a class.
      The `promote` _method_ must appear after any methods in the
      class which are decorated with `@promote`, otherwise the
      `promote` method supplants the name `promote` making it
      unavailable as the decorator.
      I usually just make `.promote` the last method.

      Failing example:

          class Foo:
              @classmethod
              def promote(cls, obj):
                  ... return promoted obj ...
              @promote
              def method(self, param:Type, ...):
                  ...

      Working example:

          class Foo:
              @promote
              def method(self, param:Type, ...):
                  ...
              # promote method as the final method of the class
              @classmethod
              def promote(cls, obj):
                  ... return promoted obj ...
  '''
  sig = signature(func)
  if params is not None:
    for param_name in params:
      if param_name not in sig.parameters:
        raise ValueError(
            "@promote(%r,params=%r): no %r parameter in signature (sig.parameters=%r)"
            % (func, params, param_name, dict(sig.parameters))
        )
  promotions = {}  # mapping of arg->(type,promote)
  for param_name, param in sig.parameters.items():
    if params is not None and param_name not in params:
      continue
    annotation = param.annotation
    if annotation is Parameter.empty:
      continue
    # recognise optional parameters and use their primary type
    optional = False
    if param.default is not Parameter.empty:
      opt_type = is_optional(annotation)
      if opt_type:
        optional = True
        annotation = opt_type
    if types is not None and annotation not in types:
      continue
    try:
      promote_method = annotation.promote
    except AttributeError:
      continue
    if not callable(promote_method):
      continue
    promotions[param_name] = (param, annotation, promote_method, optional)
  if not promotions:
    warning("@promote(%s): no promotable parameters", func)
    return func

  def promote_args(a, kw):
    ''' Promote the position and keyword arguments.
    '''
    bound_args = sig.bind(*a, **kw)
    arg_mapping = bound_args.arguments
    # we don't import cs.pfx (many dependencies!)
    # pylint: disable=unnecessary-lambda-assignment
    get_context = lambda: (
        "@promote(%s.%s)(%s=%s:%r)" % (
            func.__module__, func.__name__, param_name, arg_value.__class__.
            __name__, arg_value
        )
    )
    for param_name, (param, annotation, promote_method,
                     optional) in promotions.items():
      try:
        arg_value = arg_mapping[param_name]
      except KeyError:
        # parameter not supplied
        if param.default is Parameter.empty:
          continue
        # fill in the default values
        arg_value = param.default
      if isinstance(arg_value, annotation):
        # already of the desired type
        continue
      try:
        try:
          promoted_value = promote_method(arg_value)
        except TypeError as te:
          # see if the value has an as_TypeName() method
          as_method_name = "as_" + annotation.__name__
          try:
            as_annotation = getattr(arg_value, as_method_name)
          except AttributeError:
            # no .as_TypeName, reraise the original TypeError
            raise te  # pylint: disable=raise-missing-from
          else:
            if ismethod(as_annotation) and as_annotation.__self__ is arg_value:
              # bound instance method of arg_value
              try:
                as_value = as_annotation()
              except (TypeError, ValueError) as e:
                raise TypeError(
                    "%s: %s.%s(): %s" %
                    (get_context(), param_name, as_method_name, e)
                ) from e
            else:
              # assuming a property or even a plain attribute
              as_value = as_annotation
            arg_value = as_value
      except TypeError:
        # promotion fails
        if (optional and arg_value is param.default
            and param.default is not Parameter.empty):
          # allow omitted/unconverted optional value with default None
          continue
        raise
      else:
        arg_value = promoted_value
      arg_mapping[param_name] = arg_value
    return bound_args.args, bound_args.kwargs

  if isgeneratorfunction(func):

    def promoting_func(*a, **kw):
      a, kw = promote_args(a, kw)
      yield from func(*a, **kw)

  else:

    def promoting_func(*a, **kw):
      a, kw = promote_args(a, kw)
      return func(*a, **kw)

  return promoting_func

# pylint: disable=too-few-public-methods
class Promotable:
  ''' A mixin class which supports the `@promote` decorator.
  '''

  @classmethod
  def promote(cls, obj, **from_t_kw):
    ''' Promote `obj` to an instance of `cls` or raise `TypeError`.
        This method supports the `@promote` decorator.

        This base method will call the `from_`*typename*`(obj,**from_t_kw)`
        class factory method if present, where *typename* is
        `obj.__class__.__name__`.

        Subclasses may override this method to promote other types,
        typically:

            @classmethod
            def promote(cls, obj):
                if isinstance(obj, cls):
                    return obj
                ... various specific type promotions
                ... not done via a from_typename factory method
                # fall back to Promotable.promote
                return super().promote(obj)

        An typical `from_`*typename*` factory method:

            class Foo(Promotable):

                def __init__(self, dbkey, dbrow):
                    self.key = dbkey
                    self.row_data = row

                @classmethod
                def from_str(cls, s : str):
                    """Accept a database key string, return a `Foo` instance."""
                    row = db_lookup(s)
                    return cls(s, row)

        This supports using `@promote` on functions with `Foo` instances:

            @promote
            def do_it(foo : Foo):
                ... work with foo ...

        but calling it as:

            do_it("foo_key_value")
    '''
    if isinstance(obj, cls):
      return obj
    try:
      from_type = getattr(cls, f'from_{obj.__class__.__name__}')
    except AttributeError:
      pass
    else:
      return from_type(obj, **from_t_kw)
    # try instantiating the class with obj as its sole argument
    return cls(obj)
