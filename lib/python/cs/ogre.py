#!/usr/bin/env python3

''' Basic stuff to do simple things with OGRE.
'''

from contextlib import contextmanager
from math import sqrt
from pprint import pformat, pprint
from typing import Optional, Tuple

from cs.resources import MultiOpenMixin

from cs.x import X

from typeguard import typechecked

import Ogre
from Ogre import *
import Ogre.Bites
import Ogre.RTShader

class AppKeyListener(Ogre.Bites.InputListener):

  def keyPressed(self, evt):
    if evt.keysym.sym == Ogre.Bites.SDLK_ESCAPE:
      Ogre.Root.getSingleton().queueEndRendering()
    return True

class App(MultiOpenMixin):
  ''' A default application, initially based off `Samples/Python/sample.py`.
  '''

  DEFAULT_AMBIENCE = 0.1, 0.1, 0.1
  DEFAULT_BACKGROUND_COLOUR = 0.3, 0.3, 0.3
  DEFAULT_VIEWPOINT = 0.0, 0.0, 15.0
  DEFAULT_LIGHTPOINT = DEFAULT_VIEWPOINT

  @typechecked
  def __init__(
      self,
      name,
      *,
      ambient_light: Optional[Tuple[float, float, float]] = None,
      background_colour: Optional[Tuple[float, float, float]] = None,
      # create a camera here and point it at the origin
      viewpoint: Optional[Tuple[float, float, float]] = None,
      # create a light here and point it at the origin
      # default from theviewpoint
      lightpoint: Optional[Tuple[float, float, float]] = None,
  ):
    if ambient_light is None:
      ambient_light = self.DEFAULT_AMBIENCE
    if background_colour is None:
      background_colour = self.DEFAULT_BACKGROUND_COLOUR
    if viewpoint is None:
      viewpoint = self.DEFAULT_VIEWPOINT
    if lightpoint is None:
      lightpoint = tuple(viewpoint)
    self.name = name
    self.ambient_light = ambient_light
    self.background_colour = background_colour
    self.viewpoint = viewpoint
    self.lightpoint = lightpoint

  @contextmanager
  def startup_shutdown(self):
    ctx = self.ctx = Ogre.Bites.ApplicationContext(self.name)
    ctx = self.ctx
    ctx.initApp()

    # register for input events
    self.klistener = AppKeyListener()  # must keep a reference around
    ctx.addInputListener(self.klistener)

    self.root = ctx.getRoot()
    scn_mgr = self.scene_manager = self.root.createSceneManager()

    shadergen = Ogre.RTShader.ShaderGenerator.getSingleton()
    shadergen.addSceneManager(
        scn_mgr
    )  # must be done before we do anything with the scene

    # without light we would just get a black screen
    scn_mgr.setAmbientLight(self.ambient_light)

    light = scn_mgr.createLight("MainLight")
    light_node = self.attach(light)
    light_node.setPosition(*self.lightpoint)

    # create the camera
    cam = scn_mgr.createCamera("MainCamera")
    cam.setNearClipDistance(5)
    cam.setAutoAspectRatio(True)
    cam_node = self.attach(cam)

    # set up the default camera at the same distance as the default light source
    camman = Ogre.Bites.CameraMan(cam_node)
    camman.setStyle(Ogre.Bites.CS_ORBIT)
    camman.setYawPitchDist(0, 0.3, self.distance(self.lightpoint, (0, 0, 0)))

    # map input events to camera controls
    ctx.addInputListener(camman)

    # and tell it to render into the main window
    vp = ctx.getRenderWindow().addViewport(cam)
    vp.setBackgroundColour(self.background_colour)

    yield

    self.ctx.closeApp()

  def run(self):
    self.root.startRendering()  # blocks until queueEndRendering is called

  @staticmethod
  def distance(p1, p2):
    ''' Compute the distance between 2 points (3-tuples).
    '''
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    return sqrt((x1 - x2)**2 + (y1 - y2)**2 + (z1 - z2)**2)

  def root_node(self, scene_manager=None):
    ''' Return the root `SceneNode` from `scene_manager`
        (default `self.scene_manager`).
    '''
    if scene_manager is None:
        # default SceneManager is the one set up an init
        scene_manager = self.scene_manager
    return scene_manager.getRootSceneNode()

  def attach(self, obj, *, parent=None, scene_manager=None):
    ''' Attach `obj` (en entity or camera) to a scene,
        return the `SceneNode` containing it.
    '''
    if parent is None:
      parent=self.root_node(scene_manager)
    node = parent.createChildSceneNode()
    node.attachObject(obj)
    return node

  def new_entity(self, *a, parent=None, scene_manager=None):
    ''' Add a new entity to the scene,
        return the `SceneNode` containing the new entity.

        The positional parameters are passed to `SceneManager.createEntity`
        to create the new entity.
    '''
    if scene_manager is None:
      scene_manager = self.scene_manager
    ent = scene_manager.createEntity(*a)
    return self.attach(ent, parent=parent, scene_manager=scene_manager)

  def add_camera(

if __name__ == "__main__":
  with App(__file__) as app:
    app.new_entity("sinbad-mesh", "Sinbad.mesh")
    app.run()
