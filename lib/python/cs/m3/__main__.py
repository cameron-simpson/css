#!/usr/bin/env python3

from contextlib import contextmanager
import os
from os.path import basename, expanduser
import sys

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.panda3d_utils.egg import Model, Material, Texture, Transform, Translate
from cs.panda3d_utils.geom import equilateral_pyramid, pyramid, sphere
from cs.progress import progressbar

from .material import Material as M3Material

class M3Command(BaseCommand):
  ''' Demo command for cs.m3.
  '''

  @contextmanager
  def run_context(self):
    texture_path = expanduser('~/im/them/me/gravatar-crack-128.png')
    texture = Texture(basename(texture_path), texture_path)
    material = Material(
        name="test-mat",
        emitr=0,
        emitg=100,
        emitb=50,
        #ambr=40,
        #ambg=0,
        #ambb=0,
        shininess=5,
        specr=0.5,
        specg=50,
        specb=0.5,
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
    xy_s, = argv
    xy = int(xy_s)
    options = self.options
    model = options.model
    M = M3Material(xy, xy, 2, mass=1.0)
    for t in progressbar(range(100), "step"):
      M.step()
    M.EggModel(model=model)

  def cmd_sol(self, argv):
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
    xy_s, = argv
    xy = int(xy_s)
    options = self.options
    model = options.model
    ##surface = pyramid(3, xy, texture=texture)
    surface = equilateral_pyramid(
        3,
        xy,
        material=options.material,
        ##texture=options.texture,
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

if __name__ == '__main__':
  sys.exit(M3Command(sys.argv).run())
