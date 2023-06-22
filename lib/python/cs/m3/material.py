#!/usr/bin/python3

from itertools import chain, product
from math import nan, sqrt
from os.path import expanduser
from pprint import pformat, pprint
import string
import sys
from types import SimpleNamespace as NS
from typing import Optional, Tuple

from cs.lex import r, s
from cs.logutils import warning
from cs.mappings import AttrableMapping
from cs.panda3d_utils.egg import (
    Group,
    Model,
    Polygon,
    RGBA,
    Texture,
    Vertex,
    VertexPool,
)
from cs.panda3d_utils.geom import Surface
from cs.pfx import Pfx, pfx_call
from cs.progress import progressbar
from cs.resources import uses_runstate
from cs.result import CancellationError

from icontract import require
import numpy as np
import pandas as pd
from typeguard import typechecked

def grid(*dimensions):
  ''' Return a list of n-tuples sized by `dimensions`
      numbered from 0.0 through to `size-1`
      for each `size` in `dimensions`.

      Example:

          >>> pprint(grid(2,3,4))
          [(0.0, 0.0, 0.0),
           (0.0, 0.0, 1.0),
           (0.0, 0.0, 2.0),
           (0.0, 0.0, 3.0),
           (0.0, 1.0, 0.0),
           (0.0, 1.0, 1.0),
           (0.0, 1.0, 2.0),
           (0.0, 1.0, 3.0),
           (0.0, 2.0, 0.0),
           (0.0, 2.0, 1.0),
           (0.0, 2.0, 2.0),
           (0.0, 2.0, 3.0),
           (1.0, 0.0, 0.0),
           (1.0, 0.0, 1.0),
           (1.0, 0.0, 2.0),
           (1.0, 0.0, 3.0),
           (1.0, 1.0, 0.0),
           (1.0, 1.0, 1.0),
           (1.0, 1.0, 2.0),
           (1.0, 1.0, 3.0),
           (1.0, 2.0, 0.0),
           (1.0, 2.0, 1.0),
           (1.0, 2.0, 2.0),
           (1.0, 2.0, 3.0)]
  '''
  return list(product(*[map(float, range(d)) for d in dimensions]))

def grid_as_np_array(*dimensions, _labels=None, **properties):
  ''' Return a Numpy array of Python objects,
      each being a `SimpleNamespace` with labelled spatial coordinates
      using labels from `_labels`
      and addtional attributes from `properties`.

      The default labels are the trailing letters from `string.ascii_lowercase`.

      Example:

          >>> grid_as_np_array(2, 3, 4, mass=0.0, vx=0.0, vy=0.0, vz=0.0)
          array([[[namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=0.0, y=0.0, z=0.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=0.0, y=0.0, z=1.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=0.0, y=0.0, z=2.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=0.0, y=0.0, z=3.0)],
                  [namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=0.0, y=1.0, z=0.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=0.0, y=1.0, z=1.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=0.0, y=1.0, z=2.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=0.0, y=1.0, z=3.0)],
                  [namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=0.0, y=2.0, z=0.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=0.0, y=2.0, z=1.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=0.0, y=2.0, z=2.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=0.0, y=2.0, z=3.0)]],
          <BLANKLINE>
                 [[namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=1.0, y=0.0, z=0.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=1.0, y=0.0, z=1.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=1.0, y=0.0, z=2.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=1.0, y=0.0, z=3.0)],
                  [namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=1.0, y=1.0, z=0.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=1.0, y=1.0, z=1.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=1.0, y=1.0, z=2.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=1.0, y=1.0, z=3.0)],
                  [namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=1.0, y=2.0, z=0.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=1.0, y=2.0, z=1.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=1.0, y=2.0, z=2.0),
                   namespace(mass=0.0, vx=0.0, vy=0.0, vz=0.0, x=1.0, y=2.0, z=3.0)]]],
                dtype=object)
  '''
  if _labels is None:
    _labels = string.ascii_lowercase[-len(dimensions):]
  assert len(_labels) == len(dimensions)
  ary = np.array(
      [
          NS(**dict(chain(
              zip(_labels, coords),
              properties.items(),
          ))) for coords in grid(*dimensions)
      ]
  )
  return ary.reshape(*dimensions)

def grid_as_dict(*dimensions, _labels=None, **properties):
  ''' Prepare a `dict` mapping various names to `ndarray` instances
      where the names are coordinate names from `_labels` or property
      names from `properties`.
      The array shape comes from `dimensions`.
  '''
  if _labels is None:
    _labels = string.ascii_lowercase[-len(dimensions):]
  assert len(_labels) == len(dimensions)
  d = {}
  # prepare the n-space coordinate arrays
  g = grid(*dimensions)
  for i, ax_name in enumerate(_labels):
    assert ax_name not in d
    d[ax_name] = np.array([p[i] for p in g]).reshape(dimensions)
  # prepare the property arrays
  for pname, pvalue in properties.items():
    assert pname not in d
    d[pname] = np.array([pvalue] * len(g)).reshape(dimensions)
  return d

class Material:
  ''' An n-dimensional material composed of a rectilinear grid of particles.

      Unlike `grid_as_np_array`, which is an n-dimensional `ndarray`
      of Python objects with individual properties, this implementation
      keeps an `ndarray` of `float`s for each property in order to
      perform vector operations.

      A `Material` has the following attributes:
      * `shape`: the number of particles in each spatial direction
      * `nparticles`: the number of particles, also available as `__len__`
      * `labels`: a list of labels for each spatial dimension.
      * `ordinates`: the spatial coordinates of each particle.
      * `properties`: a mapping of property name to a n-dimensional
        array of values for the property corresponding to each particle.
        These might represent values such as charge or mass.

      A default `inertial_mass=1.0` is provided to support response to forces.

      When choosing property names, keep in mind that for each axis
      `label` there are `f'p{label}'` and `f'v{label}'` automatic
      properties representing particle position and velocity,
      prefilled with zeros. If these names are used for properties
      they will overwrite these prefills.
  '''

  @uses_runstate
  @typechecked
  def __init__(
      self,
      *shape: int,
      _labels=None,
      _spacing=1.0,
      _steptime=1.0,
      _stiffness=1.0,
      runstate,
      **properties,
  ):
    ''' Initialise the `Material`.

        The positional parameters should be positive integers
        defining the `shape` of the material as a rectilinear grid
        of particles.

        The grid defines 2 things:
        the topological relationship of the particles,
        used when computing forces between "adjecaent" particles
        or when deriving "surfaces",
        and the _initial_ spacial locations of the particles.
        The topological relation presists under deformation.

        The keyword parameters can be used to define various particle
        properties to be applied uniformly to every particle.

        Tuning keyword parameters therefore commence with an
        underscore as follows:
        * `_labels`: an optional iterable of names for each spacial axis,
          presented later as a list in the `Material.labels` attribute.
          The default `_labels` are taken from the end of
          `string.ascii_lowercase` so that a 3-space `Material` would
          have the labels `'x','y','z'`.
        * `_spacing`: the initial spacing between particles, default `1.0`
        * `_steptime`: the default iteration time step as the material evolves
        * `_stiffness`: the stiffness of the material, used to
          provide resistance when particles are displaced from their
          default spacing
        * `runstate`: optional `RunState` to cancel the init,
          which can take a long time
    '''
    assert len(shape) >= 1
    assert all(map(lambda dim: dim > 0, shape))
    under_props = [
        pname for pname in properties.keys() if pname.startswith('_')
    ]
    assert not under_props, "unexpected _underscored parameters: %r" % under_props
    if _labels is None:
      _labels = string.ascii_lowercase[-len(shape):]
    if not isinstance(_labels, list):
      _labels = list(_labels)
    if isinstance(_spacing, (int, float)):
      _spacing = [float(_spacing)] * len(shape)
    else:
      _spacing = list(_spacing)
      assert len(_spacing) == len(shape)
    self.spacing = _spacing
    self.steptime = _steptime
    if isinstance(_stiffness, (int, float)):
      _stiffness = [float(_stiffness)] * len(shape)
    else:
      _stiffness = list(_stiffness)
      assert len(_stiffness) == len(shape)
    self.stiffness = _stiffness
    # dimension labels must match the shape
    assert len(_labels) == len(shape)
    # labels must be unique
    assert len(_labels) == len(set(_labels))
    properties.setdefault('inertial_mass', 1.0)
    self.shape = shape
    nparticles = self.nparticles
    self.labels = list(_labels)
    data = {}
    # coordinates - initially all zero for position and velocity
    for i, label in enumerate(_labels):
      if runstate and runstate.cancelled:
        raise CancellationError
      with Pfx("i=%d, df[%r]", i, label):
        data[f'p{label}'] = np.zeros(shape)
        data[f'v{label}'] = np.zeros(shape)
    # fill in the positions
    plabels = [f'p{label}' for label in _labels]
    for coords in product(*[np.linspace(
        0.0,
        _spacing[i] * (dim - 1),
        dim,
        dtype=int,
    ) for i, dim in enumerate(shape)]):
      if runstate and runstate.cancelled:
        raise CancellationError
      for plabel, coord in zip(plabels, coords):
        data[plabel][coords] = coord
    self.data = data
    for pname, pvalue in properties.items():
      if runstate and runstate.cancelled:
        raise CancellationError
      data[pname] = np.full(shape, pvalue)

  def __str__(self):
    return f'{self.__class__.__name__}(*{self.shape})'

  @property
  def nparticles(self):
    ''' The number of particles i.e. the product of the shape.
    '''
    n = 1
    for d in self.shape:
      n *= d
    return n

  def __getitem__(self, column):
    return self.data[column]

  def __getattr__(self, attr):
    try:
      return self[attr]
    except KeyError:
      raise AttributeError(
          "%s.%s: unknown attribute" % (self.__class__.__name__, attr)
      )

  def axial_force(self, axis):
    ''' Compute the forces in the material along `axis`, returns a
        `DataFrame` with the net force on each particle along that axis.
    '''
    # TODO: direct sideways diffs provide no tortional resistance
    #  because they only measure displacement along that line; torsion
    #  requires diffing the adjacent n-cube
    # TODO: fields such as gravity
    # TODO: callables to compute forces?
    # TODO: maybe fields derived from point sources can serve as
    #  proxies for volumes of remote particles, eg centre of mass or
    #  charge etc
    # TODO: charged properties? can they supplant stiffness?
    # TODO: damping?
    # TODO: torsional effects from deformation instead of stretch
    # TODO: maybe there are no torsional effects, instead torsion
    #   emerges from forcing neighbours closer together
    data = self.data
    label = self.labels[axis]
    ##print("axis,label", axis, label)
    stiffness = self.stiffness[axis]
    ##print("stiffness =", stiffness)
    diff_left = np.diff(data[f'p{label}'], axis=axis, prepend=nan)
    ##print("diff_left =", diff_left)
    f_left = (-1.0 / diff_left + 1.0) * stiffness
    ##print("f_left =", f_left)
    diff_right = -np.diff(data[f'p{label}'], axis=axis, append=nan)
    f_right = (-1.0 / diff_right - 1.0) * stiffness
    ##print("f_right =", f_right)
    f = np.nan_to_num(f_left) + np.nan_to_num(f_right)
    ##print("axis,label", axis, label, "f =", f)
    return f

  def step(self, dt=None):
    ''' Advance the positions and velocity of the particles
        in a time step of `dt`, default from `self.steptime`.
    '''
    if dt is None:
      dt = self.steptime
    data = self.data
    inertia = data['inertial_mass']
    for axis, label in enumerate(self.labels):
      f = self.axial_force(axis)
      # move the particles with their current velocities
      data[f'p{label}'] += data[f'v{label}'] * dt
      # update the velocities in response to forces
      data[f'v{label}'] += f / inertia * dt

  @typechecked
  def Surface(
      self,
      slice_dim: int,
      slice_index: int,
      *,
      clockwise: Optional[bool] = None,
      texture: Texture,
  ):
    ''' Create a `Surface` by slicing the `Material` along the
        dimension `slice_dim` at index `slice_index` using `Polygon`s
        of the specified `clockwise`ness.
    '''
    # infer a default clockwiseness
    # we got widddershins at index 0, clockwise otherwise
    # except for the Y-axis whose direction means we reverse this
    if clockwise is None:
      clockwise = slice_index > 0
      if slice_dim == 1:
        # the Y axis goes positive away from the viewer, reverse the polarity
        clockwise = not clockwise
    # deduce the dimensions comprising the surface
    # i.e. those which are not the slice dimension
    dims = 0, 1, 2
    abdims = [dim for dim in dims if dim != slice_dim]
    a_dim = abdims[0]
    b_dim = abdims[1]
    shape = self.shape
    a_len = shape[a_dim] - 1
    b_len = shape[b_dim] - 1
    # dimension labels in surface selection order
    labels = self.labels
    alabel = self.labels[a_dim]
    blabel = self.labels[b_dim]
    clabel = self.labels[slice_dim]
    # dimension spacial position labels for x,y,z
    labelx, labely, labelz, *_ = labels
    data = self.data
    datax = data[f'p{labelx}']
    datay = data[f'p{labely}']
    dataz = data[f'p{labelz}']
    # function to compute a Series index from a, b, c
    if slice_dim == 0:
      abc_fn = lambda a, b, c: (c, a, b)
    elif slice_dim == 1:
      abc_fn = lambda a, b, c: (a, c, b)
    elif slice_dim == 2:
      abc_fn = lambda a, b, c: (a, b, c)
    else:
      raise RuntimeError("unhandled slice_dim %s" % (r(slice_dim),))
    c = slice_index
    surface = Surface(f'surface_{alabel}_{blabel}_{clabel}{c}')
    # enumerate all the polygons
    for a, b in product(range(a_len), range(b_len)):
      vertices = []
      for da, db in (((0, 0), (1, 0), (1, 1), (0, 1)) if clockwise else
                     ((0, 0), (0, 1), (1, 1), (1, 0))):
        ##print("da =", da, "db =", db)
        va = a + da
        vb = b + db
        abc_index = abc_fn(va, vb, c)
        vertices.append(
            Vertex(
                datax[abc_index],
                datay[abc_index],
                dataz[abc_index],
                attrs=dict(UV=(va / a_len, vb / b_len))
            )
        )
      surface.add_polygon(*vertices, Texture=texture)
    return surface

  @uses_runstate
  @typechecked
  def EggModel(self, *, model=None, runstate, texture: Texture) -> Model:
    ''' Return a `cs.panda3dutils.egg.Model` derived from this material.
    '''
    rgba = RGBA(1, 1, 1, 1)
    if model is None:
      mode = Model(str(self))
    with model:
      model.append(texture)
      data = self.data
      labels = self.labels
      shape = self.shape
      spacing = self.spacing
      # generate surfaces for each surface of the material
      # requires at least 3 dimensions
      dims = 0, 1, 2
      labelx, labely, labelz, *_ = labels
      # the Serieses for positional data
      datax = data[f'p{labelx}']
      datay = data[f'p{labely}']
      dataz = data[f'p{labelz}']
      # iterate over the dimensions orthogonal to the surface
      for skip_dim in dims:
        for c in 0, shape[skip_dim] - 1:
          if runstate and runstate.cancelled:
            return None
          surface = self.Surface(skip_dim, c, texture=texture)
          model.append(surface)
    return model
