#!/usr/bin/env python3

''' Panda3d Egg format.

    Because Panda3d seems to load things from `.egg` files
    and some other compiled formats
    this module is aimed at writing Egg files.
    As such it contains functions and classes for making
    entities found in Egg files, and for writing these out in Egg syntax.
    The entities are _not_ directly useable by Pada3d itself,
    they get into panda3d by being written as Egg and loaded;
    see the `load_model` function.

    The following are provided:
    * `quote(str)`: return a string quoted correctly for an Egg file
    * `dump`,`dumps`,dumpz`: after the style of `json.dump`, functions
      to dump objects in Egg syntax
    * `Eggable`: a mixin to support objects which can be transcribed in Egg syntax
    * `Eggable.as_str(obj)`: a class method to transcribe an object in Egg syntax,
      accepting `Eggable`s, `str`s and numeric values
    * various factories and classes for Egg nodes: `Texture`,
      `Vertex` and so forth
'''

from collections import defaultdict, namedtuple
from dataclasses import dataclass, field as dataclass_field, fields as dataclass_fields
from tempfile import NamedTemporaryFile
from typing import Any, Iterable, Mapping, Optional, Tuple, Union
from zlib import compress

from typeguard import typechecked

from cs.context import ContextManagerMixin
from cs.deco import fmtdoc
from cs.fileutils import atomic_filename
from cs.lex import is_identifier, r
from cs.logutils import warning
from cs.mappings import StrKeyedDict
from cs.numeric import intif
from cs.pfx import Pfx, pfx, pfx_call, pfx_method
from cs.seq import Seq
from cs.threads import State as ThreadState

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

# a stackable state
state = ThreadState(registry=EggRegistry(__file__))

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

      The usual implementation of an objects is as a dataclass, example:

          @dataclass
          class UV(Eggable):
              u: float
              v: float

      and the default `__iter__` (and therefore `egg_contents`) assume this.
  '''

  def register(self, registry=None):
    ''' Register this `instance` in `registry`, default `state.registry`.
        The same instance may be registered more than once.
    '''
    if registry is None:
      registry = state.registry
    return registry.register(self)

  @classmethod
  @typechecked
  def instance(cls, name: str, registry=None):
    ''' Return the instance named `name` from `registry`,
        default `state.registry`.
    '''
    assert name is not None
    if registry is None:
      registry = state.registry
    return registry.instance(cls, name)

  def __str__(self):
    return "".join(self.egg_transcribe())

  @classmethod
  def transcribe(cls, item, indent=""):
    ''' A generator yielding `str`s which transcribe `item` in Egg syntax.
    '''
    if isinstance(item, Eggable):
      yield from item.egg_transcribe(indent)
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
    ''' Generator yielding the `EggNode` contents.
        This base implementation yields the contents of `self.attrs` if present.
    '''
    for attr, value in getattr(self, 'attrs', {}).items():
      if value is None:
        continue
      if isinstance(value, (str, int, float)):
        yield EggNode('Scalar', attr, [value])
      elif isinstance(value, Eggable):
        yield value
      else:
        raise TypeError(
            "%s.attrs[%r]=%s: not slacar or Eggable" %
            (self.__class__.__name__, attr, value)
        )

  def egg_transcribe(self, indent=''):
    ''' A generator yielding `str`s which transcribe `self` in Egg syntax.
    '''
    subindent = indent + "  "
    content_break = "\n" + subindent
    content_parts = []
    had_break = False
    had_breaks = False
    for item in self.egg_contents():
      with Pfx("%r.egg_transcribe: item=%r", type(self), item):
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
    yield self.egg_type()
    name = self.egg_name()
    if name is not None:
      yield " " + quote(name)
    yield " {"
    yield from content_parts
    yield "}"

class DCEggable(Eggable):
  ''' `Eggable` superclass for dataclasses.
  '''

  def egg_contents(self):
    ''' Generator yielding the `EggNode` contents.
        This implementation yields the non-`None` field values in order,
        then the contents of `self.attrs` if present.
    '''
    for F in dataclass_fields(self):
      value = getattr(self, F.name)
      if F.name != 'attrs' and value is not None:
        yield value
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

@fmtdoc
@typechecked
def write_model(fspath: str, comment: str, *, coordinate_system=None):
  ''' Context manager for writing an Egg model file to the path
      `fspath` which yields an open file for the model contents.

      Parameters:
      * `fspath`: the filesystem path of the model file to create
      * `comment`: opening comment for the file
      * `coordinate_system`: coordinate system for the file,
       default `{DEFAULT_COORDINATE_SYSTEM!r}` from `DEFAULT_COORDINATE_SYSTEM`

      This uses `atomic_filename` to create the file so it will not
      exist in the filesystem until it is complete.

      Example use:

          with write_model("my_model.egg", "test model") as f:
            for nodes in egg_nodes:
              print(node, file=f)
  '''
  if coordinate_system is None:
    coordinate_system = DEFAULT_COORDINATE_SYSTEM
  with atomic_filename(fspath) as T:
    with open(T.name, 'w') as f:
      print(EggNode('Comment', None, comment), file=f)
      print(EggNode('CoordinateSystem', None, coordinate_system), file=f)
      yield f

def load_model(
    loader, comment: str, egg_nodes: Iterable, *, coordinate_system=None
):
  ''' Load an iterable of `Eggable` nodes `egg_nodes` as a model
      via the supplied loader.

      This transcribes the `egg_nodes` to a temporary Egg file using
      `write_model` and then calls `loader.load_model(filename)`
      to load that file, returning the resulting scene.

      Example use:

          scene = load_model(showbase.loader, "my model", egg_nodes)
  '''
  with NamedTemporaryFile(suffix='.egg') as T:
    with write_model(T.name, comment=comment,
                     coordinate_system=coordinate_system) as f:
      for node in egg_nodes:
        print(node, file=f)
    return loader.loadModel(T.name)

class EggNode(Eggable):
  ''' A representation of a basic EGG syntactic node with an explicit type.
  '''

  @pfx
  @typechecked
  def __init__(
      self, typename: str, name: Optional[str], contents: Iterable, **kw
  ):
    assert not isinstance(contents, str)  # str is iterable :-(
    self.typename = typename
    self.name = name
    self.contents = contents

  def egg_type(self):
    return self.typename

  def egg_contents(self):
    return self.contents

@dataclass
class Normal(DCEggable):
  x: float
  y: float
  z: float

@dataclass
class UV(DCEggable):
  u: float
  v: float

@dataclass
class RGBA(DCEggable):
  r: float
  g: float
  b: float
  a: float = 1.0

@dataclass
class Vertex(DCEggable):
  x: float
  y: float
  z: float
  w: Optional[float] = None
  attrs: Mapping = dataclass_field(default_factory=dict)

class VertexPool(Eggable):
  ''' A subclass of `list` containing vertices.
  '''

  @typechecked
  def __init__(self, name: str, vertices: Iterable, *, _registry=None):
    self.name = name
    self.vertices = list(vertices)
    self.register(registry=_registry)

  def __len__(self):
    return len(self.vertices)

  def __iter__(self):
    return (
        EggNode(v.egg_type(), str(i), v.egg_contents())
        for i, v in enumerate(self.vertices, 1)
    )

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
    return self.texture_image, *map(
        lambda kv: EggNode('Scalar', kv[0], [kv[1]]), self.attrs.items()
    )

class Polygon(Eggable):

  @typechecked
  def __init__(
      self,
      name: str,
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
      indices = list(range(len(vpool)))
    self.name = name
    self.vpool = vpool
    self.name = name
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

class Group(Eggable):

  def __init__(self, name: Optional[str], *a):
    self.name = name
    self.items = list(a)

  def egg_contents(self):
    return self.items

class Model(ContextManagerMixin):

  REFTYPES = {
      'TRef': Texture,
  }

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
    item.register(self._registry)
    self.items.append(item)

  def check_eggable(self, item):
    ''' Check an `Eggable` `item` for consistency.
    '''
    typename = item.egg_type()
    name = item.egg_name()
    with Pfx("%s.check_item(<%s>%s)", self.__class__.__name__, typename, name
             or ""):
      if name is not None:
        # check the reference in things like <TRef> { texture-name }
        try:
          ref_type = self.REFTYPES[item.egg_type()]
        except KeyError:
          pass
        else:
          ref_name, = item.egg_contents()
          X("    look up %r in %s", ref_name, ref_type)
          assert isinstance(ref_name, str)
          pfx_call(ref_type.instance, ref_name)
      # check all the contained items
      for subitem in item.egg_contents():
        if isinstance(subitem, Eggable):
          self.check_eggable(subitem)

  @pfx_method
  def check(self):
    ''' Check the model for consistency.
    '''
    for item in self.items:
      self.check_eggable(item)

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
