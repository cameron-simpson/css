#!/usr/bin/python
#
# Decorators.
#   - Cameron Simpson <cs@cskk.id.au> 02jul2017
#

r'''
Assorted decorator functions.
'''

import time
from cs.pfx import Pfx

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.pfx',
    ],
}

def decorator(deco, *da, **dkw):
  ''' Wrapper for decorator functions to support optional keyword arguments.

      Examples:

          @decorator
          def dec(func, **dkw):
            ...
          @dec
          def func1(...):
            ...
          @dec(foo='bah')
          def func2(...):
            ...
  '''
  def overdeco(*da, **dkw):
    if not da:
      def wrapper(*a, **dkw2):
        dkw.update(dkw2)
        func, = a
        dfunc = deco(func, **dkw)
        dfunc.__doc__ = getattr(func, '__doc__', '')
        return dfunc
      return wrapper
    if len(da) > 1:
      raise ValueError("extra positional arguments after function: %r" % (da[1:],))
    func = da[0]
    dfunc = deco(func, **dkw)
    dfunc.__doc__ = getattr(func, '__doc__', '')
    return dfunc
  return overdeco

@decorator
def cached(func, attr_name=None, poll_delay=None, sig_func=None, unset_value=None):
  ''' Decorator to cache the result of a method and keep a revision
      counter for changes.
      The revision supports the @revised decorator.

      This decorator may be used in 2 modes.
      Directly:

          @cached
          def method(self, ...)

      or indirectly:

          @cached(poll_delay=0.25)
          def method(self, ...)

      Optional keyword arguments:
      * `attr_name`: the basis name for the supporting attributes.
        Default: the name of the method.
      * `poll_delay`: minimum time between polls; after the first
        access, subsequent accesses before the `poll_delay` has elapsed
        will return the cached value.
        Default: None, meaning no poll delay.
      * `sig_func`: a signature function, which should be significantly
        cheaper than the method. If the signature is unchanged, the
        cached value will be returned. The signature function
        expected the instance (self) as its first parameter.
        Default: None, meaning no signature function. The first
        computed value will be kept and never updated.
      * `unset_value`: the value to return before the method has been
        called successfully.
        Default: None.

      If the method raises an exception, this will be logged and
      the method will return the previously cached value.

      An example use of this decorator might be to keep a "live"
      configuration data structure, parsed from a configuration
      file which might be modified after the program starts. One
      might provide a signature function which called os.stat() on
      the file to check for changes before invoking a full read and
      parse of the file.
  '''
  if poll_delay is not None and poll_delay <= 0:
    raise ValueError("poll_delay <= 0: %r" % (poll_delay,))
  if poll_delay is not None and poll_delay <= 0:
    raise ValueError("invalid poll_delay, should be >0, got: %r" % (poll_delay,))

  attr = attr_name if attr_name else func.__name__
  val_attr = '_' + attr
  sig_attr = val_attr + '__signature'
  rev_attr = val_attr + '__revision'
  lastpoll_attr = val_attr + '__lastpoll'
  firstpoll_attr = val_attr + '__firstpoll'

  def wrapper(self, *a, **kw):
    first = getattr(self, firstpoll_attr, True)
    setattr(self, firstpoll_attr, False)
    value0 = getattr(self, val_attr, unset_value)
    # see if we should use the cached value
    if poll_delay is not None and not first:
      # too early to check the signature function?
      now = time.time()
      lastpoll = getattr(self, lastpoll_attr, None)
      if (
          value0 is not unset_value
          and lastpoll is not None
          and now - lastpoll < poll_delay
      ):
        return value0
      setattr(self, lastpoll_attr, now)
    if sig_func is not None:
      # see if the signature is unchanged
      sig0 = getattr(self, sig_attr, None)
      try:
        sig = sig_func(self)
      except Exception as e:
        from cs.logutils import exception
        exception("%s.%s: sig func %s(self): %s", self, attr, sig_func, e)
        return value0
      if sig0 is not None and sig0 == sig:
        return value0
    # compute the current value
    try:
      value = func(self, *a, **kw)
    except Exception as e:
      from cs.logutils import exception
      exception("%s.%s: value func %s(*%r,**%r): %s", self, attr, func, a, kw, e)
      return value0
    setattr(self, val_attr, value)
    if sig_func is not None:
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

@decorator
def strable(func, open_func=None):
  ''' Decorator for functions which may accept a str instead of their core type.

      Parameters:
      * `func`: the function to decorate
      * `open_func`: the "open" factory to produce the core type form
        the string if a string is provided; the default is the builtin
        "open" function

      The usual (and default) example is a function to process an
      open file, designed to be handed a file object but which may
      be called with a filename. If the first argument is a str
      then that file is opened and the function called with the
      open file.

      Examples:

          @strable
          def count_lines(f):
            return len(line for line in f)

          class Recording:
            "Class representing a video recording."
            ...
          @strable
          def process_video(r, open_func=Recording):
            ... do stuff with `r` as a Recording instance ...
  '''
  if open_func is None:
    open_func = open
  def accepts_str(arg, *a, **kw):
    if isinstance(arg, str):
      with Pfx(arg):
        with open_func(arg) as opened:
          return func(opened, *a, **kw)
    return func(arg, *a, **kw)
  return accepts_str

if __name__ == '__main__':
  class Foo:
    @cached(poll_delay=2)
    def x(self, y):
      return str(y)
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
