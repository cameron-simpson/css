#!/usr/bin/env python3

''' Basic stuff to do simple things with OGRE.
'''

from collections import defaultdict
from contextlib import contextmanager
from math import sqrt
from os.path import basename
from pprint import pformat, pprint
import time
from typing import Optional, Tuple, Union

from cs.context import stackattrs
from cs.pfx import pfx_call
from cs.resources import MultiOpenMixin
from cs.seq import Seq
from cs.shims import call_setters, GetterSetterProxy as GSProxy

from cs.x import X

from icontract import require
from typeguard import typechecked

import Ogre
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
      app_subdir=None,
      ambient_light: Optional[Tuple[float, float, float]] = None,
      background_colour: Optional[Tuple[float, float, float]] = None,
      # create a camera here and point it at the origin
      viewpoint: Optional[Tuple[float, float, float]] = None,
      # create a light here and point it at the origin
      # default from theviewpoint
      lightpoint: Optional[Tuple[float, float, float]] = None,
  ):
    if app_subdir is None:
      app_subdir = __name__ + '--' + basename(name)
    if ambient_light is None:
      ambient_light = self.DEFAULT_AMBIENCE
    if background_colour is None:
      background_colour = self.DEFAULT_BACKGROUND_COLOUR
    if viewpoint is None:
      viewpoint = self.DEFAULT_VIEWPOINT
    if lightpoint is None:
      lightpoint = tuple(viewpoint)
    self.name = name
    self.app_subdir = app_subdir
    self.fsl = Ogre.FileSystemLayer(self.app_subdir)
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
    self.camera = camera

    # map input events to camera controls
    ctx.addInputListener(camera_manager)

    # note the default window
    self.window = ctx.getRenderWindow()

    # and tell it to render into the main window
    vp = self.window.addViewport(camera)
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

  def screenshot(self, camera, *, ext='.png'):
    cproxy = GSProxy(camera)
    vproxy = GSProxy(cproxy.viewport)
    with stackattrs(vproxy, overlays_enabled=False):
      target = vproxy.target
      # why 2 renders?
      self.root.renderOneFrame()
      self.root.renderOneFrame()
      target.writeContentsToTimestampedFile("screenshot_", ext)

class ManualObjectProxy(GSProxy):

  @typechecked
  def __init__(
      self, name: Union[str, Ogre.ManualObject], *, app=None, dynamic=False
  ):
    ''' Initialise a proxy for an `Ogre.ManualObject`.

        Parameters:
        * `name`: a name for the new object, or a presupplied `Ogre.ManualObject`
        * `app`: optional `App` instance, used for operations requiring some context
        * `dynamic`: whether the `ManualObject` will be dynamic,
          ignored if an object is presupplied
    '''
    if isinstance(name, str):
      mobj = Ogre.ManualObject(name)
      mobj.setDynamic(dynamic)
    else:
      assert isinstance(name, Ogre.ManualObject)
      mobj = name
      name = mobj.getName()
    super().__init__(mobj)
    self._app = app
    self._adding_section = False
    self._updating_section = False

  @contextmanager
  @typechecked
  @require(lambda self: not self._adding_section)
  def new_section(
      self,
      material_name: str,
      op_type: int = Ogre.RenderOperation.OT_TRIANGLE_LIST
  ):
    ''' Context manager to enclose a new section definition.

        Parameters:
        * `material_name`: the material name
        * `op_type`: the render operation type
          from the operation types defined by `Ogre.RenderOperation`,
          default: `Ogre.RenderOperation.OT_TRIANGLE_LIST`
        * `dynamic`: whether this is a dynamic object, default `False`

        Example:

            with mobj_proxy.new_section():
                with mobj_proxy.add_quad():
                    mobj_proxy.add_vertex(...)
                    mobj_proxy.add_vertex(...)
                    mobj_proxy.add_vertex(...)
                    mobj_proxy.add_vertex(...)
    '''
    self._proxied.begin(material_name, op_type)
    try:
      with stackattrs(self, _adding_section=True):
        yield
    finally:
      self._proxied.end()

  @typechecked
  @require(lambda self: self._adding_section)
  def add_vertex(
      self, position: Tuple, *, normal=None, texture_coord=None, colour=None
  ):
    ''' Add a new vertex to the current section.

        Parameters:
        * `position`: the position of the vertex,
          a `Vector3` or a coordinate 3-tuple
        * `normal`: the optional normal to this vertex,
          a `Vector3` or a vector 3-tuple
        * `texture_coord`: an optional texture coordinate
        * `colour`: an optional colour
    '''
    mobj = self._proxied
    tupleish_call(mobj.position, position)
    tupleish_call(mobj.normal, normal, optional=True)
    tupleish_call(mobj.textureCoord, texture_coord, optional=True)
    tupleish_call(mobj.colour, colour, optional=True)

  def _build_vertex_list(self, vertex_indices, *existing_vertex_indices):
    if len(existing_vertex_indices) > len(vertex_indices):
      raise ValueError(
          "too many existing_vertex_indices supplied, max %d, got %d: %r" % (
              len(vertex_indices),
              len(existing_vertex_indices),
              existing_vertex_indices,
          )
      )
    nvertices0 = self.current_vertex_count
    for i, vertex_index in enumerate(existing_vertex_indices):
      if vertex_index is None:
        # skip placeholder
        continue
      # negative indices provide reuse of recent vertices
      if vertex_index < 0:
        vertex_index = nvertices0 + vertex_index
        assert vertex_index >= 0
      vertex_indices[i] = vertex_index
    yield vertex_indices
    n_new_vertices = self.current_vertex_count - nvertices0
    new_vertex_index = nvertices0
    # fill in unfilled vertices with the additional vertex indices
    for i in range(len(vertex_indices)):
      if vertex_indices[i] is None:
        # skip indices already listed
        while new_vertex_index in vertex_indices:
          new_vertex_index += 1
        vertex_indices[i] = new_vertex_index
        new_vertex_index += 1
    if new_vertex_index < self.current_vertex_count:
      warning(
          "%d unused vertices left over",
          self.current_vertex_count - new_vertex_index
      )

  @contextmanager
  @require(lambda self: self._adding_section)
  def add_quad(self, *existing_vertex_indices):
    ''' Context manage to surround the addition of 4 vertices comprising a quad.

        Example:

            # add a new quad reusing 2 previously created vertices
            # and 2 newly created ones inside the suite
            with mobj_proxy.add_quad(-2, -1):
                mobj_proxy.add_vertex(...)
                mobj_proxy.add_vertex(...)
    '''
    vertex_indices = [None, None, None, None]
    yield from self._build_vertex_list(
        vertex_indices, *existing_vertex_indices
    )
    print("QUAD", vertex_indices)
    pfx_call(self._proxied.quad, *vertex_indices)

  @contextmanager
  @require(lambda self: self._adding_section)
  def add_triangle(self, *existing_vertex_indices):
    ''' Context manage to surround the addition of 3 vertices comprising a triangle.

        Example:

            mobjp = ManualObjectProxy("mobj1", app=app)
            with mobjp.new_section("section1"):
                # new triangle
                with mobjp.add_triangle() as V:
                    mobjp.add_vertex((-20, 20, 20), normal=(0, 0, 1), texture_coord=(0, 0))
                    mobjp.add_vertex((-20, -20, 20), normal=(0, 0, 1), texture_coord=(0, 1))
                    mobjp.add_vertex((20, -20, 20), normal=(0, 0, 1), texture_coord=(1, 1))
                print("triangle 1 =", V)
                # second triangle reusing the most recent 2 vertices
                with mobjp.add_triangle(-1, None, -2) as V2:
                    mobjp.add_vertex((20, -40, 40), normal=(0, 0, 1), texture_coord=(1, 1))
                print("triangle 2 =", V2)

    '''
    vertex_indices = [None, None, None]
    yield from self._build_vertex_list(
        vertex_indices, *existing_vertex_indices
    )
    print("TRIANGLE", vertex_indices)
    pfx_call(self._proxied.triangle, *vertex_indices)

  @contextmanager
  @typechecked
  @require(lambda self: not self._adding_section)
  @require(lambda self: not self._updating_section)
  @require(lambda section_index: section_index >= 0)
  def update(self, section_index: int):
    ''' Context manager to enclose a section update.
    '''
    with stackattrs(self, _updating_section=True):
      self._proxied.begin_update(section_index)
      try:
        yield
      finally:
        self._proxied.end()

  @typechecked
  def as_mesh(self, mesh_name: Optional[str] = None, resource_group=None):
    ''' Return a new `Ogre.Mesh` contructed from this object.
    '''
    if mesh_name is None:
      app = self._app
      mesh_name = app.auto_name(app.name + '--' + self.name)
    if resource_group is None:
      resource_group = 'General'
    return self._proxied.convertToMesh(mesh_name, resource_group)

if __name__ == "__main__":
  with App(__file__) as app:
    app.new_entity("sinbad-mesh", "Sinbad.mesh")
    app.run()
