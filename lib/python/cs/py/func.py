#!/usr/bin/python
#
# Convenience routines for python functions.
#       - Cameron Simpson <cs@cskk.id.au> 15apr2014
#

r'''
Convenience facilities related to Python functions.
'''

from functools import partial
from pprint import pformat
from cs.deco import decorator
from cs.py3 import unicode, raise_from
from cs.x import X

__version__ = '20220311'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.py3',
        'cs.x',
    ],
}

def funcname(func):
  ''' Return a name for the supplied function `func`.
      Several objects do not have a __name__ attribute, such as partials.
  '''
  try:
    return func.__qualname__
  except AttributeError:
    try:
      return func.__name__
    except AttributeError:
      if isinstance(func, partial):
        return "partial(%s)" % (funcname(func.func),)
      return str(func)

def funccite(func):
  ''' Return a citation for a function (name and code location).
  '''
  try:
    code = func.__code__
  except AttributeError:
    return "%s[no.__code__]" % (repr(func),)
  return "%s[%s:%d]" % (funcname(func), code.co_filename, code.co_firstlineno)

def func_a_kw_fmt(func, *a, **kw):
  ''' Prepare a percent-format string and associated argument list
      describing a call to `func(*a,**kw)`.
      Return `format,args`.

      The `func` argument can also be a string,
      presumably a prepared description of `func` such as `funccite(func)`.
  '''
  av = [
      func if isinstance(func, str) else getattr(func, '__name__', str(func))
  ]
  afv = ['%r'] * len(a)
  av.extend(a)
  afv.extend(['%s=%r'] * len(kw))
  for kv in kw.items():
    av.extend(kv)
  return '%s(' + ','.join(afv) + ')', av

# pylint: disable=too-many-arguments
@decorator
def trace(
    func, call=True, retval=False, exception=False, pfx=False, pprint=False
):
  ''' Decorator to report the call and return of a function.
  '''

  citation = funccite(func)

  def traced_function_wrapper(*a, **kw):
    ''' Wrapper for `func` to trace call and return.
    '''
    # late import so that we can use this in modules we import
    # pylint: disable=import-outside-toplevel
    if pfx:
      try:
        from cs.pfx import XP as xlog
      except ImportError:
        xlog = X
    else:
      xlog = X
    if call:
      fmt, av = func_a_kw_fmt(citation, *a, **kw)
      xlog("CALL " + fmt, *av)
    try:
      result = func(*a, **kw)
    except Exception as e:
      if exception:
        xlog("CALL %s RAISE %r", citation, e)
      raise
    else:
      if retval:
        xlog(
            "CALL %s RETURN %s", citation,
            (pformat if pprint else repr)(result)
        )
      return result

  traced_function_wrapper.__name__ = "@trace(%s)" % (citation,)
  traced_function_wrapper.__doc__ = "@trace(%s)\n\n" + (func.__doc__ or '')
  return traced_function_wrapper

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

def prop(func):
  ''' A substitute for the builtin @property.

      The builtin @property decorator lets internal AttributeErrors escape.
      While that can support properties that appear to exist conditionally,
      in practice this is almost never what I want, and it masks deeper errors.
      Hence this wrapper for @property that transmutes internal AttributeErrors
      into RuntimeErrors.
  '''

  # pylint: disable=inconsistent-return-statements
  def prop_wrapper(*a, **kw):
    try:
      return func(*a, **kw)
    except AttributeError as e:
      raise_from(RuntimeError("inner function %s raised %s" % (func, e)), e)

  prop_wrapper.__name__ = "@prop(%s)" % (funcname(func),)
  return property(prop_wrapper)

def derived_property(
    func,
    original_revision_name='_revision',
    lock_name='_lock',
    property_name=None,
    unset_object=None
):
  ''' A property which must be recomputed
      if the reference revision (attached to self)
      exceeds the snapshot revision.
  '''
  if property_name is None:
    property_name = '_' + func.__name__
  # the property used to track the reference revision
  property_revision_name = property_name + '__revision'

  def property_value(self):
    ''' Attempt lockless fetch of property first.
        Use lock if property is unset and up to date.
    '''
    # poll outside lock
    try:
      p = getattr(self, property_name, unset_object)
      p_revision = getattr(self, property_revision_name, 0)
      o_revision = getattr(self, original_revision_name)
      if p is unset_object or p_revision < o_revision:
        with getattr(self, lock_name):
          # repoll value inside lock
          p = getattr(self, property_name, unset_object)
          p_revision = getattr(self, property_revision_name, 0)
          o_revision = getattr(self, original_revision_name)
          if p is unset_object or p_revision < o_revision:
            p = func(self)
            setattr(self, property_name, p)
            setattr(self, property_revision_name, o_revision)
    except AttributeError as e:
      raise_from(RuntimeError("AttributeError: %s" % (e,)), e)
    return p

  return property(property_value)

def derived_from(property_name):
  ''' A property which must be recomputed
      if the revision of another property exceeds the snapshot revision.
  '''
  return partial(
      derived_property,
      original_revision_name='_' + property_name + '__revision'
  )

def yields_type(func, basetype):
  ''' Decorator which checks that a generator yields values of type `basetype`.
  '''
  citation = funccite(func)

  def check_yields_type(*a, **kw):
    for item in func(*a, **kw):
      if not isinstance(item, basetype):
        raise TypeError(
            "wrong type yielded from func %s: expected subclass of %s, got %s: %r"
            % (citation, basetype, type(item), item)
        )
      yield item

  check_yields_type.__name__ = (
      'check_yields_type[%s,basetype=%s]' % (citation, basetype)
  )
  return check_yields_type

def returns_type(func, basetype):
  ''' Decrator which checks that a function returns values of type `basetype`.
  '''
  citation = funccite(func)

  def check_returns_type(*a, **kw):
    retval = func(*a, **kw)
    if not isinstance(retval, basetype):
      raise TypeError(
          "wrong type returned from func %s: expected subclass of %s, got %s: %r"
          % (citation, basetype, type(retval), retval)
      )
    return retval

  check_returns_type.__name__ = (
      'check_returns_type[%s,basetype=%s]' % (citation, basetype)
  )
  return check_returns_type

def yields_str(func):
  ''' Decorator for generators which should yield strings.
  '''
  return yields_type(func, (str, unicode))

def returns_bool(func):
  ''' Decorator for functions which should return Booleans.
  '''
  return returns_type(func, bool)

def returns_str(func):
  ''' Decorator for functions which should return strings.
  '''
  return returns_type(func, (str, unicode))
