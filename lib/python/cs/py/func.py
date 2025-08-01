#!/usr/bin/python
#
# Convenience routines for python functions.
#       - Cameron Simpson <cs@cskk.id.au> 15apr2014
#

r'''
Convenience facilities related to Python functions.
'''

from functools import partial

__version__ = '20250724-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'python_requires':
    '>=3',
}

def funcname(func):
  ''' Return a name for the supplied function `func`.
      Several objects do not have a __name__ attribute, such as partials.
  '''
  try:
    name = func.__qualname__
  except AttributeError:
    try:
      name = func.__name__
    except AttributeError:
      if isinstance(func, partial):
        name = "partial(%s)" % (funcname(func.func),)
      else:
        name = str(func)
  return "%s:%s" % (getattr(func, '__module__', '?'), name)

def funccite(func):
  ''' Return a citation for a function (name and code location).
  '''
  try:
    code = func.__code__
  except AttributeError:
    return "%s[no.__code__]" % (repr(func),)
  try:
    from cs.fs import shortpath  # pylint: disable=import-outside-toplevel
  except ImportError:
    shortpath = lambda p: p  # pylint: disable=unnecessary-lambda-assignment
  return "%s[%s:%d]" % (
      funcname(func),
      shortpath(code.co_filename),
      code.co_firstlineno,
  )

def func_a_kw_fmt(func, *a, **kw):
  ''' Prepare a percent-format string and associated argument list
      describing a call to `func(*a,**kw)`.
      Return `format,args`.

      The `func` argument can also be a string,
      typically a prepared description of `func` such as `funccite(func)`.

      *Note*: the returned `args` is a `list` for easy incorporation
      into further arguments.  The `%` operator requires a `tuple`.
  '''
  av = [
      func if isinstance(func, str) else getattr(func, '__name__', str(func))
  ]
  afv = ['%.40r'] * len(a)
  av.extend(a)
  afv.extend(['%s=%.40r'] * len(kw))
  for kv in kw.items():
    av.extend(kv)
  return '%s(' + ','.join(afv) + ')', av

def func_a_kw(func, *a, **kw):
  ''' Return a string representing a call to `func(*a,**kw)`.
  '''
  fmt, args = func_a_kw_fmt(func, *a, **kw)
  return fmt % tuple(args)

def callif(doit, func, *a, **kw):
  ''' Call `func(*a,**kw)` if `doit` is true
      otherwise just print it out.

      The parameter `func` may be preceeded optionally by a `dict`
      containing modes. The current modes are:
      * `'print'`: the print function, default the builtin `print`
  '''
  if isinstance(func, dict):
    modes = func
    a = list(a)
    func = a.pop(0)
  else:
    modes = {}
  if doit:
    return func(*a, **kw)
  # just recite the function
  modes.get('print', print)(func_a_kw(func, *a, **kw))
  return None

def callmethod_if(obj, method, default=None, a=None, kw=None):
  ''' Call the named `method` on the object `obj` if it exists.

      If it does not exist, return `default` (which defaults to None).
      Otherwise call getattr(obj, method)(*a, **kw).
      `a` defaults to ().
      `kw` defaults to {}.
  '''
  try:
    m = getattr(obj, method)
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
      raise RuntimeError("inner function %s raised %s" % (func, e)) from e

  prop_wrapper.__name__ = "@prop(%s)" % (funcname(func),)
  return property(prop_wrapper)

def derived_property(
    func,
    original_revision_name='_revision',
    lock_name='_lock',
    property_name=None,
    unset_object=None,
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
      raise RuntimeError("AttributeError: %s" % (e,)) from e
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
