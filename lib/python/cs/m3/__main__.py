#!/usr/bin/env python3

import os
from os.path import basename
import sys

from cs.cmdutils import BaseCommand
from cs.panda3d_utils.egg import Texture
from cs.progress import progressbar

from .material import Material

class M3Command(BaseCommand):
  ''' Demo command for cs.m3.
  '''

  def main(self, argv):
    xy_s, texture_path = argv
    xy = int(xy_s)
    M = Material(xy, xy, 2, mass=1.0)
    for t in progressbar(range(100), "step"):
      M.step()
    model = M.EggModel(texture=Texture(basename(texture_path), texture_path))
    print(model)
    model.save('model-material.egg', exists_ok=True)
    os.system('pview model-material.egg')

if __name__ == '__main__':
  sys.exit(M3Command(sys.argv).run())
