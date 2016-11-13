#!/usr/bin/python
#
# Convenience routines for python functions.
#       - Cameron Simpson <cs@zip.com.au> 15apr2014
#

DISTINFO = {
    'description': "convenience facilities related to Python functions",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'install_requires': ['cs.excutils'],
}

from cs.excutils import transmute

def funcname(func):
  ''' Return a name for the supplied function `func`.
      Several objects do not have a __name__ attribute, such as partials.
  '''
  try:
    return func.__name__
  except AttributeError:
    return str(func)

def funccite(func):
  code = func.__code__
  return "%s[%s:%d]" % (funcname(func), code.co_filename, code.co_firstlineno)

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
  ''' The builtin @property decorator lets internal AttributeErrors escape.
      While that can support properties that appear to exist conditionally,
      in practice this is almost never what I want, and it masks deeper errors.
      Hence this wrapper for @property that transmutes internal AttributeErrors
      into RuntimeErrors.
  '''
  def wrapper(*a, **kw):
    try:
      return func(*a, **kw)
    except AttributeError as e:
      raise RuntimeError("inner function %s raised %s" % (func, e))
  return property(wrapper)

def derived_property(func, original_revision_name='_revision', lock_name='_lock', property_name=None, unset_object=None):
  ''' A property which must be recomputed if the reference revision exceeds the snapshot revision.
  '''
  if property_name is None:
    property_name = '_' + func.__name__
  # the property used to track the reference revision
  property_revision_name = property_name + '__revision'

  from cs.logutils import X

  @transmute(AttributeError)
  def property_value(self):
    ''' Attempt lockless fetch of property first.
        Use lock if property is unset and up to date.
    '''
    # poll outside lock
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
          X("COMPUTE .%s... [p_revision=%s, o_revision=%s]", property_name, p_revision, o_revision)
          p = func(self)
          setattr(self, property_name, p)
          ##X("COMPUTE .%s: set .%s to %s", self, property_revision_name, o_revision)
          setattr(self, property_revision_name, o_revision)
        else:
          ##debug("inside lock, already computed up to date %s", property_name)
          pass
    else:
      ##debug("outside lock, already computed up to date %s", property_name)
      pass
    return p
  return property(property_value)

def derived_from(property_name):
  ''' A property which must be recomputed if the revision of another property exceeds the snapshot revision.
  '''
  return partial(derived_property, original_revision_name='_' + property_name + '__revision')

def yields_type(func, basetype):
  ''' Decrator which checks that a generator yields values of type `basetype`.
  '''
  funcname = funccite(func)
  def check_yields_type(*a, **kw):
    for item in func(*a, **kw):
      if not isinstance(item, basetype):
        raise TypeError(
                "wrong type yielded from func %s: expected subclass of %s, got %s: %r"
                % (funcname, basetype, type(item), item)
              )
      yield item
  check_yields_type.__name__ = ( 'check_yields_type[%s,basetype=%s]'
                                 % (funcname, basetype)
                               )
  return check_yields_type

def returns_type(func, basetype):
  ''' Decrator which checks that a function returns values of type `basetype`.
  '''
  funcname = funccite(func)
  def check_returns_type(*a, **kw):
    retval = func(*a, **kw)
    if not isinstance(retval, basetype):
      raise TypeError(
              "wrong type returned from func %s: expected subclass of %s, got %s: %r"
              % (funcname, basetype, type(retval), retval)
            )
    return retval
  check_returns_type.__name__ = ( 'check_returns_type[%s,basetype=%s]'
                                  % (funcname, basetype)
                                )
  return check_returns_type
