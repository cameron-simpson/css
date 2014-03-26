#!/usr/bin/python
#
# Random stuff for "objects". - Cameron Simpson <cs@zip.com.au>
#

from cs.py3 import StringTypes

class slist(list):
  ''' A list with a shorter str().
  '''
  def __str__(self):
    return str(len(self)) + ":[" + ",".join(str(e) for e in self) + "]"

T_SEQ = 'ARRAY'
T_MAP = 'HASH'
T_SCALAR = 'SCALAR'
def objFlavour(obj):
  """ Return the ``flavour'' of an object:
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

class WithUCAttrs:
  ''' An object where access to obj.FOO accesses obj['FOO']
      if FOO is all upper case.
  '''
  def __getattr__(self, attr):
    if attr.isalpha() and attr.isupper():
      return self[attr]
    return dict.__getattr__(self, attr)
  def __setattr__(self, attr, value):
    if attr.isalpha() and attr.isupper():
      self[attr]=value
      return
    self.__dict__[attr]=value

class DictUCAttrs(dict, WithUCAttrs):
  ''' A dict where access to obj.FOO accesses obj['FOO']
      if FOO is all upper case.
  '''
  def __init__(self, fill=None):
    if fill is None:
      fill=()
    dict.__init__(self, fill)

class WithUC_Attrs:
  ''' An object where access to obj.FOO accesses obj['FOO']
      if FOO matches ^[A-Z][_A-Z0-9]*.
  '''
  def __uc_(self, s):
    if s.isalpha() and s.isupper():
      return True
    if len(s) < 1:
      return False
    if not s[0].isupper():
      return False
    for c in s[1:]:
      if c != '_' and not (c.isupper() or c.isdigit()):
        return False
    return True
  def __getattr__(self, attr):
    if self.__uc_(attr):
      return self[attr]
    return dict.__getattr__(self, attr)
  def __setattr__(self, attr, value):
    if self.__uc_(attr):
      self[attr]=value
      return
    self.__dict__[attr]=value

class DictUC_Attrs(dict, WithUC_Attrs):
  ''' A dict where access to obj.FOO accesses obj['FOO']
      if FOO matches ^[A-Z][_A-Z0-9]*.
  '''
  def __init__(self, fill=None):
    if fill is None:
      fill=()
    dict.__init__(self, fill)

class DictAttrs(dict):
  def __init__(self, d=None):
    dict.__init__()
    if d is not None:
      for k in d.keys():
        self[k]=d[k]

  def __getattr__(self, attr):
    return self[attr]
  def __setattr__(self, attr, value):
    self[attr]=value

# Assorted functions for working with O instances.
# These are not methods because I don't want to pollute O subclasses
# with lots of extra method noise.
#
def O_merge(o, _conflict=None, **kw):
  ''' Merge key:value pairs from a mapping into an O as attributes.
      Ignore keys that do not start with a letter.
      New attributes or attributes whose values compare equal are
      merged in. Unequal values are passed to:
        _conflict(o, attr, old_value, new_value)
      to resolve the conflict. If _conflict is omitted or None
      a warning if printed and the new value not merged.
  '''
  for attr, value in kw.iteritems():
    if not len(attr) or not attr[0].isalpha():
      if not attr.startswith('_O_'):
        warning(".%s: ignoring, does not start with a letter", attr)
      continue
    try:
      ovalue = getattr(o, attr)
    except AttributeError:
      # new attribute - 
      setattr(o, attr, value)
    else:
      if ovalue != value:
        if _conflict is None:
          from cs.logutils import warning
          warning(".%s: conflicting values: old=%s, new=%s", attr, ovalue, value)
        else:
          _conflict(o, attr, ovalue, value)

def O_attrs(o):
  ''' Yield attribute names from o which are pertinent to O_str.
      Note: this calls getattr(o, attr) to inspect it in order to
      prune callables.
  '''
  try:
    omit = o._O_omit
  except AttributeError:
    omit = ()
  for attr in sorted(dir(o)):
    if attr[0].isalpha() and not attr in omit:
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
  if t in (tuple,int,float,bool,list):
    return str(o)
  if t is dict:
    o2 = dict( [ (k, str(v)) for k, v in o.iteritems() ] )
    return str(o2)
  if t is set:
    return 'set(%s)' % (','.join(sorted([ str(item) for item in o])))
  seen.add(id(o))
  s = ( "<%s %s>"
         % ( o.__class__.__name__,
             (
               ",".join([ ( "%s=<%s>" % (pattr, type(pvalue).__name__)
                            if no_recurse else
                            "%s=%s" % (pattr,
                                       O_str(pvalue,
                                             no_recurse=no_recurse,
                                             seen=seen)
                                         if id(pvalue) not in seen
                                         else "<%s>" % (type(pvalue).__name__,)
                                      )
                          )
                          for pattr, pvalue in O_attritems(o)
                        ])
             )
           )
     )
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
    s = O_str(self, no_recurse = not recurse)
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

class Proxy(object):
  ''' An extremely simple proxy object that passes all unmatched attribute accesses to the proxied object.
      Note that setattr and delattr work directly on the proxy, not the proxied object.
  '''

  def __init__(self, other):
    self._proxied = other

  def __getattr__(self, attr):
    return getattr(self._proxied, attr)

  def __iter__(self):
    return iter(self._proxied)

  def __len__(self):
    return len(self._proxied)
