#!/usr/bin/python
#
# Decorators.
#   - Cameron Simpson <cs@zip.com.au> 02jul2017
# 

import time
from cs.x import X

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
  X("@decorator: deco=%s, da=%r, dkw=%r)", deco, da, dkw)
  def overdeco(*da, **dkw):
    X("@decorator:overdeco: da=%r, dkw=%r)", da, dkw)
    if not da:
      def wrapper(*a, **dkw):
        X("@decorator:wrapper:a=%r,kw=%r", a, dkw)
        func, = a
        return deco(func, **dkw)
      return wrapper
    if len(da) > 1:
      raise ValueError("extra positional arguments after function: %r" % (da[1:],))
    func = da[0]
    return deco(func, **dkw)
  return overdeco

@decorator
def cached(func, **dkw):
  ''' Decorator to cache the result of a method and keep a revision counter for changes.
      The revision supports the @revised decorator.

      This decorator may be used in 2 modes.
      Directly:
        @cached
        def method(self, ...)
      or indirectly:
        @cached(poll_delay=0.25)
        def method(self, ...)

      Optional keyword arguments:
      `attr_name`: the basis name for the supporting attributes.
        Default: the name of the method.
      `poll_delay`: minimum time between polls; after the first
        access, subsequent accesses before the `poll_delay` has elapsed
        will return the cached value.
        Default: None, meaning no poll delay.
      `sig_func`: a signature function, which should be significantly
        cheaper than the method. If the signature is unchanged, the
        cached value will be returned. The signature function
        expected the instance (self) as its first parameter.
        Default: None, meaning no signature function.
      `unset_value`: the value to return before the method has been
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
  attr_name = dkw.pop('attr_name', None)
  poll_delay = dkw.pop('poll_delay', None)
  sig_func = dkw.pop('sig_func', None)
  unset_value = dkw.pop('unset_value', None)
  if dkw:
    raise ValueError("unexpected keyword arguments: %r" % (dkw,))
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
    try:
      if poll_delay is not None and not first:
        # too early to check the signature function?
        now = time.time()
        lastpoll = getattr(self, lastpoll_attr, None)
        if ( value0 is not unset_value
         and lastpoll is not None
         and now - lastpoll < poll_delay
        ):
          return value0
        setattr(self, lastpoll_attr, now)
      if sig_func is not None:
        # see if the signature is unchanged
        sig0 = getattr(self, sig_attr, None)
        sig = sig_func(self)
        if sig0 is not None and sig0 == sig:
          return value0
      # compute the current value
      value = func(self, *a, **kw)
      setattr(self, val_attr, value)
      if sig_func is not None:
        setattr(self, sig_attr, sig)
      # bump revision if the value changes
      # noncomparable values are always presumed changed
      try:
        changed = value != value0
      except TypeError:
        changed = True
      if changed:
        setattr(self, rev_attr, getattr(self, rev_attr, 0) + 1)
      return value
    except Exception as e:
      from cs.logutils import exception, setup_logging
      setup_logging("foo")
      exception("%s.%s: %s", self, attr, e)
      return value0

  return wrapper

if __name__ == '__main__':
  class Foo:
    @cached(poll_delay=2)
    def x(self, y):
      return str(y)
  F = Foo();
  y = F.x(1)
  print("F.x() ==>", y)
  y = F.x(1)
  print("F.x() ==>", y)
  y = F.x(2)
  print("F.x() ==>", y)
  time.sleep(3)
  y = F.x(3)
  print("F.x() ==>", y)
