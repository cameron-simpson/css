#!/usr/bin/python

from __future__ import print_function
from cs.misc import DictUCAttrs

class Display(DictUCAttrs):
  def __init__(self,display=None):
    dict.__init__(self)
    self.windows={}
    if display is None:
      import os
      display=os.environ['DISPLAY']
    self['DISPLAY']=display

    import cs.sh
    fp=cs.sh.vpopen(("xdpyinfo","-display",display))

    extensions=[]
    screens=[]
    visuals=[]
    screen=None

    self.EXTENSIONS=extensions
    self.SCREENS=screens

    lineno=0
    redo=False
    while True:
      if redo:
        redo=False
      else:
        line=fp.readline()

      if len(line) == 0:
        break

      if line.startswith("number of extensions:"):
        while True:
          if redo:
            redo=False
          else:
            line=fp.readline()

          if len(line) == 0:
            break

          if not line[0].isspace():
            break

          extensions.append(line.strip())

        redo=True
        continue

      if line.startswith("screen #"):
        screennum=int(line[8:-2])
        assert screennum == len(screens)
        screen=DictUCAttrs()
        screens.append(screen)
        visuals=DictUCAttrs()
        screen.VISUALS=visuals
        while True:
          if redo:
            redo=False
          else:
            line=fp.readline()

          if len(line) == 0:
            break

          if not line[0].isspace():
            break

          line=line.lstrip()
          if line.startswith('dimensions:'):
            screen.DX, screen.DY = [ int(d)
                                     for d in line.split()[1].split('x')
                                   ]
          elif line.startswith('resolution:'):
            screen.DPIX, screen.DPIY = [ int(d)
                                         for d in line.split()[1].split('x')
                                       ]
          elif line.startswith('default visual id:'):
            screen.DEFAULT_VISUAL_ID=eval(line.split()[3])
          elif line.startswith("visual"):
            visual=DictUCAttrs()
            while True:
              line=fp.readline()
              if len(line) == 0:
                break

              if not line.startswith('    '):
                break

              line=line.lstrip()
              if line.startswith('visual id:'):
                visual.ID=eval(line.split()[2])
              elif line.startswith('class:'):
                visual.CLASS=line.split()[1]
              elif line.startswith('depth:'):
                visual.DEPTH=int(line.split()[1])

            visuals[visual.ID]=visual

            redo=True
            continue

        redo=True
        continue

  def __getattr__(self,attr):
    if attr.isalpha() and attr.isupper():
      return self[attr]
    raise AttributeError("no attribute named "+attr)

  def window(self,wid):
    if id not in self.windows:
      self.windows[wid]=Window(self,wid)
    return self.windows[wid]

def window(wid,display=None):
  return Display(display=display).window(wid)

class Window(dict):
  def __init__(self,D,wid):
    self.wid=wid
    self.display=D
    self.reload()

  def reload(self):
    import cs.sh
    for line in cs.sh.vpopen(("set-x","xwininfo","-display",self.display['DISPLAY'],"-id",str(self.wid))):
      line=line.lstrip()
      print("W:", line)
      if line.startswith("Absolute upper-left X:"):
        self.x=int(line[22:])
      elif line.startswith("Absolute upper-left Y:"):
        self.y=int(line[22:])
      elif line.startswith("Width:"):
        self.dx=int(line[6:])
      elif line.startswith("Height:"):
        self.dy=int(line[7:])
