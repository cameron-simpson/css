#!/usr/bin/env python3

''' Hooks for Blender interoparability.
'''

import bpy
from bpy.types import Light, Object
from icontract import require
from typeguard import typechecked

from cs.lex import r
from cs.logutils import setup_logging, warning
from cs.pfx import Pfx

from .egg import (
    Eggable,
    EggRegistry,
    PointLight,
    Vertex as P3DVertex,
    VertexPool,
    uses_vpool,
)
from .geom import Surface

from cs.debug import X, trace

@typechecked
def as_Eggable(blobj: Object) -> Eggable:
  ''' Return an `Eggable` proxy for the Blender object `blobj`.
  '''
  X("blobj.name=%r", blobj.name)
  X("blobj.type=%r", blobj.type)
  X("blobj.data:%r", type(blobj.data))
  if blobj.type == 'LIGHT':
    return LightProxy(blobj)
  if blobj.type == 'MESH':
    mesh = blobj.data
    X("  vertices = %r", list(mesh.vertices))
    X("  edges = %r", list(mesh.edges))
    X("  loops = %r", list(mesh.loops))
    X("  polygons = %r", list(mesh.polygons))
    return MeshProxy(blobj)
  raise TypeError(f'cannot proxy as an Eggable: {r(blobj)}')

class BlenderObjectProxy(Eggable):
  ''' A base class for an `Eggable` proxy for a Blender `Object`.
  '''

  def __init__(self, blobj):
    self.__dict__.update(blender_object=blobj)

  def __getattr__(self, attr):
    ''' Fetch unknown attributes from the proxied object. '''
    X(
        "%s.__getattr__(%r): indirect through self.blender_object:%r",
        type(self), attr, type(self.blender_object)
    )
    return getattr(self.blender_object, attr)

  def __setattr__(self, attr: str, value):
    ''' Set attributes on `self.__dict__` or `self.blender_object` if already present. '''
    if attr in self.__dict__:
      self.__dict__[attr] = value
    elif hasattr(self.blender_object, attr):
      setattr(self.blender_object, attr, value)
    else:
      raise AttributeError(
          f'{self.__class__.__name__}.{attr}: no such attribute on self or self.blender_object:{self.blender_object.__class__.__name__}'
      )

  def egg_name(self):
    ''' The default Egg name comes from `self.name`.
    '''
    return self.name

  def egg_type(self):
    ''' The default Egg type comes from the class name of `self.egg_cls`.
    '''
    return self.egg_cls.__name__

class LightProxy(BlenderObjectProxy):
  ''' A proxy for a Blender light.
  '''

  @require(lambda blobj: blobj.type == 'LIGHT')
  @require(lambda blobj: blobj.data.type in ('POINT',))
  @typechecked
  def __init__(self, blobj: Object):
    assert blobj.type == 'LIGHT'
    super().__init__(blobj)
    datatype = blobj.data.type
    if datatype == 'POINT':
      self.__dict__.update(egg_cls=PointLight)
    else:
      raise ValueError(
          '{self.__class__.__name__}: unsupported data.type {datatype!r} on Blender object {r(blobj)}'
      )

  @uses_vpool
  @typechecked
  def egg_contents(self, *, vpool: VertexPool):
    ''' Return the Egg contents by constructing a suitable light object.
    '''
    light = self.egg_cls(self.name, vpool=vpool)
    return light.egg_contents()

class MeshProxy(BlenderObjectProxy):
  ''' A proxy for a Blender mesh.
  '''

  @require(lambda blobj: blobj.type == 'MESH')
  @typechecked
  def __init__(self, blobj: Object):
    super().__init__(blobj)
    self.__dict__.update(egg_cls=Surface)

  @typechecked
  def egg_contents(self):
    ''' Return the Egg contents by constructing a `cs.panda3d_utils.geom.Surface`.
    '''
    surface = self.as_Surface()
    return surface.as_Eggable().egg_contents()

  @uses_vpool
  @typechecked
  def as_Surface(self, *, vpool: VertexPool) -> Surface:
    ''' Return a `cs.panda3d_utils.geom.Surface` representing the mesh.
    '''
    mesh = self.blender_object.data
    surface = Surface(self.name, vpool=vpool)
    # store all the vertices
    sindex_by_vindex = {}
    for v in mesh.vertices:
      sindex_by_vindex[v.index] = vpool.vertex_index(P3DVertex(*v.co))
    # store the polygons
    for p in mesh.polygons:
      surface.add_polygon(*[sindex_by_vindex[vindex] for vindex in p.vertices])
    return surface

if __name__ == '__main__':
  setup_logging()
  with VertexPool('__main__') as vpool:
    ##light = bpy.data.scenes[0].objects[1]
    ##proxy = as_Eggable(light)
    for blobj in bpy.data.scenes[0].objects:
      with Pfx(blobj):
        try:
          eggable = as_Eggable(blobj)
        except TypeError as e:
          warning("SKIP, cannot represent in Egg syntax: %s", e)
        else:
          print(as_Eggable(blobj))
