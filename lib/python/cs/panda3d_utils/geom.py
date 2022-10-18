#!/usr/bin/env python3

from dataclasses import dataclass, field
from typing import Any, Callable, Hashable, List, Mapping, Optional

from typeguard import typechecked

from .egg import Group, Polygon, Vertex, VertexPool

class Surface:
  ''' A surface definition consisting of a list of `Polygon`s
      and an associated `VertexPool`.
  '''

  @typechecked
  def __init__(self, name, vpool: Optional[VertexPool] = None):
    if vpool is None:
      vpool = VertexPool(name, [])
    self.name = name
    self.vpool = vpool
    self.polygons = []

  def add_polygon(self, *vertices, **polygon_attrs):
    ''' Create and add a new `Polygon` to the surfac.
    '''
    vifn = self.vpool.vertex_index
    vindices = [vifn(v) for v in vertices]
    self.polygons.append(Polygon(None, self.vpool, *vindices, **polygon_attrs))

  def EggNode(self):
    ''' Return a `<Group>` defining this `Surface`.
    '''
    return Group(self.name, self.vpool, *self.polygons)
