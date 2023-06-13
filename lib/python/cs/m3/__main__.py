#!/usr/bin/env python3

from contextlib import contextmanager
import os
from os.path import basename, expanduser
import sys

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.panda3d_utils.egg import (
    Model,
    Material,
    PointLight,
    RGBA,
    Texture,
    Transform,
    Translate,
)
from cs.panda3d_utils.geom import equilateral_pyramid, pyramid, sphere, torus
from cs.progress import progressbar

from .material import Material as M3Material

class M3Command(BaseCommand):
  ''' Demo command for cs.m3.
  '''

  @contextmanager
  def run_context(self):
    with super().run_context():
      texture_path = expanduser('~/im/them/me/gravatar-crack-128.png')
      texture = Texture(basename(texture_path), texture_path)
      material = Material(
          name="test-mat",
          diffr=100,
          diffg=100,
          diffb=100,
          diffa=100,
          emitr=0,
          emitg=0,
          emitb=100,
          ##ambr=40,
          ##ambg=0,
          ##ambb=0,
          ##amba=0.5,
          specr=0.5,
          specg=50,
          specb=0.5,
          shininess=100,
      )
      with Model(self.cmd) as model:
        model.append(material)
        model.append(texture)
        options = self.options
        with stackattrs(self.options, model=model, texture_path=texture_path,
                        texture=texture, material=material):
          yield
        print(model)
        model.view(lighting=True)

  def cmd_material(self, argv):
    ''' Usage: {cmd} scale
          Render a `Material` of size scale x scale x 2.
    '''
    xy_s, = argv
    xy = int(xy_s)
    options = self.options
    model = options.model
    M = M3Material(xy, xy, 2, mass=1.0)
    for t in progressbar(range(100), "step"):
      M.step()
    M.EggModel(model=model, texture=options.texture)

  def cmd_sol(self, argv):
    ''' Usage: {cmd} scale
          Render the sun and the earth at the specified scale.
    '''
    xy_s, = argv
    xy = int(xy_s)
    options = self.options
    model = options.model
    sol = sphere(xy * 8, 20, material=options.material)
    model.append(sol.EggNode())
    terra = sphere(xy, 20, texture=options.texture)
    tnode = terra.EggNode(translate=(100, 0, 0))
    model.append(tnode)

  def cmd_pyramid(self, argv):
    ''' Usage: {cmd} scale
          Render a pyramid of the specified scale.
    '''
    xy_s, = argv
    xy = int(xy_s)
    options = self.options
    model = options.model
    ##surface = pyramid(3, xy, texture=texture)
    surface = equilateral_pyramid(
        3,
        xy,
        ##material=options.material,
        texture=options.texture,
    )
    model.append(surface.EggNode())

  def cmd_sphere(self, argv):
    xy_s, = argv
    xy = int(xy_s)
    options = self.options
    model = options.model
    texture = options.texture
    surface = sphere(xy, 40, texture=texture)
    model.append(surface.EggNode())

  def cmd_torus(self, argv):
    ''' Usage: {cmd} radius1 radius2'''
    radius1_s, radius2_s = argv
    radius1 = float(radius1_s)
    radius2 = float(radius2_s)
    options = self.options
    model = options.model
    material = options.material
    texture = options.texture
    surface = torus(
        radius1,
        radius2,
        steps1=12,
        steps2=12,
        material=material,
        ##texture=texture,
    )
    model.append(surface.EggNode())
    model.append(
        PointLight(
            "light0",
            surface.vpool,
            (100, 100, 100),
            thick=10,
            RGBA=RGBA(300, 0, 0),
        )
    )

if __name__ == '__main__':
  sys.exit(M3Command(sys.argv).run())
