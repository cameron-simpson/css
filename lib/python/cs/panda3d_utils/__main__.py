#!/usr/bin/env python3

from contextlib import contextmanager
from getopt import GetoptError
from math import pi, sin, cos
from os.path import abspath
import sys
from tkinter import Tk, Button, Toplevel
from typing import Callable, Optional, Tuple, TypeVar, Union

from cs.cmdutils import BaseCommand
from cs.context import ContextManagerMixin, stackattrs
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.resources import MultiOpenMixin

from cs.py.func import trace
from cs.lex import r, s
from cs.x import X

from panda3d.core import Point3 as P3
from direct.actor.Actor import Actor
from direct.showbase.ShowBase import ShowBase
from direct.showbase.DirectObject import DirectObject
from direct.task import Task
from direct.interval.IntervalGlobal import Sequence

from typeguard import typechecked

from .egg import Material, Model, PointLight, RGBA
from .geom import torus
from .io import pzread

Numeric = Union[int, float]
Appish = TypeVar('Appish', bound=ShowBase)

def main(argv=None):
  ''' Run the `P3DCommand` command line implementation.
  '''
  return P3DCommand(argv).run()

class P3DCommand(BaseCommand):

  @contextmanager
  def run_context(self):
    with super().run_context():
      yield

  def cmd_demo(self, argv):
    ''' Usage: {cmd} [environment.egg]
    '''
    if argv:
      model_spec = argv.pop(0)
    else:
      model_spec = None
    if argv:
      raise GetoptError("extra arguments: %s" % argv)
    if model_spec == 'torus':
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
      with Model("demo") as model:
        model.append(material)
        surface = torus(
            10,  ##radius1,
            2,  ##radius2,
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
      scene = model
    else:
      scene = model_spec
    options = self.options
    with P3DemoApp(scene) as app:
      X("app=%s", r(app))
      X("app.tkRoot = %s", r(app.tkRoot))
      app.run()
    return 0

  def cmd_pzcat(self, argv):
    ''' Usage: {cmd} *.pz-files...
          Decompress .pz files and write their content to the standard output.
    '''
    if not argv:
      raise GetoptError("missing *.pz filenames")
    badopts = False
    for pzfname in argv:
      with Pfx(pzfname):
        if not pzfname.endswith('.pz'):
          warning("does not end in .pz")
          badopts = True
    if badopts:
      raise GetoptError("bad arguments")
    for pzfname in argv:
      with Pfx(pzfname):
        egg_bs = pzread(pzfname)
        sys.stdout.write(egg_bs.decode('utf-8'))

class P3dApp(MultiOpenMixin, ShowBase):

  DEFAULT_SCALE = 1.0, 1.0, 1.0
  DEFAULT_POS = -10, 10, 0

  @typechecked
  def __init__(
      self,
      scene: Optional[Union[str, Model]] = None,  # TODO: add scene type
      *,
      scale: Optional[Tuple[Numeric, Numeric, Numeric]] = None,
      pos: Optional[Tuple[Numeric, Numeric, Numeric]] = None
  ):
    if scale is None:
      scale = self.DEFAULT_SCALE
    if pos is None:
      pos = self.DEFAULT_POS
    super().__init__()
    print("self.render", s(self.render))
    # Load the environment model.
    if scene is None:
      self.scene = None
    else:
      self.scene = (
          self.add_model(scene) if isinstance(scene, (str, Model)) else scene
      )
      # Reparent the model to render.
      self.scene.reparentTo(self.render)
      # Apply scale and position transforms on the model.
      self.scene.setScale(*scale)
      self.scene.setPos(pos)

  @pfx_method
  def add_model(self, modelref, **lm_kwargs):
    if isinstance(modelref, str):
      # filename
      return self.loader.loadModel(modelref, **lm_kwargs)
    if isinstance(modelref, Model):
      with modelref.saved() as eggpath:
        return self.loader.loadModel(eggpath, **lm_kwargs)
    raise TypeError(
        f'do not know how to loadModel from type {modelref.__class__.__name__}'
    )

  @typechecked
  def add_task(
      self,
      task_func: Callable[[Task.PythonTask], int],
      name: Optional[str] = None,
  ):
    ''' Convenience method to add a task.
    '''
    if name is None:
      name = task_func.__qualname__
    return self.taskMgr.add(task_func, name)

class P3DemoApp(P3dApp):

  def __init__(self, envpath=None):
    if envpath is None:
      envpath = "models/environment"
    super().__init__(
        envpath,
        scale=(0.25, 0.25, 0.25),
        pos=(-8, 42, 0),
    )

  @contextmanager
  def startup_shutdown(self):
    with super().startup_shutdown():
      X("add SetupTask => %r", self.add_task(self.setupTask))
      self.add_task(self.spinCameraTask)

      # Load and transform the panda actor.
      panda = Actor("models/panda-model", {"walk": "models/panda-walk4"})
      print("panda", s(panda), type(panda).__mro__)
      panda.setScale(0.005, 0.005, 0.005)
      panda.reparentTo(self.render)

      # Loop its animation.
      panda.loop("walk")

      # Create the four lerp intervals needed for the panda to
      # walk back and forth.
      posInterval1 = panda.posInterval(
          13, P3(0, -10, 0), startPos=P3(0, 10, 0)
      )
      posInterval2 = panda.posInterval(
          13, P3(0, 10, 0), startPos=P3(0, -10, 0)
      )
      hprInterval1 = panda.hprInterval(3, P3(180, 0, 0), startHpr=P3(0, 0, 0))
      hprInterval2 = panda.hprInterval(3, P3(0, 0, 0), startHpr=P3(180, 0, 0))

      # Create and play the sequence that coordinates the intervals.
      pandaPace = Sequence(
          posInterval1,
          hprInterval1,
          posInterval2,
          hprInterval2,
          name="pandaPace"
      )
      pandaPace.loop()

      yield

  @trace
  def setupTask(self, task):
    ''' An initial task, runs once.
    '''
    X("SETUP")
    X("task = %s", r(task))
    return Task.done

  def spinCameraTask(self, task):
    ''' Move the camera viewpoint by a step.
        Return `Task.cont` so that we get called again.
    '''
    angleDegrees = task.time * 6.0
    angleRadians = angleDegrees * (pi / 180.0)
    self.camera.setPos(20 * sin(angleRadians), -20 * cos(angleRadians), 3)
    self.camera.setHpr(angleDegrees, 0, 0)
    return Task.cont

if __name__ == '__main__':
  sys.exit(main(sys.argv))
