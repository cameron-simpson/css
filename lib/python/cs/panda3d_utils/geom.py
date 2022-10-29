#!/usr/bin/env python3

from copy import copy
from dataclasses import dataclass, field
from math import pi, sin, cos, sqrt
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

  @typechecked
  def EggNode(self, transform: Optional[Transform] = None):
    ''' Return a `<Group>` defining this `Surface`.
    '''
    nodes = []
    if transform:
      nodes.append(transform)
    nodes.append(self.vpool)
    nodes.extend(self.polygons)
    return Group(self.name, *nodes)

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
      UV=(i / steps / 2, j / steps),
      ##RGBA=RGBA.random(),
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

@typechecked
@require(lambda sides: sides >= 3)
@require(lambda base_length: base_length > 0.0)
@require(lambda aspect: aspect > 0.0)
def pyramid(
    sides: int,
    base_length: float = 1.0,
    aspect=1.0,
    **poly_attrs,
) -> Surface:
  ''' A pyramid with `sides` faces (excepting the base)
      and a height of `base_length*aspect`.
  '''
  surface = Surface(f'pyramid({sides:d},{base_length:f})')
  height = base_length * aspect
  apex = Vertex(0, 0, height, attrs=dict(UV=(0.5, 1.0)))
  base_angle = 2 * pi / sides
  # base_length=2*(radius*sin(base_angle/2))
  radius = base_length / sin(base_angle / 2) / 2
  corner_angles = np.linspace(0.0, 2 * pi, num=sides + 1)
  corner_vs = [
      Vertex(
          radius * sin(corner_angle),
          radius * cos(corner_angle),
          0.0,
          UV=(0.0, 0.0),
      ) for i, corner_angle in enumerate(corner_angles[:-1])
  ]
  assert len(corner_vs) == sides
  for i, corner_angle in enumerate(corner_angles[:-1]):
    corner_angle2 = corner_angles[(i + 1) % sides]
    v0 = corner_vs[i]
    v1_ = corner_vs[(i + 1) % sides]
    v1 = copy(v1_)
    assert v1 is not v1_
    assert v1.attrs is not v1_.attrs
    v1.attrs.update(UV=(1.0, 0.0))
    surface.add_polygon(apex, v1, v0, **poly_attrs)
  base_vs = [copy(v) for v in corner_vs]
  for i, v in enumerate(base_vs):
    v.UV = (
        (sin(corner_angles[i]) + 1.0) / 2, (cos(corner_angles[i]) + 1.0) / 2
    )
  surface.add_polygon(*base_vs, **poly_attrs)
  return surface

@typechecked
@require(lambda sides: sides >= 3)
@require(lambda base_length: base_length > 0.0)
def equilateral_pyramid(sides: int, base_length: float = 1.0, **poly_attrs):
  ''' A pyramid with `sides` faces (excepting the base)
      whose faces are equilateral triangles.
      With 3 `sides` the base is also an equilateral triangle.
  '''
  # h: height
  # dc: distance from base corner to base centre
  # right angle using rising edge
  # base_length ** 2 = h ** 2 + dc ** 2
  # h ** 2 = base_length ** 2 - dc ** 2
  # dn: distance normal to base edge to base centre
  # right angle using base edge
  # dc ** 2 = (base_length / 2) ** 2 + dn ** 2
  # h = base_length * aspect
  # base corner theta wrt radius
  theta_bc = (pi - (pi * 2 / sides)) / 2
  if theta_bc * 2 > pi / 2:
    raise ValueError("theta_bc * 2 > pi / 2")
  dn = base_length * cos(theta_bc)
  dc = base_length * sin(theta_bc)
  h = sqrt(base_length**2 - dc**2)
  aspect = h / base_length
  return pyramid(sides, base_length, aspect, **poly_attrs)
