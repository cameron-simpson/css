#!/usr/bin/python
#
# Convenience routines for python functions.
#       - Cameron Simpson <cs@zip.com.au> 15apr2014
#

def funcname(func):
  ''' Return a name for the supplied function `func`.
      Several objects do not have a __name__ attribute, such as partials.
  '''
  try:
    return func.__name__
  except AttributeError:
    return str(func)

def callmethod_if(o, method, default=None, a=None, kw=None):
  ''' Call the named `method` on the object `o` if it exists.
      If it does not exist, return `default` (which defaults to None).
      Otherwise call getattr(o, method)(*a, **kw).
      `a` defaults to ().
      `kw` defaults to {}.
  '''
  try:
    m = getattr(o, method)
  except AttributeError:
    return default
  if a is None:
    a = ()
  if kw is None:
    kw = {}
  return m(*a, **kw)
