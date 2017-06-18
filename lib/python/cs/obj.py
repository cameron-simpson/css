#!/usr/bin/python
#
# Random stuff for "objects". - Cameron Simpson <cs@zip.com.au>
#

from copy import copy as copy0
from cs.py3 import StringTypes

DISTINFO = {
    'description': "Convenience facilities for objects.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.py3'],
}

T_SEQ = 'SEQUENCE'
T_MAP = 'MAPPING'
T_SCALAR = 'SCALAR'

def flavour(obj):
  """ Return constants indicating the ``flavour'' of an object:
      T_MAP: DictType, DictionaryType, objects with an __keys__ or keys attribute.
      T_SEQ: TupleType, ListType, objects with an __iter__ attribute.
      T_SCALAR: Anything else.
  """
  t = type(obj)
  if isinstance(t, (tuple, list)):
    return T_SEQ
  if isinstance(t, dict):
    return T_MAP
  if hasattr(obj, '__keys__') or hasattr(obj, 'keys'):
    return T_MAP
  if hasattr(obj, '__iter__'):
    return T_SEQ
  return T_SCALAR

# Assorted functions for working with O instances.
# These are not methods because I don't want to pollute O subclasses
# with lots of extra method noise.
#
def O_merge(o, _conflict=None, _overwrite=False, **kw):
  ''' Merge key:value pairs from a mapping into an O as attributes.
      Ignore keys that do not start with a letter.
      New attributes or attributes whose values compare equal are
      merged in. Unequal values are passed to:
        _conflict(o, attr, old_value, new_value)
      to resolve the conflict. If _conflict is omitted or None
      then the new value overwrites the old if _overwrite is true.
  '''
  for attr, value in kw.items():
    if not len(attr) or not attr[0].isalpha():
      if not attr.startswith('_O_'):
        ##warning(".%s: ignoring, does not start with a letter", attr)
        pass
      continue
    try:
      ovalue = getattr(o, attr)
    except AttributeError:
      # new attribute -
      setattr(o, attr, value)
    else:
      if ovalue != value:
        if _conflict is None:
          if _overwrite:
            setattr(o, attr, value)
        else:
          _conflict(o, attr, ovalue, value)

def O_attrs(o):
  ''' Yield attribute names from o which are pertinent to O_str.
      Note: this calls getattr(o, attr) to inspect it in order to
      prune callables.
  '''
  omit = getattr(o, '_O_omit', ())
  for attr in sorted(dir(o)):
    if attr[0].isalpha() and attr not in omit:
      try:
        value = getattr(o, attr)
      except AttributeError:
        continue
      if not callable(value):
        yield attr

def O_attritems(o):
  for attr in O_attrs(o):
    try:
      value = getattr(o, attr)
    except AttributeError:
      continue
    else:
      yield attr, value

def O_str(o, no_recurse=False, seen=None):
  if seen is None:
    seen = set()
  t = type(o)
  if t in StringTypes:
    return repr(o)
  if t in (tuple, int, float, bool, list):
    return str(o)
  if t is dict:
    o2 = dict([(k, str(v)) for k, v in o.items()])
    return str(o2)
  if t is set:
    return 'set(%s)' % (','.join(sorted([str(item) for item in o])))
  seen.add(id(o))
  if no_recurse:
    attrdesc_strs = [
        "%s=<%s>" % (pattr, type(pvalue).__name__)
        for pattr, pvalue in O_attritems(o)
    ]
  else:
    attrdesc_strs = []
    for pattr, pvalue in O_attritems(o):
      if id(pvalue) in seen:
        desc = "<%s>" % (type(pvalue).__name__,)
      else:
        desc = "%s=%s" % (pattr,
                          O_str(pvalue,
                                no_recurse=no_recurse,
                                seen=seen))
      attrdesc_strs.append(desc)
  s = "<%s %s>" % (o.__class__.__name__, ",".join(attrdesc_strs))
  seen.remove(id(o))
  return s

class O(object):

  ''' A bare object subclass to allow storing arbitrary attributes.
      It also has a nicer default str() action.
  '''

  _O_recurse = True

  def __init__(self, **kw):
    ''' Initialise this O.
        Fill in attributes from any keyword arguments if supplied.
        This call can be omitted in subclasses if desired.
    '''
    self._O_omit = []
    for k in kw:
      setattr(self, k, kw[k])

  def __str__(self):
    recurse = self._O_recurse
    self._O_recurse = False
    s = O_str(self, no_recurse=not recurse)
    self._O_recurse = recurse
    return s

  def __eq__(self, other):
    attrs = tuple(O_attrs(self))
    oattrs = tuple(O_attrs(other))
    if attrs != oattrs:
      return False
    for attr in O_attrs(self):
      if getattr(self, attr) != getattr(other, attr):
        return False
    return True

  __hash__ = object.__hash__

  def __ne__(self, other):
    return not (self == other)

  def D(self, msg, *a):
    ''' Call cs.logutils.D() if this object is being traced.
    '''
    if getattr(self, '_O_trace', False):
      from cs.logutils import D as dlog
      if a:
        dlog("%s: " + msg, self, *a)
      else:
        dlog(': '.join((str(self), msg)))

def copy(obj, *a, **kw):
  ''' Convenient function to shallow copy an object with simple modifications.
       Performs a shallow copy of `self` using copy.copy.
       Treat all positional parameters as attribute names, and
       replace those attributes with shallow copies of the original
       attribute.
       Treat all keyword arguments as (attribute,value) tuples and
       replace those attributes with the supplied values.
  '''
  obj2 = copy0(obj)
  for attr in a:
    setattr(obj2, attr, copy(getattr(obj, attr)))
  for attr, value in kw.items():
    setattr(obj2, attr, value)
  return obj2

def obj_as_dict(o, attr_prefix=None, attr_match=None):
  ''' Return a dictionary with keys mapping to `o` attributes.
  '''
  if attr_match is None:
    if attr_prefix is None:
      match = lambda attr: len(attr) > 0 and not attr.startswith('_')
    else:
      match = lambda attr: attr.startswith(attr_prefix)
  elif attr_prefix is None:
    match = attr_match
  else:
    raise ValueError("cannot specify both attr_prefix and attr_match")
  d = {}
  for attr in dir(o):
    if match(attr):
      d[attr] = getattr(o, attr)
  return d

class Proxy(object):

  ''' An extremely simple proxy object that passes all unmatched attribute accesses to the proxied object.
      Note that setattr and delattr work directly on the proxy, not the proxied object.
  '''

  def __init__(self, other):
    self._proxied = other

  def __getattr__(self, attr):
    _proxied = object.__getattribute__(self, '_proxied')
    return getattr(_proxied, attr)

  def __iter__(self):
    _proxied = object.__getattribute__(self, '_proxied')
    return iter(_proxied)

  def __len__(self):
    _proxied = object.__getattribute__(self, '_proxied')
    return len(_proxied)
