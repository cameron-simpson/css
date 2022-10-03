#!/usr/bin/env python3

''' Panda3d Egg format.

    Because Panda3d seems to load things from `.egg` files
    and some other compiled formats
    this module is aimed at writing Egg files.
    As such it contains functions and classes for making
    entities found in Egg files, and for writing these out in Egg syntax.
    The entities are _not_ directly useable by Pada3d itself,
    they get into panda3d by being written as Egg and loaded.

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

from collections import namedtuple
from typing import Any, Iterable, Mapping, Optional, Tuple, Union
from zlib import compress

from typeguard import typechecked

from cs.lex import is_identifier, r
from cs.mappings import StrKeyedDict
from cs.numeric import intif
from cs.pfx import Pfx, pfx

from cs.x import X

@pfx
def quote(text):
  ''' Quote a piece of text for inclusion in an Egg file.
  '''
  return (
      text if is_identifier(text.replace('-', '_')) else
      ('"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"')
  )

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
      assert class_name_lc not in self.egg_classnames_by_lc, \
          "new class %r: %r already maps to %r" % (
          class_name, class_name_lc, self.egg_classnames_by_lc[class_name_lc]
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
    ''' Register this instance in `register` by `self.name`,
        default `EggMetaClass.egg_instances`.
    '''
    assert self.name is not None
    if registry is None:
      registry = EggMetaClass.egg_instances
    instances = registry[id(type(self))]
    assert self.name not in instances
    instances[self.name] = self

  @classmethod
  @typechecked
  def instance(cls, name: str, registry=None):
    ''' Return the instance named `name` from `registry`,
        default `EggMetaClass.egg_instances`.
    '''
    assert name is not None
    if registry is None:
      registry = EggMetaClass.egg_instances
    instances = registry[id(cls)]
    return instances[name]

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

  def egg_contents(self):
    ''' Generator yielding the `EggNode` contents.
        This base implementation yields the contents of `self.attrs` if present.
    '''
    for attr, value in getattr(self, 'attrs', {}).items():
      if value is None:
        continue
      if isinstance(value, (str, int, float)):
        yield EggNode('Scalar', attr, [value])
      elif value.egg_name().lower() != attr.lower():
        warning(
            "iter(%s): value.name does not match attr: attr=%r, value=%s",
            self, attr, r(value)
        )
      else:
        yield value

  def egg_transcribe(self, indent=''):
    ''' A generator yielding `str`s which transcribe `self` in Egg syntax.
    '''
    subindent = indent + "  "
    item_strs = []
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
      if value is not None:
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

class Vertex(Eggable):

  @typechecked
  def __init__(
      self,
      x: float,
      y: float,
      z: float,
      *,
      normal: Normal,
      uv: Tuple[float, float],
  ):
    self.x = x
    self.y = y
    self.z = z
    self.normal = normal
    self.uv = uv

  def egg_contents(self):
    return self.x, self.y, self.z, self.normal, self.uv

# alias Vertex as V
V = Vertex

class NamedVertexPool(dict, Eggable):
  ''' A subclass of `dict` mapping names to vertices.
  '''

  def egg_type(self):
    ''' Return `VertexPool`.
    '''
    return 'VertexPool'

  def egg_contents(self):
    return [
        EggNode(v.egg_type(), k, v.egg_contents()) for k, v in self.items()
    ]

class IndexedVertexPool(list, Eggable):
  ''' A subclass of `list` containing vertices.
  '''

  def egg_type(self):
    ''' Return `VertexPool`.
    '''
    return 'VertexPool'

  def egg_contents(self):
    return [
        EggNode(v.egg_type(), str(i), v.egg_contents())
        for i, v in enumerate(self, 1)
    ]

@typechecked
def VertexPool(name, vertices: Union[Mapping[str, Any], Iterable]):
  ''' Factory returning an `IndexedVertexPool` or a `NamedVertexPool`
      depending on the nature or `vertices`,
      which may be an iterable of vertices or a mapping.
  '''
  try:
    items = vertices.items
  except AttributeError:
    vpool = IndexedVertexPool(vertices)
  else:
    vpool = NamedVertexPool(items())
  vpool.name = name
  return vpool

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
      name: Optional[str],
      *,
      rgba: RGBA,
      tref: Union[str, Texture],
      vertexref,
  ):
    if isinstance(tref, Texture):
      tname = tref.egg_name()
      if tname is None:
        raise ValueError("tref: Texture has no name")
      tref = tname
    self.name = name
    self.rgba = rgba
    self.tref = tref
    self.vertexref = vertexref

  def egg_contents(self):
    return self.rgba, EggNode('TRef', None, [self.tref]), self.vertexref

class Group(list, Eggable):

  def __init__(self, name: Optional[str], *a):
    self.name = name
    super().__init__(a)

  def egg_contents(self):
    return self

if __name__ == '__main__':
  vp = VertexPool(
      "named", {
          'v':
          Vertex(1, 2, 3, normal=Normal(4, 5, 6), uv=UV(6, 7)),
          't':
          Texture("texture1", "texture1.png"),
          'p':
          Polygon(
              "polyname",
              rgba=RGBA(1, 1, 1, 1),
              tref=Texture("foo", "tpath.png"),
              vertexref="vertexref",
          ),
      }
  )
  print(vp)
  print(Group(None, Texture("texture2", "texture2.png")))
