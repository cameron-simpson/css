#!/usr/bin/env python3

import sys

from cs.cmdutils import BaseCommand
from cs.progress import progressbar

from .material import Material

class M3Command(BaseCommand):
  ''' Demo command for cs.m3.
  '''

  def main(self, argv):
    M = Material(10, 10, 3, mass=1.0)
    ##print(M.px)
    print(M.px[0, 0, 0])
    for t in progressbar(range(100), "step"):
      M.step()
    model = M.EggModel()
    print(model)
    model.save('model-material.egg', exists_ok=True)

if __name__ == '__main__':
  sys.exit(M3Command(sys.argv).run())
