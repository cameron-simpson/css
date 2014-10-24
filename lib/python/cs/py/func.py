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

def derived_property(func, original_revision_name='_revision', lock_name='_lock', property_name=None, unset_object=None):
  ''' A property which must be recomputed if the reference revision exceeds the snapshot revision.
  '''
  if property_name is None:
    property_name = '_' + func.__name__
  # the property used to track the reference revision
  property_revision_name = property_name + '__revision'

  from cs.excutils import transmute
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
  return partial(derived_property, original_revision_name=property_name + '__revision')
