#!/usr/bin/python
#
# Random stuff for "objects". - Cameron Simpson <cs@cskk.id.au>
#

r'''
Convenience facilities for objects.

Presents:
* flavour, for deciding whether an object resembles a mapping or sequence.
* Some O_* functions for working with objects
* Proxy, a very simple minded object proxy intended to aid debugging.
'''

from __future__ import print_function
from copy import copy as copy0
import sys
import traceback
from types import SimpleNamespace
from threading import Lock
from weakref import WeakValueDictionary
from cs.py3 import StringTypes

__version__ = '20200517-post'

DISTINFO = {
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
      * `T_MAP`: DictType, DictionaryType, objects with an __keys__ or keys attribute.
      * `T_SEQ`: TupleType, ListType, objects with an __iter__ attribute.
      * `T_SCALAR`: Anything else.
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

class O(SimpleNamespace):
  ''' The `O` class is now obsolete, please subclass `types.SimpleNamespace`.
  '''

  callers = set()

  def __init__(self, **kw):
    frame = traceback.extract_stack(None, 2)[0]
    caller=(frame[0], frame[1])
    if caller not in self.callers:
      self.callers.add(caller)
      print(
          "WARNING: %s:%d %s: obsolete use of cs.obj.O, please shift to types.SimpleNamespace."
          % (frame[0], frame[1], frame[2]),
          file=sys.stderr
      )
    SimpleNamespace.__init__(self, **kw)

def O_merge(o, _conflict=None, _overwrite=False, **kw):
  ''' Merge key:value pairs from a mapping into an object.

      Ignore keys that do not start with a letter.
      New attributes or attributes whose values compare equal are
      merged in. Unequal values are passed to:

          _conflict(o, attr, old_value, new_value)

      to resolve the conflict. If _conflict is omitted or None
      then the new value overwrites the old if _overwrite is true.
  '''
  for attr, value in kw.items():
    if attr or not attr[0].isalpha():
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
  ''' Yield attribute names from `o` which are pertinent to `O_str`.

      Note: this calls `getattr(o,attr)` to inspect it in order to
      prune callables.
  '''
  for attr in sorted(dir(o)):
    if attr[0].isalpha():
      try:
        value = getattr(o, attr)
      except AttributeError:
        continue
      if not callable(value):
        yield attr

def O_attritems(o):
  ''' Generator yielding `(attr,value)` for relevant attributes of `o`.
  '''
  for attr in O_attrs(o):
    try:
      value = getattr(o, attr)
    except AttributeError:
      continue
    else:
      yield attr, value

def O_str(o, no_recurse=False, seen=None):
  ''' Return a `str` representation of the object `o`.

      Parameters:
      * `o`: the object to describe.
      * `no_recurse`: if true, do not recurse into the object's structure.
        Default: `False`.
      * `seen`: a set of previously sighted objects
        to prevent recursion loops.
  '''
  if seen is None:
    seen = set()
  obj_type = type(o)
  if obj_type in StringTypes:
    return repr(o)
  if obj_type in (tuple, int, float, bool, list):
    return str(o)
  if obj_type is dict:
    o2 = dict([(k, str(v)) for k, v in o.items()])
    return str(o2)
  if obj_type is set:
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
        desc = "%s=%s" % (
            pattr, O_str(pvalue, no_recurse=no_recurse, seen=seen)
        )
      attrdesc_strs.append(desc)
  s = "<%s %s>" % (o.__class__.__name__, ",".join(attrdesc_strs))
  seen.remove(id(o))
  return s

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
      match = lambda attr: attr and not attr.startswith('_')
    else:
      match = lambda attr: attr.startswith(attr_prefix)
  elif attr_prefix is None:
    match = attr_match
  else:
    raise ValueError("cannot specify both attr_prefix and attr_match")
  obj_attrs = {}
  for attr in dir(o):
    if match(attr):
      obj_attrs[attr] = getattr(o, attr)
  return obj_attrs

class Proxy(object):
  ''' An extremely simple proxy object
      that passes all unmatched attribute accesses to the proxied object.

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

class TrackedClassMixin(object):
  ''' A mixin to track all instances of a particular class.

      This is aimed at checking the global state of objects of a
      particular type, particularly states like counters. The
      tracking is attached to the class itself.

      The class to be tracked includes this mixin as a superclass and calls:

          TrackedClassMixin.__init__(class_to_track)

      from its __init__ method. Note that `class_to_track` is
      typically the class name itself, not `type(self)` which would
      track the specific subclass. At some relevant point one can call:

          self.tcm_dump(class_to_track[, file])

      `class_to_track` needs a `tcm_get_state` method to return the
      salient information, such as this from cs.resources.MultiOpenMixin:

          def tcm_get_state(self):
              return {'opened': self.opened, 'opens': self._opens}

      See cs.resources.MultiOpenMixin for example use.
  '''

  def __init__(self, cls):
    try:
      m = cls.__map
    except AttributeError:
      m = cls.__map = {}
    m[id(self)] = self

  def __state(self, cls):
    return cls.tcm_get_state(self)

  @staticmethod
  def tcm_all_state(klass):
    ''' Generator yielding tracking information
        for objects of type `klass`
        in the form `(o,state)`
        where `o` if a tracked object
        and `state` is the object's `get_tcm_state` method result.
    '''
    m = klass.__map
    for o in m.values():
      yield o, klass.__state(o, klass)

  @staticmethod
  def tcm_dump(klass, f=None):
    ''' Dump the tracking information for `klass` to the file `f`
        (default `sys.stderr`).
    '''
    if f is None:
      f = sys.stderr
    for o, state in TrackedClassMixin.tcm_all_state(klass):
      print(str(type(o)), id(o), repr(state), file=f)

def singleton(registry, key, factory, fargs, fkwargs):
  ''' Obtain an object for `key` via `registry` (a mapping of `key`=>`object`.
      Return `(is_new,instance)`.

      If the `key` exists in the registry, return the associated object.
      Otherwise create a new object by calling `factory(*fargs,**fkwargs)`
      and store it as `key` in the `registry`.

      The `registry` may be any mapping of `key`s to objects
      but might usually be a `weakref.WeakValueMapping`
      to that object references expire as normal.

      *Note*: this function *is not* thread safe.
      Multithreaded users should hold a mutex.

      See the `SingletonMixin` class for a simple mixin to create
      singleton classes,
      which does provide thread safe operations.
  '''
  try:
    instance = registry[key]
    is_new = False
  except KeyError:
    instance = factory(*fargs, **fkwargs)
    registry[key] = instance
    is_new = True
  return is_new, instance

class SingletonMixin:
  ''' A mixin turning a subclass into a singleton factory.

      *Note*: this should be the *first* superclass of the subclass
      in order to intercept `__new__` and `__init__`.

      A subclass should:
      * *not* provide an `__init__` method.
      * provide a `_singleton_init` method in place of the normal `__init__`
        with the usual signature `(self,*args,**kwargs)`.
      * provide a `_singleton_key(cls,*args,**kwargs)` class method
        returning a key for the single registry
        computed from the positional and keyword arguments
        supplied on instance creation
        i.e. those which `__init__` would normally receive.
        This should have the same signature as `_singleton_init`
        (but using `cls` instead of `self`).

      This class is thread safe for the registry operations.

      Example:

          class Pool(SingletonMixin):

              @classmethod
              def _singleton_key(cls, foo, bah=3):
                  return foo, bah

              def _singleton_init(self, foo, bah=3):
                 ... normal __init__ stuff here ...
  '''

  # This lock is used to control setup of the per-class registry.
  # It is shared across all subclasses, but that bypasses any need to call an
  # __init__ for this mixin. In mitigation, the lock is only used if the class
  # does not yet have a registry.
  _global_lock = Lock()

  def __new__(cls, *a, **kw):
    ''' Prepare a new instance of `cls` if required.
        Return the instance.

        This creates the class registry if missing,
        and then
    '''
    try:
      registry = cls._singleton_registry
    except AttributeError:
      with cls._global_lock:
        try:
          registry = cls._singleton_registry
        except AttributeError:
          # create the registry and give it its own mutex
          registry = cls._singleton_registry = WeakValueDictionary()
          registry._singleton_lock = Lock()

    def factory(*fargs, **fkwargs):
      ''' Prepare a new object.

          Call `object.__new__(cls)` and then `o._singleton_init(*a,**kw)`
          on the new object.
      '''
      o = object.__new__(cls)
      o._singleton_init(*fargs, **fkwargs)
      return o

    okey = cls._singleton_key(*a, **kw)
    with registry._singleton_lock:
      _, instance = singleton(registry, okey, factory, a, kw)
    return instance
