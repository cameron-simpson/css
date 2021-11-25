#!/usr/bin/env python3

''' Various shims to adapt one thing to another.
'''

from cs.lex import camelcase, r, s
from cs.pfx import Pfx

def call_setters(objs, settings):
  ''' A shim to call setter function on objects for the snake cased
      mapping `settings`.

      This supports easy use of Pythonic snake cased named
      to apply values to objects with C++ or Javaesque
      camel cased getter and setter methods,
      particularly useful when these values have been supplied as keyword
      arguments to a function call.

      For example, I have a little adaptor library for the ORGE C++ library
      and the excellent `ogre-python` adaptor module.
      The module exposes the OGRE objects fairly directly,
      which thus have setter and getter methods.

      My adaptor has an `add_camera()` method like this:

          def add_camera(
              self,
              name=None,
              *,
              scene_manager=None,
              look_at=None,
              **kw,
          ):

      After it makes a `camera` and `camera_manager` it calls:

          call_setters((camera, camera_manager), kw)

      to apply the supplied keyword arguments to the `Camera`
      or `CameraManager` objects.
      For example, if I had supplied a keyword argument
      `near_clip_distance=1`
      this function will find the `setNearClipDistance` method
      on the `camera` object and call
      `camera.setNearClipDistance(1)`.
  '''
  for snake_k, v in settings.items():
    with Pfx("%s=%r", snake_k, v):
      camel_k = camelcase(snake_k)
      set_name = 'set' + camel_k[0].upper() + camel_k[1:]
      setter = None
      for obj in objs:
        try:
          setter = getattr(obj, set_name)
        except AttributeError:
          continue
        else:
          break
      if setter is None:
        raise AttributeError(
            "no %r setter on any object in objs=%r" % (set_name, objs)
        )
      with Pfx("%s.%s(%s)", s(obj), set_name, r(v)):
        setter(v)
