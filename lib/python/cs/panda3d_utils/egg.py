#!/usr/bin/env python3

''' Panda3d EGG format.

    Because Panda3d seems to load things from `.egg` files
    and some other compiled formats
    this module is aimed at writing Egg files.
    As such it contains functions and classes for making
    entities found in Egg files, and for writing these out in Egg syntax.
    The entities are _not_ directly useable by Pada3d itself,
    they get into panda3d by being written as Egg and loaded.

    The following are provided:
    * `egg_str`: return a `str` with the Egg syntax transcription of an object
    * `quote(str)`: return a string quoted correctly for an Egg file
    * `dump`,`dumps`,dumpz`: after the style of `json.dump`, functions
      to dump objects in egg syntax
    * `EggNode`: an Egg syntactic object
    * `Eggable`: a mixin for objects which can be made into `EggNode`s
    * various factories and classes for Egg nodes: `Texture`,
      `Vertex` and so forth
'''

from collections import namedtuple
from typing import Any, Iterable, Mapping, Optional, Tuple, Union

from typeguard import typechecked

from cs.lex import is_identifier, r
from cs.mappings import StrKeyedDict
from cs.numeric import intif
from cs.pfx import Pfx, pfx

@pfx
def quote(text):
  ''' Quote a piece of text for inclusion in an EGG file.
  '''
  return (
      text if is_identifier(text.replace('-', '_')) else
      ('"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"')
  )

def egg_str(item, indent=""):
  ''' Present `item` in EGG syntax.
  '''
  if isinstance(item, EggNode):
    return item.transcribe(indent)
  if isinstance(item, Eggable):
    return item.EggNode().transcribe(indent)
  if isinstance(item, str):
    return quote(item)
  if isinstance(item, (int, float)):
    return str(intif(item))
  raise TypeError("unhandled type for item %s" % (r(item),))

class EggNode(namedtuple('EggNode', 'typename name contents')):
  ''' A representation of an EGG syntactic node.
  '''

  @pfx
  @typechecked
  def __new__(cls, typename: str, name: Optional[str], contents: Iterable):
    return super().__new__(cls, typename, name, contents)

  def __str__(self):
    return self.transcribe()

  def transcribe(self, indent=''):
    subindent = indent + "  "
    item_strs = []
    if isinstance(self.contents, str):
      contents = (self.contents,)
    else:
      try:
        contents = list(self.contents)
      except TypeError:
        contents = (self.contents,)
    for item in contents:
      with Pfx("%r", item):
        item_strs.append(egg_str(item, subindent))
    content_break = "\n" + subindent
    content_parts = []
    had_break = False
    had_breaks = False
    for item, item_s in zip(contents, item_strs):
      if had_break or item_s.endswith('}') or '\n' in item_s:
        # a line break before and after any complex item
        content_parts.append(content_break)
        had_break = True
        had_breaks = True
      else:
        # otherwise write things out on one line
        content_parts.append(" ")
        had_break = False
      content_parts.append(item_s)
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
    content_part = "".join(content_parts)
    return (
        f'<{self.typename}> {{{content_part}}}' if self.name is None else
        f'<{self.typename}> {quote(self.name)} {{{content_part}}}'
    )

  def from_obj(obj):
    egg_args = {}[type(obj)]
    return EggNode(*egg_args)

def dumps(obj):
  ''' Return a string containing `obj` in EGG syntax.
  '''
  return str(EggNode.from_obj(obj))

def dump(obj, f):
  ''' Write `obj` to the text file `f` in EGG syntax.
  '''
  if isinstance(f, str):
    with open(f, 'w') as f2:
      dump(obj, f2)
  else:
    f.write(dumps(obj))

def dumpz(obj, f):
  ''' Write `obj` to the binary file `f` in EGG syntax, zlib compressed.
  '''
  if isinstance(f, str):
    with open(f, 'wb') as f2:
      dumpz(obj, f2)
  else:
    f.write(compress(dumps(obj).encode('utf-8')))

class Eggable:
  ''' A mixin to support encoding this object as an `EggNode`.
  '''

  def __str__(self):
    return str(self.EggNode())

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
    ''' The `EggNode` contents, an iterable.
    '''
    return list(iter(self))

  def EggNode(self):
    ''' Return an `EggNode` representing this object.
    '''
    return EggNode(self.egg_type(), self.egg_name(), self.egg_contents())

# a 3 value vector or point
V3 = namedtuple('V3', 'x y z')

class Normal(V3, Eggable):
  ''' The normal to a surface.
  '''

class UV(namedtuple('UV', 'u v'), Eggable):

  def egg_contents(self):
    return self

class RGBA(namedtuple('RGBA', 'r g b a'), Eggable):

  def egg_contents(self):
    return self

class Vertex(Eggable):

  @typechecked
  def __init__(
      self, x: float, y: float, z: float, *, normal: Normal, uv: Tuple[float,
                                                                       float]
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
