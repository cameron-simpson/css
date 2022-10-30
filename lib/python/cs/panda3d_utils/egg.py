#!/usr/bin/env python3

''' Panda3d Egg format.

    Because Panda3d seems to load things from `.egg` files
    and some other compiled formats
    this module is aimed at writing Egg files.
    As such it contains functions and classes for making
    entities found in Egg files, and for writing these out in Egg syntax.
    The entities are _not_ directly useable by Panda3d itself,
    they get into panda3d by being written as Egg and loaded;
    see the `load_model` function.

    The following are provided:
    * `quote(str)`: return a string quoted correctly for an Egg file
    * `dump`,`dumps`,dumpz`: after the style of `json.dump`, functions
      to dump objects in Egg syntax
    * `Eggable`: a mixin to support objects which can be transcribed in Egg syntax
    * `DCEggable`: an `Eggable` based on a dataclass
    * `Eggable.as_str(obj)`: a class method to transcribe an object in Egg syntax,
      accepting `Eggable`s, `str`s and numeric values
    * various factories and classes for Egg nodes: `Texture`,
      `Vertex` and so forth
'''

from collections import defaultdict, namedtuple
from contextlib import contextmanager
from dataclasses import dataclass, field as dataclass_field, fields as dataclass_fields
from random import randint
from tempfile import NamedTemporaryFile
from typing import Callable, Hashable, Iterable, Mapping, Optional, Union
from zlib import compress

from typeguard import typechecked

from cs.context import ContextManagerMixin
from cs.deco import decorator, default_params, fmtdoc
from cs.fileutils import atomic_filename
from cs.lex import is_identifier, r
from cs.logutils import warning
from cs.mappings import IndexedMapping, StrKeyedDict
from cs.numeric import intif
from cs.pfx import Pfx, pfx, pfx_call, pfx_method
from cs.psutils import run
from cs.queues import ListQueue
from cs.seq import Seq, unrepeated
from cs.threads import State as ThreadState


# the default coordinate system to use in model files
DEFAULT_COORDINATE_SYSTEM = 'Z-up'

# a mapping of reference Egg type names to the class to which it refers,
# for example 'TRef' => Texture.
REFTYPES = IndexedMapping(pk='typename')

class RefTypeSpec(namedtuple('RefTypeSpec', 'type refname')):

  @property
  def typename(self):
    return self.type.__name__

  def __getitem__(self, index):
    if isinstance(index, str):
      try:
        return getattr(self, index)
      except AttributeError:
        raise KeyError("no attribute %r" % (index,))
    else:
      return super().__getitem__(index)

@pfx
def quote(text):
  ''' Quote a piece of text for inclusion in an Egg file.
  '''
  return (
      text if is_identifier(text.replace('-', '_')) else
      ('"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"')
  )

class EggRegistry(defaultdict, ContextManagerMixin):
  _seq = Seq()

  def __init__(self, name=None):
    if name is None:
      name = f'{self.__class__.__name__}{self._seq()}'
    self.name = name
    super().__init__(dict)

  def __str__(self):
    return f'{self.__class__.__name__}({self.name!r})'

  def __repr__(self):
    return str(self)

  def __enter_exit__(self):
    with state(registry=self):
      yield self

  @typechecked
  def register(self, instance: 'Eggable'):
    ''' Register this `instance`.
        The same instance may be registered more than once.
    '''
    name = instance.egg_name()
    egg_cls = type(instance)
    assert name is not None
    instances = self[id(egg_cls)]
    try:
      existing = instances[name]
    except KeyError:
      instances[name] = instance
    else:
      if existing is not instance:
        raise RuntimeError(
            "%s.register: name %r already registered to %s:%d" %
            (self, name, instance.__class__.__name__, id(existing))
        )

  @pfx_method(use_str=True)
  @typechecked
  def instance(self, egg_cls, name: str):
    ''' Return the `egg_cls` instance named `name`.
    '''
    instances = self[id(egg_cls)]
    try:
      return instances[name]
    except KeyError:
      warning(
          "%s: no %r in instances[id(%s)]: %r", self, name, egg_cls.__name__,
          instances
      )
      raise

  def ref_for(self, egg_node):
    ''' Return a reference `EggNode` for an Eggable node `egg_node`,
        or `None`.

        For example, since `<TRef> { name }` is defined as a reference
        type for `Texture` nodes, a `Texture("foo","foo_image.png")`
        would return an `EggNode("TRef",None,"fpp")` if the `Texture`
        was in this registry.
    '''
    name = egg_node.egg_name()
    if name is None:
      # cannot make a reference to an unnamed node
      return None
    try:
      refspec = REFTYPES.by_typename[egg_node.egg_type()]
    except KeyError:
      # there's no reftype for this type
      return None
    instance = self.instance(refspec.type, name)
    if instance is None:
      # this is not a known instance
      return None
    return EggNode(refspec.refname, None, [name])

# a stackable state
_registry0 = EggRegistry(__file__)
state = ThreadState(registry=_registry0)

@decorator
def uses_registry(func):

  @default_params(registry=lambda: state.registry)
  def with_registry(*a, registry, **kw):
    assert registry is not _registry0
    with registry:
      return func(*a, registry=registry, **kw)

  return with_registry

class EggMetaClass(type):

  # mapping of ClassName.lower() => ClassName
  # possibly to cononicalise attributes
  # though I'm currently just distinguishing based on Eggable vs str/float
  egg_classnames_by_lc = {}

  # mapping of id(type(instance))=>instance.name=>instance
  egg_instances = defaultdict(dict)

  def __init__(self, class_name, bases, namespace, **kwds):
    if class_name[0].isupper():
      # record the canonical
      class_name_lc = class_name.lower()
      assert class_name_lc not in self.egg_classnames_by_lc, (
          "new class %r: %r already maps to %r" % (
              class_name, class_name_lc,
              self.egg_classnames_by_lc[class_name_lc]
          )
      )
      self.egg_classnames_by_lc[class_name_lc] = class_name

class Eggable(metaclass=EggMetaClass):
  ''' A base class for objects which expect to be transcribed in Egg syntax.
      Implementations of these objects should provide the following methods:
      * `egg_name`: return the name of this object, or `None`
      * `egg_type`: return the Egg node type of this object
      * `egg_contents`: return an iterable of items contained by this Egg node

      A common implementation for Egg nodes is based on a data class,
      see the `DCEggable` subclass which is oriented to this.

      This base implementation provides the following implementations:
      * `egg_name`: returns `self.name` or `None` if that is missing
      * `egg_type`: the class name
      * `egg_contents`: if there is a `.attrs` mapping attribute,
        yield its entries, processed as detailed by the method docstring
  '''

  @uses_registry
  def register(self, *, registry):
    ''' Register this `instance` in `registry`, default `state.registry`.
        The same instance may be registered more than once.
    '''
    return registry.register(self)

  @classmethod
  @uses_registry
  @typechecked
  def instance(cls, name: str, *, registry):
    ''' Return the instance named `name` from `registry`,
        default `state.registry`.
    '''
    assert name is not None
    return registry.instance(cls, name)

  def __str__(self):
    return "".join(self.egg_transcribe())

  @classmethod
  @uses_registry
  def transcribe(cls, item, indent='', *, registry):
    ''' A generator yielding `str`s which transcribe `item` in Egg syntax.
    '''
    if isinstance(item, Eggable):
      ref_node = registry.ref_for(item)
      if ref_node is None:
        yield from item.egg_transcribe(indent)
      else:
        yield from ref_node.egg_transcribe(indent)
    elif isinstance(item, str):
      yield quote(item)
    elif isinstance(item, (int, float)):
      yield str(intif(item))
    else:
      raise TypeError(
          "%s.transcribe: unhandled type for item %s" % (
              cls.__name__,
              r(item),
          )
      )

  @classmethod
  def as_str(cls, item, indent=""):
    ''' Return the `str` representation of `item` in Egg syntax.
    '''
    return "".join(cls.transcribe(item, indent=indent))

  def egg_name(self):
    ''' Return the name of this Egg node, or `None`.
        This default returns `self.name` if present, otherwise `None`.
    '''
    return getattr(self, 'name', None)

  def egg_type(self):
    ''' Return the Egg node type name.
    '''
    return self.__class__.__name__

  @pfx_method
  def egg_contents(self):
    ''' Return an iterable of the `Eggable` contents.

        This base implementation is a generator which yields the
        contents of `self.attrs` if present.
        For each `attr,value` pair in the mapping:
        * ignore entries whose `value` is `None`
        * yield `Eggable` instances directly
        * yield `str`, `int` or `float` as `<Scalar>` Egg nodes named after `attr`
        * yield `tuples` as `<attr> { *tuple }` eg `UV { 0 1 }`
        Other values raise a `TypeError`.
    '''
    for attr, value in getattr(self, 'attrs', {}).items():
      if value is None:
        continue
      if isinstance(value, Eggable):
        yield value
      elif isinstance(value, (str, int, float)):
        yield EggNode('Scalar', attr, [value])
      elif isinstance(value, tuple):
        yield EggNode(attr, None, value)
      else:
        raise TypeError(
            "%s.attrs[%r]=%s: not slacar or Eggable" %
            (self.__class__.__name__, attr, value)
        )

  @uses_registry
  def egg_transcribe(self, indent='', *, registry):
    ''' A generator yielding `str`s which transcribe `self` in Egg syntax.
    '''
    subindent = indent + "  "
    content_break = "\n" + subindent
    content_parts = []
    had_break = False
    had_breaks = False
    for item in self.egg_contents():
      with Pfx("<%s>.egg_transcribe: item=%r", self.egg_type(), item):
        item_sv = list(self.transcribe(item, subindent))
        assert len(item_sv) > 0
        if (had_break or item_sv[-1].endswith('}')
            or any(map(lambda item_s: '\n' in item_s, item_sv))):
          # a line break before and after any complex item
          content_parts.append(content_break)
          had_break = True
          had_breaks = True
        else:
          # otherwise write things out on one line
          content_parts.append(" ")
          had_break = False
        content_parts.extend(item_sv)
    if content_parts:
      if had_breaks:
        if content_parts[0] == " ":
          # if there were breaks, indent the leading item
          content_parts[0] = content_break
        # end with a break
        content_parts.append("\n")
        content_parts.append(indent)
      else:
        # end with a space
        content_parts.append(" ")
    yield f'<{self.egg_type()}>'
    name = self.egg_name()
    if name is not None:
      yield " "
      yield from self.transcribe(name)
    yield " {"
    yield from content_parts
    yield "}"

  def check(self):
    ''' Check an `Eggable` `item` for consistency.
    '''
    q = ListQueue([self])
    for item in unrepeated(q, signature=id):
      typename = item.egg_type()
      name = item.egg_name()
      with Pfx("<%s>%s.check()", typename, name or ""):
        if name is not None:
          # check the reference in things like <TRef> { texture-name }
          try:
            refspec = REFTYPES.by_refname[typename]
          except KeyError:
            pass
          else:
            ref_name, = item.egg_contents()
            ref_type = refspec.type
            assert isinstance(ref_name, str)
            pfx_call(ref_type.instance, ref_name)
        # check all the contained items
        for subitem in item.egg_contents():
          if isinstance(subitem, Eggable):
            q.append(subitem)

class EggableList(list, Eggable):
  ''' An `Eggable` which is just a list of items, such as `<Transform>`.
  '''

  def __str__(self):
    return Eggable.__str__(self)

  def egg_contents(self):
    return iter(self)

class DCEggable(Eggable):
  ''' `Eggable` subclass for dataclasses.

       This provides an `egg_contents` method which enumerates the
       fields in order, except for a field named `attrs` whose
       contents is yielded last as for the `Eggable.egg_contents`
       method.
  '''

  @classmethod
  def promote(cls, obj):
    ''' Promote `obj` to `cls`. Return the promoted object.
    '''
    if not isinstance(obj, cls):
      if isinstance(obj, (list, tuple)):
        obj = cls(*obj)
      elif isinstance(obj, dict):
        obj = cls(**obj)
      else:
        raise TypeError(
            "%s.promote: do not know how to promote %s" % (cls, r(obj))
        )
    return obj

  def egg_name(self):
    ''' The Egg has a name if it has a `.name` which is not `None`.
    '''
    return getattr(self, 'name', None)

  def egg_contents(self):
    ''' Generator yielding the `EggNode` contents.
        This implementation yields the non-`None` field values in order,
        then the contents of `self.attrs` if present.
    '''
    # yield positional fields in order
    positional_fields = getattr(self.__class__, 'POSITIONAL', ())
    if isinstance(positional_fields, bool):
      all_positional = positional_fields
      positional_fields = (
          tuple(
              F.name
              for F in dataclass_fields(self)
              if F.name not in ('attrs', 'name')
          ) if all_positional else ()
      )
    assert 'name' not in positional_fields and 'attrs' not in positional_fields
    none_field_name = None
    for field_name in positional_fields:
      value = getattr(self, field_name)
      if value is None:
        if none_field_name is None:
          none_field_name = field_name
      else:
        if none_field_name is not None:
          raise ValueError(
              "None positional field %r is followed by non-None positional field %r:%s"
              % (none_field_name, field_name, r(value))
          )
        yield value
    for F in dataclass_fields(self):
      field_name = F.name
      if field_name in ('attrs', 'name') or field_name in positional_fields:
        continue
      value = getattr(self, field_name)
      if value is not None:
        if isinstance(value, (str, int, float)):
          yield EggNode('Scalar', field_name, [value])
        elif isinstance(value, Eggable):
          yield value
        else:
          yield EggNode(field_name, None, value)
    # yield named attrs if present
    yield from super().egg_contents()

def dumps(obj, indent=""):
  ''' Return a string containing `obj` in Egg syntax.
  '''
  return Eggable.as_str(obj, indent=indent)

def dump(obj, f, indent=""):
  ''' Write `obj` to the text file `f` in Egg syntax.
  '''
  if isinstance(f, str):
    with open(f, 'w') as f2:
      dump(obj, f2, indent=indent)
  else:
    f.write(dumps(obj, indent=indent))

def dumpz(obj, f, indent=""):
  ''' Write `obj` to the binary file `f` in Egg syntax, zlib compressed.
  '''
  if isinstance(f, str):
    with open(f, 'wb') as f2:
      dumpz(obj, f2, indent=indent)
  else:
    f.write(compress(dumps(obj, indent=indent).encode('utf-8')))

# TODO: make a Model and transcribe it
def load_model(
    loader,
    comment: str,
    egg_nodes: Iterable[Eggable],
    *,
    coordinate_system=None,
    skip_check=False,
):
  ''' Load an iterable of `Eggable` nodes `egg_nodes` as a model
      via the supplied loader.

      This constructs a `Model` and calls its `.load()` method.
  '''
  M = Model(comment, coordinate_system=coordinate_system)
  M.extend(egg_nodes)
  return M.load(loader, skip_check=skip_check)

class EggNode(Eggable):
  ''' A representation of a basic EGG syntactic node with an explicit type.
  '''

  @pfx
  @typechecked
  def __init__(
      self, typename: str, name: Optional[Union[str, int]], contents: Iterable,
      **kw
  ):
    assert not isinstance(contents, str)  # str is iterable :-(
    self.typename = typename
    self.name = name
    self.contents = contents
    self.attrs = kw

  def egg_type(self):
    return self.typename

  def egg_contents(self):
    return self.contents

@dataclass
class Normal(DCEggable):
  POSITIONAL = True
  x: float
  y: float
  z: float

@dataclass
class BiNormal(DCEggable):
  POSITIONAL = True
  x: float
  y: float
  z: float

@dataclass
class Tangent(DCEggable):
  POSITIONAL = True
  x: float
  y: float
  z: float

@dataclass
class UV(DCEggable):
  POSITIONAL = True
  u: float
  v: float
  Tangent: Optional[Tangent] = None
  BiNormal: Optional[BiNormal] = None

@dataclass
class RGBA(DCEggable):
  POSITIONAL = True
  r: float
  g: float
  b: float
  a: float = 1.0

  @classmethod
  def random(cls, r=None, g=None, b=None, a=None):
    if r is None:
      r = randint(0, 255)
    if g is None:
      g = randint(0, 255)
    if b is None:
      b = randint(0, 255)
    if a is None:
      a = randint(0, 255)
    return cls(r, g, b, a)

@dataclass
class Translate(DCEggable):
  POSITIONAL = True
  x: float
  y: float
  z: float

@dataclass
class RotX(DCEggable):
  POSITIONAL = True
  degrees: float

@dataclass
class RotY(DCEggable):
  POSITIONAL = True
  degrees: float

@dataclass
class RotZ(DCEggable):
  POSITIONAL = True
  degrees: float

@dataclass
class Rotate(DCEggable):
  POSITIONAL = True
  degrees: float
  x: Optional[float] = None
  y: Optional[float] = None
  z: Optional[float] = None

  @classmethod
  def promote(cls, rotate):
    ''' Promote `rotate` to a `Rotate` instance.
    '''
    if isinstance(rotate, (int, float)):
      rotate = cls(rotate)
    else:
      rotate = super().promote(rotate)
    return rotate

@dataclass
class Scale1(DCEggable):
  POSITIONAL = True
  egg_name = lambda _: 'Scale'
  s: float

@dataclass
class Scale3(DCEggable):
  POSITIONAL = True
  egg_name = lambda _: 'Scale'
  x: float
  y: float
  z: float

@dataclass
class Matrix4(DCEggable):
  POSITIONAL = True
  aa: float
  ab: float
  ac: float
  ad: float
  ba: float
  bb: float
  bc: float
  bd: float
  ca: float
  cb: float
  cc: float
  cd: float
  da: float
  db: float
  dc: float
  dd: float

class Transform(EggableList):
  pass

@dataclass
class Vertex(DCEggable):
  POSITIONAL = 'x', 'y', 'z', 'w'
  x: float
  y: float
  z: float
  w: Optional[float] = None
  Dxyz: Optional[Dxyz] = None
  Normal: Optional[Normal] = None
  RGBA: Optional[RGBA] = None
  UV: Optional[UV] = None
  attrs: Mapping = dataclass_field(default_factory=dict)

  def __copy__(self):
    ''' Shallow copy: copy the coordinates, make a new shallow dict for the `attrs`.
    '''
    return self.__class__(
        x=self.x, y=self.y, z=self.z, w=self.w, attrs=dict(self.attrs)
    )

class VertexPool(Eggable):
  ''' A subclass of `list` containing vertices.
  '''

  @typechecked
  def __init__(
      self, name: str, vertices: Optional[Iterable] = None, *, keyfn=None
  ):
    if keyfn is None:
      keyfn = self.default_vertex_keyfn
    self.name = name
    self._by_vkey: Mapping[Hashable, (int, Vertex)] = {}
    self.keyfn = keyfn
    self.register()

  def __len__(self):
    return len(self.vertices)

  def __iter__(self):
    ''' Iteration yields the `vertices`.
    '''
    return iter(self.vertices)

  def egg_contents(self):
    ''' The Egg contents are synthetic `vertex` nodes numbered by their position.
    '''
    yield from (
        EggNode(v.egg_type(), i, v.egg_contents())
        for i, v in sorted(self._by_vkey.values(), key=lambda iv: iv[0])
    )

  def default_vertex_keyfn(self, v: Vertex) -> Hashable:
    ''' The default key function for a `Vertex`,
        returning a 5-tuple of `(x,y,z,w,attrs)` from the `Vertex`,
        where the `attrs` part is a tuple of `sorted(v.attrs.items())`.
    '''
    return v.x, v.y, v.z, v.w, tuple(sorted(v.attrs.items()))

  def vertex_index(self, v: Vertex) -> int:
    ''' Return the index of this `Vertex`.
        If the vertex key `self.vertex_keyfn(v)` is unknown,
        store `v` in the vertex map as the reference `Vertex`.
    '''
    vmap = self._by_vkey
    vkey = self.keyfn(v)
    try:
      index, _ = vmap[vkey]
    except KeyError:
      index = len(vmap) + 1
      assert vkey not in vmap
      vmap[vkey] = (index, v)
    return index

  def vertex_by_index(self, index: int) -> Vertex:
    return self.vertex_map[index]

class Texture(Eggable):
  ''' An Egg `Texture` definition.
  '''

  @typechecked
  def __init__(
      self,
      name: str,
      texture_image: str,
      *,
      format: str = 'rgb',
      wrapu: str = 'repeat',
      wrapv: str = 'repeat',
      minfilter: str = 'linear_mipmap_linear',
      magfilter: str = 'linear',
      **kw,
  ):
    self.name = name
    self.texture_image = texture_image
    self.attrs = StrKeyedDict(
        format=format,
        wrapu=wrapu,
        wrapv=wrapv,
        minfilter=minfilter,
        magfilter=magfilter,
        **kw,
    )

  def egg_contents(self):
    yield self.texture_image
    yield from super().egg_contents()

REFTYPES.add(RefTypeSpec(type=Texture, refname='TRef'))

@dataclass
class Material(DCEggable):
  name: str
  diffr: Optional[float] = None
  diffg: Optional[float] = None
  diffb: Optional[float] = None
  diffa: Optional[float] = None
  ambr: Optional[float] = None
  ambg: Optional[float] = None
  ambb: Optional[float] = None
  amba: Optional[float] = None
  emitr: Optional[float] = None
  emitg: Optional[float] = None
  emitb: Optional[float] = None
  emita: Optional[float] = None
  specr: Optional[float] = None
  specg: Optional[float] = None
  specb: Optional[float] = None
  speca: Optional[float] = None
  shininess: Optional[float] = None
  local: Optional[bool] = False

REFTYPES.add(RefTypeSpec(type=Material, refname='MRef'))

class Polygon(Eggable):

  @typechecked
  def __init__(
      self,
      name: Optional[str],
      pool: Union[str, VertexPool],
      *indices,
      **attrs,
  ):
    if isinstance(pool, str):
      vpool = VertexPool.instance(pool)
    elif isinstance(pool, VertexPool):
      vpool = pool
    else:
      raise TypeError(
          "unhandled pool_name type %s, expected str or VertexPool" %
          (type(pool),)
      )
    if not indices:
      indices = list(range(1, len(vpool) + 1))
    self.name = name
    self.vpool = vpool
    self.name = name
    self.attrs = attrs
    self.indices = indices

  def egg_contents(self):
    yield from super().egg_contents()
    yield EggNode(
        'VertexRef',
        None,
        (
            list(
                self.indices if self
                .indices else range(1,
                                    len(self.vpool) + 1)
            ) + [EggNode('Ref', None, [self.vpool.name])]
        ),
    )

  def check(self):
    with Pfx("%s.check", self.__class__.__name__):
      super().check()
      vpool = self.vpool
      for index in self.indices:
        assert index > 0 and index <= len(vpool), \
            f'index {index} not in range for VertexPool'

class Group(Eggable):

  def __init__(self, name: Optional[str], *a):
    self.name = name
    self.items = list(a)

  def __iter__(self):
    return iter(self.items)

  def append(self, item):
    self.items.append(item)

  def extend(self, more_items):
    self.items.extend(more_items)

  def egg_contents(self):
    return self.items

class Instance(Group):
  ''' An `<Instance>` is like a `<Group>` but with local coordinates.

      The docs are unforthcoming, and I'm hoping the coords are
      thus pretransform rather than posttransform.
  '''

class Model(ContextManagerMixin):

  @typechecked
  def __init__(self, comment: str, *, coordinate_system: Optional[str] = None):
    if coordinate_system is None:
      coordinate_system = DEFAULT_COORDINATE_SYSTEM
    self.comment = comment
    self.coordinate_system = coordinate_system
    self._registry = EggRegistry(
        f'{self.__class__.__name__}:{comment!r}:{id(self)}'
    )
    self.items = []

  def __str__(self):
    return "%s:%r:%r[%d]" % (
        self.__class__.__name__, self.comment, self.coordinate_system,
        len(self.items)
    )

  def __enter_exit__(self):
    ''' Context manager: push `self._register` as `state.registry`.
    '''
    with self._registry:
      yield self

  @typechecked
  def append(self, item: Eggable):
    ''' Append `item` to the model.
    '''
    item.register(registry=self._registry)
    self.items.append(item)

  @typechecked
  def extend(self, items: Iterable[Eggable]):
    ''' Extend the model with `items`, an iterable of `Eggable`s.
    '''
    for item in items:
      self.append(item)

  @pfx_method
  def check(self):
    ''' Check the model for consistency.
    '''
    with self._registry:
      for item in self.items:
        item.check()

  def save(self, fspath, *, skip_check=False, exists_ok=False):
    ''' Save this model to the filesystem path `fspath`.
    '''
    if not skip_check:
      self.check()
    with self._registry:
      with atomic_filename(fspath, exists_ok=exists_ok) as T:
        with open(T.name, 'w') as f:
          with self:
            print(EggNode('Comment', None, [self.comment]), file=f)
            print(
                EggNode('CoordinateSystem', None, [self.coordinate_system]),
                file=f
            )
            for item in self.items:
              for s in item.egg_transcribe(indent='  '):
                f.write(s)
              f.write('\n')
              ##print(item, file=f)

  def load(self, loader, *, skip_check=False):
    ''' Load this model via `loader.loadModel`.
    '''
    with NamedTemporaryFile(suffix='.egg') as T:
      M.save(T.name, exists_ok=True)
      return loader.loadModel(T.name)

  def view(
      self, *, centre=True, lighting=False, skip_check=False, quiet=False
  ):
    ''' Quick view of the `Model` using `pview`.
    '''
    pview_opts = ['-l']
    if centre:
      pview_opts.append('-c')
    if lighting:
      pview_opts.append('-L')
    with NamedTemporaryFile(suffix='.egg') as T:
      self.save(T.name, skip_check=skip_check, exists_ok=True)
      run(['cat', T.name])
      run(['pview', *pview_opts, T.name])

if __name__ == '__main__':
  for eggable in (
      Normal(4, 5, 6),
      UV(6, 7),
      Vertex(1, 2, 3, attrs=dict(normal=Normal(4, 5, 6), uv=UV(6, 7))),
      VertexPool(
          "vpool1",
          [Vertex(1, 2, 3, attrs=dict(normal=Normal(4, 5, 6), uv=UV(6, 7)))],
      ),
      Texture("texture1", "texture1.png"),
      pfx_call(
          Polygon,
          "polyname",
          "vpool1",
          rgba=RGBA(1, 1, 1, 1),
          tref=Texture("foo", "tpath.png"),
          vertexref="vertexref",
      ),
      Group(None, Texture("texture2", "texture2.png")),
  ):
    print(eggable)
  M = Model("test model")
  print(M)
  M.check()
  with M:
    normal = Normal(4, 5, 6)
    uv = UV(6, 7)
    v = Vertex(1, 2, 3, attrs=dict(normal=normal, uv=uv))
    vpool = VertexPool(
        "vpool2",
        [v, v],
    )
    texture = Texture("texture2", "texture2.png")
    for item in vpool, texture:
      M.append(item)
    M.check()
    M.append(
        Polygon(
            "polyname",
            "vpool2",
            rgba=RGBA(1, 1, 1, 1),
            tref=texture,
            vertexref="vertexref",
        )
    )
    M.check()
    M.save("model-out.egg")
