#!/usr/bin/env python3

from dataclasses import dataclass, field
from math import pi, sin, cos
from random import randint
from typing import Any, Callable, Hashable, List, Mapping, Optional, Tuple

from icontract import require
import numpy as np
from typeguard import typechecked

from .egg import Group, Model, Polygon, RGBA, Texture, Vertex, VertexPool

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

def sphere_coords(longitude: float,
                  latitude: float,
                  radius: float = 1.0) -> Tuple[float, float, float]:
  ''' Return the coordinates of a point on the surphace of a sphere
  in world coordinates.
  '''
  lat_cos = cos(latitude)
  xyz = (
      sin(longitude) * lat_cos * radius,
      cos(longitude) * lat_cos * radius,
      sin(latitude) * radius,
  )
  X("    sphere coords(%f,%f) => %r", longitude, latitude, xyz)
  return xyz

@typechecked
@require(lambda radius: radius > 0)
def sphere(
    radius: float = 1.0, steps: int = 8, *, texture: Texture
) -> Surface:
  ''' Lay out texture onto a sphere where the texture is a Mercator
      projection.
  '''
  surface = Surface(f'sphere({radius:f}x{steps:d})')
  vertex_fn = lambda i, longitude, j, latitude: Vertex(
      *sphere_coords(longitude, latitude, radius),
      attrs=dict(
          ##RGBA=RGBA.random(),
          UV=(i / steps / 2, j / steps),
      )
  )
  longitudes = np.linspace(0, 2 * pi, num=steps * 2 + 1)
  X("longitudes = %r", longitudes)
  latitudes = np.linspace(-pi / 2, pi / 2, num=steps + 1)
  X("latitudes = %r", latitudes)
  for i, longitude in enumerate(longitudes[:-1]):
    long2 = longitudes[i + 1]
    X("long %f..%f", longitude, long2)
    for j, latitude in enumerate(latitudes[:-1]):
      lat2 = latitudes[j + 1]
      X("  lat %f..%f", latitude, lat2)
      if j == 0:
        # triangle from the pole
        vs = (
            vertex_fn(i, longitude, j, latitude),
            vertex_fn(i, longitude, j + 1, lat2),
            vertex_fn(i + 1, long2, j + 1, lat2),
        )
      elif j == steps * 2:
        # triangle to the pole
        vs = (
            vertex_fn(i, longitude, j, latitude),
            vertex_fn(i, longitude, j + 1, lat2),
            vertex_fn(i + 1, long2, j + 1, lat2),
        )
      else:
        # rectangle
        vs = (
            vertex_fn(i, longitude, j, latitude),
            vertex_fn(i, longitude, j + 1, lat2),
            vertex_fn(i + 1, long2, j + 1, lat2),
            vertex_fn(i + 1, long2, j, latitude),
        )
      surface.add_polygon(
          *vs,
          ##RGBA=RGBA.random(),
          Texture=texture,
      )
  return surface
