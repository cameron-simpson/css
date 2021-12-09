#!/usr/bin/env python3

''' Basic stuff to do simple things with OGRE.
'''

from collections import defaultdict
from contextlib import contextmanager
from math import sqrt, sin
from os.path import basename
from typing import Optional, Tuple, Union

from cs.context import stackattrs
from cs.lex import r
from cs.logutils import warning
from cs.pfx import pfx_call, pfx_method
from cs.resources import MultiOpenMixin
from cs.seq import Seq
from cs.shims import call_setters, GetterSetterProxy as GSProxy

from cs.x import X

from icontract import require
from typeguard import typechecked

import Ogre
from Ogre import SceneNode, Vector3 as V3
import Ogre.Bites
import Ogre.RTShader

@typechecked
def V3of(v3: Union[V3, Tuple[float, float, float]]) -> V3:
  ''' Return an `Ogre.Vector3` given a vector or a 3-tuple.
  '''
  return V3(*v3) if isinstance(v3, tuple) else v3

# type for things accepting a Vector3 or a 3-tuple
V3ish = Union[V3, Tuple[float, float, float]]

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

@staticmethod
def distance(p1, p2):
  ''' Compute the distance between 2 points (3-tuples).
  '''
  x1, y1, z1 = p1
  x2, y2, z2 = p2
  return sqrt((x1 - x2)**2 + (y1 - y2)**2 + (z1 - z2)**2)

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
  DEFAULT_VIEWPOINT = V3(0.0, 0.0, 15.0)
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
      lightpoint = viewpoint
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

    # note the default window
    self.window = ctx.getRenderWindow()

    # create a default camera proxy
    self.camera = self.add_camera()
    # map input events to camera controls
    ctx.addInputListener(self.camera._manager)
    # and tell it to render into the main window
    vp = self.window.addViewport(self.camera._camera)
    vp.setBackgroundColour(self.background_colour)

    yield

    self.ctx.closeApp()

  def run(self):
    self.root.startRendering()  # blocks until queueEndRendering is called

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
      position: V3ish,
      scene_manager=None,
  ):
    ''' Add a light, return the light and the `SceneNode` enclosing it.
    '''
    if name is None:
      name = self.auto_name('light')
    position = V3of(position)
    if scene_manager is None:
      scene_manager = self.scene_manager
    light = scene_manager.createLight(name)
    light_node = self.attach(light)
    light_node.setPosition(position)
    return light, light_node

  def add_camera(
      self,
      camera: Optional[Union[str, Ogre.Camera]] = None,
      *,
      scene_manager=None,
      **kw,
  ):
    ''' Add a new camera;
        return a `CamweraProxy`.

        Parameters:
        * `camera`: optional `Camera` or camera name
        * `scene_manager`: optional `SceneManager`,
          default from `self.scene_manager`.

        Other keyword arguments are passed to the `CameraProxy` constructor.
    '''
    if scene_manager is None:
      scene_manager = self.scene_manager
    return CameraProxy(camera, scene_manager=scene_manager, **kw)

  @typechecked
  def add_viewport(
      self,
      name=None,
      *,
      width=720,
      height=420,
      camera: Optional["CameraProxy"] = None,
      scene_manager=None,
      background_colour=None,
      **camera_kw,
  ):
    ''' Create a new viewport and associated window.
        Return `(Window,Viewport,CameraProxy)`.
    '''
    if name is None:
      name = self.auto_name('viewport')
    if camera is None:
      cproxy = self.add_camera(name, scene_manager=scene_manager, **camera_kw)
    else:
      assert not camera_kw
    window = self.ctx.createWindow(name, width, height)
    viewport = window.render.addViewport(camera)
    if background_colour is not None:
      viewport.setBackgroundColour(background_colour)
    return window, viewport, cproxy

  def screenshot(self, camera=None, *, ext='.png'):
    if camera is None:
      camera = self.camera
    vproxy = GSProxy(camera._camera.getViewport())
    with stackattrs(vproxy, overlays_enabled=False):
      target = vproxy.target
      # why 2 renders?
      self.root.renderOneFrame()
      self.root.renderOneFrame()
      target.writeContentsToTimestampedFile("screenshot_", ext)

class CameraProxy(GSProxy):
  ''' A proxy for a camera with the associated scene node and manager.

      Attributes:
      * `_camera`: the `Camera`
      * `_manager`: the `CameraManager`
      * `_name`: the `Camera` name
      * `_node`: the `SceneNode`
  '''

  _seq = Seq()

  @typechecked
  def __init__(
      self,
      camera: Optional[Union[str, Ogre.Camera]] = None,
      *,
      scene_manager,
      parent_scene_node=None,
      target: Optional[Union[SceneNode, V3ish]] = None,
      **kw,
  ):
    ''' Initialise the `CameraProxy`.

        Parameters:
        * `camera`: optional `Camera` or camera name
        * `scene_manager`: the scene manager
        * `parent_scene_node`: optional parent node for the camera node,
          default `scene_manager.getRootSceneNode()`
        * `target`: optional target for the camera as a position or a scene node,
          default `scene_manager.getRootSceneNode()`
        * `yaw`, `pitch`, `distance`: optiona yaw/pitch/distance settings,
          default `0`, `0.3` and `16` respectively

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
    if camera is None:
      camera = 'camera-' + str(next(self._seq))
    if isinstance(camera, str):
      name = camera
      camera = scene_manager.createCamera(name)
    else:
      name = camera.name
    self._camera = camera
    self._name = name
    if parent_scene_node is None:
      parent_scene_node = scene_manager.getRootSceneNode()
    if target is None:
      target = scene_manager.getRootSceneNode()
    elif isinstance(target, tuple):
      target = V3(*target)
    else:
      assert isinstance(target, V3)
    camera_node = self._node = parent_scene_node.createChildSceneNode()
    camera_node.attachObject(camera)
    camera_manager = self._manager = Ogre.Bites.CameraMan(camera_node)
    super().__init__(camera_manager, camera_node, camera)
    self.zoom(target)
    call_setters((camera_manager, camera, camera_node), kw)

  @typechecked
  def aim(
      self,
      target: Union[SceneNode, V3ish],
      *,
      yaw: Union[float, Ogre.Radian] = 0,
      pitch: Union[float, Ogre.Radian] = 0,
      distance=None
  ):
    ''' Aim the camera at `target`, a `SceneNode` or `Vector3` or 3-tuple.
    '''
    if isinstance(target, tuple):
      target = V3(*target)
    if distance is None:
      distance = (
          self._node.getPosition().
          distance(target if isinstance(target, V3) else target.getPosition())
      )
    if isinstance(target, SceneNode):
      pfx_call(self._manager.setTarget, target)
      pfx_call(self._manager.setYawPitchDist, yaw, pitch, distance)
    else:
      pfx_call(self._node.lookAt, target, Ogre.Node.TS_WORLD)

  @pfx_method
  @typechecked
  def zoom(self, target: Ogre.SceneNode, *, excess_ratio: float = 1.1):
    ''' Zoom this camera's view to encompass the target node's bounding radius.
    '''
    # obtain the angle within which the radius must fit
    camera = self._camera
    fov_y = camera.getFOVy()
    # theta for the diameter
    theta = min(fov_y, fov_y * camera.getAspectRatio())
    # theta for the radius
    theta2 = theta / 2.0
    # our postion, the target position, the radius
    pos = self.position
    if target.numAttachedObjects() == 0:
      # aim at the origin of the target
      target_origin = target.convertLocalToWorldPosition(V3(0, 0, 0))
      self.aim(target_origin, distance=10)
    else:
      target_obj = target.getAttachedObject(0)
      target_sphere = target_obj.getWorldBoundingSphere()
      target_pos = target_sphere.getCentre()
      target_radius = target_sphere.getRadius()
      # Compute distance so that the target_radius * excess_ratio
      # subtends theta2.
      new_distance = target_radius * excess_ratio / sin(theta2)
      self.aim(target, distance=new_distance)

class ManualObjectProxy(GSProxy):
  ''' A proxy for `Ogre.ManualObject`
      with convenience methods for mesh definition.
  '''

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
    self._proxied0.begin(material_name, op_type)
    try:
      with stackattrs(self, _adding_section=True):
        yield
    finally:
      self._proxied0.end()

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
    mobj = self._proxied0
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
    pfx_call(self._proxied0.quad, *vertex_indices)

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
    pfx_call(self._proxied0.triangle, *vertex_indices)

  @contextmanager
  @typechecked
  @require(lambda self: not self._adding_section)
  @require(lambda self: not self._updating_section)
  @require(lambda section_index: section_index >= 0)
  def update(self, section_index: int):
    ''' Context manager to enclose a section update.
    '''
    with stackattrs(self, _updating_section=True):
      self._proxied0.begin_update(section_index)
      try:
        yield
      finally:
        self._proxied0.end()

  @typechecked
  def as_mesh(self, mesh_name: Optional[str] = None, resource_group=None):
    ''' Return a new `Ogre.Mesh` contructed from this object.
    '''
    if mesh_name is None:
      app = self._app
      mesh_name = app.auto_name(app.name + '--' + self.name)
    if resource_group is None:
      resource_group = 'General'
    return self._proxied0.convertToMesh(mesh_name, resource_group)

if __name__ == "__main__":
  with App(__file__) as app:
    app.new_entity("sinbad-mesh", "Sinbad.mesh")
    app.run()
