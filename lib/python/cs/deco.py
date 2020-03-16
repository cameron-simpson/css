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
import sys
import time
try:
  from cs.logutils import warning
except ImportError:
  from logging import warning

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
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
    # TODO: general handling of first-argument-callable
    # to support func2 = deco(func1, args..., kwargs...).
    # Currently this must be done as:
    # func2=deco(args..., kwargs...)(func1)
    #
    # if there's exactly one callable position argument
    # then it is the target function: call deco(func).
    if len(da) == 1 and callable(da[0]) and not dkw:
      func = da[0]
      decorated = deco(func)
      decorated.__doc__ = getattr(func, '__doc__', '')
      func_module = getattr(func, '__module__', None)
      try:
        decorated.__module__ = func_module
      except AttributeError:
        pass
      return decorated
    # otherwise we collect the arguments supplied
    # and return a function which takes a callable
    # and returns deco(func, *da, **kw).
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
def cachedmethod(
    method, attr_name=None, poll_delay=None, sig_func=None, unset_value=None
):
  ''' Decorator to cache the result of a method and keep a revision
      counter for changes.

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
  firstpoll_attr = val_attr + '__firstpoll'

  def wrapper(self, *a, **kw):
    with Pfx("%s.%s", self, attr):
      first = getattr(self, firstpoll_attr, True)
      sig = getattr(self, sig_attr, None)
      setattr(self, firstpoll_attr, False)
      value0 = getattr(self, val_attr, unset_value)
      if not first and value0 is not unset_value:
        # see if we should use the cached value
        if poll_delay is None and sig_func is None:
          return value0
        if poll_delay is not None:
          # too early to check the signature function?
          now = time.time()
          lastpoll = getattr(self, lastpoll_attr, None)
          if lastpoll is not None and now - lastpoll < poll_delay:
            # still valid, return the value
            return value0
          setattr(self, lastpoll_attr, now)
        # no poll_delay or poll expired
        if sig_func is None:
          # no sig func
          return value0
        # see if the signature is unchanged
        sig0 = getattr(self, sig_attr, None)
        try:
          sig = sig_func(self)
        except Exception as e:
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
      except Exception as e:
        if value0 is unset_value:
          raise
        warning("exception calling %s(self): %s", method, e, exc_info=True)
        return value0
      setattr(self, val_attr, value)
      if sig_func is not None and not first:
        setattr(self, sig_attr, sig)
      # bump revision if the value changes
      # noncomparable values are always presumed changed
      try:
        changed = value0 is unset_value or value != value0
      except TypeError:
        changed = True
      if changed:
        setattr(self, rev_attr, getattr(self, rev_attr, 0) + 1)
      return value

  return wrapper

def cached(*a, **kw):
  ''' Compatibility wrapper for `@cachedmethod`, issuing a warning.
  '''
  warning("obsolete use of @cached, please update to @cachedmethod")
  return cachedmethod(*a, **kw)

def contextual(func):
  ''' Wrap a simple function as a context manager.

      This was written to support `@strable`,
      which requires its `open_func` to be a context manager.

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
        except Exception as e:
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

if __name__ == '__main__':

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
