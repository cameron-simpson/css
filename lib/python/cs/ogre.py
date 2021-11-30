#!/usr/bin/env python3

''' Basic stuff to do simple things with OGRE.
'''

from collections import defaultdict
from contextlib import contextmanager
from math import sqrt
from pprint import pformat, pprint
from typing import Optional, Tuple

from cs.resources import MultiOpenMixin
from cs.seq import Seq
from cs.shims import call_setters

from cs.x import X

from typeguard import typechecked

import Ogre
from Ogre import *
import Ogre.Bites
import Ogre.RTShader

# TODO: maybe put this in cs.shims if it feels clean
def tupleish_call(func, obj_or_tuple, optional=False):
  ''' Call `func` with `obj_or_tuple`
      where `obj_or_tuple` may be a `list` or `tuple`
      or some third type.
      If it is a third type it is passed as a single positional parameter
      otherwise it is unpacked to provide the positional parameters.
      Return the return value from the call to `func`.

      Parameters:
      * `func`: the callable to call, usually an OGRE object method
      * `obj_or_tuple`: a list or tuple, or some third thing
      * `optional`: optional mode switch, default `False`;
        if true then `obj_or_tuple` may also be `None`
        in which case `func` will not be called at all

      This is for OGRE object methods which accept,
      for example, a `Vector3` or 3 explicit arguments.

      Example:

          tupleish_call(mobj.normal, normal, optional=True)

      in `ManualObjectProxy.add_vertex`, where the `normal` might
      be provided as a vector or as a 3tuple and the underlying
      `mobj.normal` method is polymorphic.
      Also, if `normal` is `None`, do not call `mobj.normal`,
      as `normal` is an optional parameter to `add_vertex`.
  '''
  if optional and obj_or_tuple is None:
    return
  if isinstance(obj_or_tuple, (tuple, list)):
    return pfx_call(func, *obj_or_tuple)
  return pfx_call(func, obj_or_tuple)

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
    self.seqs = defaultdict(Seq)
    self.ambient_light = ambient_light
    self.background_colour = background_colour
    self.viewpoint = viewpoint
    self.lightpoint = lightpoint

  @contextmanager
  def startup_shutdown(self):
    ctx = self.ctx = Ogre.Bites.ApplicationContext(self.name)
    ctx.initApp()

    # register for input events
    self.klistener = AppKeyListener()  # must keep a reference around
    ctx.addInputListener(self.klistener)

    self.root = ctx.getRoot()
    scene_manager = self.scene_manager = self.root.createSceneManager()

    shadergen = Ogre.RTShader.ShaderGenerator.getSingleton()
    shadergen.addSceneManager(
        scene_manager
    )  # must be done before we do anything with the scene

    # without light we would just get a black screen
    scene_manager.setAmbientLight(self.ambient_light)

    # create a default light
    self.add_light(position=self.lightpoint)

    # create a default camera and manager
    camera, camera_node, camera_manager = self.add_camera()

    # map input events to camera controls
    ctx.addInputListener(camera_manager)

    # and tell it to render into the main window
    vp = ctx.getRenderWindow().addViewport(camera)
    vp.setBackgroundColour(self.background_colour)

    win2, vp2, camera2 = self.add_viewport(
        background_colour=self.background_colour
    )

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

  @typechecked
  def auto_name(self, flavour: str):
    ''' Allocated a name for an object of the given `flavour`
        (eg "camera").
    '''
    return f"{self.name}-{flavour}-{next(self.seqs[flavour])}"

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
      parent = self.root_node(scene_manager)
    node = parent.createChildSceneNode()
    node.attachObject(obj)
    return node

  def new_entity(self, *a, parent=None, scene_manager=None):
    ''' Create a new entity and add it to the scene;
        return the `SceneNode` containing the new entity.

        The positional parameters are passed to `SceneManager.createEntity`
        to create the new entity.
    '''
    if scene_manager is None:
      scene_manager = self.scene_manager
    ent = scene_manager.createEntity(*a)
    return self.attach(ent, parent=parent, scene_manager=scene_manager)

  @typechecked
  def add_light(
      self,
      name=None,
      *,
      position: Tuple[float, float, float],
      scene_manager=None,
  ):
    ''' Add a light, return the light and the `SceneNode` enclosing it.
    '''
    if name is None:
      name = self.auto_name('light')
    if scene_manager is None:
      scene_manager = self.scene_manager
    light = scene_manager.createLight(name)
    light_node = self.attach(light)
    light_node.setPosition(*position)
    return light, light_node

  def add_camera(
      self,
      name=None,
      *,
      scene_manager=None,
      look_at=None,
      **kw,
  ):
    ''' Add a new camera;
        return the camera and the `SceneNode` enclosing it.

        Parameters:
        * `name`: optional name for the camera
        * `scene_manager`: optional `SceneManager`
        * `look_at`: optional `Vector3` specifying a point for the camera to track,
          passed to `camera.LookAt()`

        Other keyword parameters are used to set things
        on the camera or its manager.
        For example, `near_clip_distance=1` would call `camera.setNearClipDistance(1)`
        and `style=Ogre.Bites.CS_ORBIT` would call `camera_manager.setStyle(Ogre.Bites.CS_ORBIT)`.

        The following default keyword arguments are applied:
        * `auto_aspect_ratio`: `True`
        * `near_clip_distance`: `1`
        * `style`: `Ogre.Bites.CS_ORBIT`
    '''
    kw.setdefault('auto_aspect_ratio', True)
    kw.setdefault('near_clip_distance', 1)
    kw.setdefault('style', Ogre.Bites.CS_ORBIT)
    # create a default camera and manager
    if name is None:
      name = self.auto_name('camera')
    if scene_manager is None:
      scene_manager = self.scene_manager
    camera = scene_manager.createCamera(name)
    camera_node = self.attach(camera)
    if look_at is not None:
      camera_node.lookAt(look_at)
    camera_manager = Ogre.Bites.CameraMan(camera_node)
    call_setters((camera, camera_manager), kw)
    camera_manager.setYawPitchDist(
        0, 0.3, self.distance(self.lightpoint, look_at or (0, 0, 0))
    )
    return camera, camera_node, camera_manager

  def add_viewport(
      self,
      name=None,
      *,
      width=720,
      height=420,
      camera=None,
      scene_manager=None,
      background_colour=None,
      **camera_kw,
  ):
    if name is None:
      name = self.auto_name('viewport')
    if camera is None:
      camera, _, _ = self.add_camera(
          name, scene_manager=scene_manager, **camera_kw
      )
    else:
      assert not camera_kw
    window = self.ctx.createWindow(name, width, height)
    viewport = window.render.addViewport(camera)
    if background_colour is not None:
      viewport.setBackgroundColour(background_colour)
    return window, viewport, camera

if __name__ == "__main__":
  with App(__file__) as app:
    app.new_entity("sinbad-mesh", "Sinbad.mesh")
    app.run()
