#!/usr/bin/env python3

import os
from os.path import basename
import sys

from cs.cmdutils import BaseCommand
from cs.panda3d_utils.egg import Model, Material, Texture
from cs.panda3d_utils.geom import equilateral_pyramid, pyramid, sphere
from cs.progress import progressbar

from .material import Material as M3Material

class M3Command(BaseCommand):
  ''' Demo command for cs.m3.
  '''

  def main(self, argv):
    mode = 'pyramid'
    xy_s, texture_path = argv
    xy = int(xy_s)
    texture = Texture(basename(texture_path), texture_path)
    material = Material(
        name="test-mat",
        emitr=0,
        emitg=100,
        emitb=50,
        ##ambr=40,
    )
    model = Model(mode)
    with model:
      model.append(material)
      model.append(texture)
      if mode == 'pyramid':
        ##surface = pyramid(3, xy, texture=texture)
        surface = equilateral_pyramid(
            3,
            xy,
            material=material,
            ##texture=texture,
        )
        model.append(surface.EggNode())
      elif mode == 'sphere':
        surface = sphere(xy, 40, texture=texture)
        model.append(surface.EggNode())
      else:
        M = M3Material(xy, xy, 2, mass=1.0)
        for t in progressbar(range(100), "step"):
          M.step()
        M.EggModel(model=model, texture=texture)
    print(model)
    model.view(lighting=True)

if __name__ == '__main__':
  sys.exit(M3Command(sys.argv).run())
