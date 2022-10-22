#!/usr/bin/env python3

import os
from os.path import basename
import sys

from cs.cmdutils import BaseCommand
from cs.panda3d_utils.egg import Model, Texture
from cs.panda3d_utils.geom import sphere
from cs.progress import progressbar

from .material import Material

class M3Command(BaseCommand):
  ''' Demo command for cs.m3.
  '''

  def main(self, argv):
    mode = 'sphere'
    xy_s, texture_path = argv
    xy = int(xy_s)
    texture = Texture(basename(texture_path), texture_path)
    if mode == 'sphere':
      model = Model(mode)
      with model:
        surface = sphere(xy, 40, texture=texture)
        egg = surface.EggNode()
        model.append(texture)
        model.append(surface.EggNode())
    else:
      M = Material(xy, xy, 2, mass=1.0)
      for t in progressbar(range(100), "step"):
        M.step()
      model = M.EggModel(texture=texture)
    print(model)
    model.view()

if __name__ == '__main__':
  sys.exit(M3Command(sys.argv).run())
