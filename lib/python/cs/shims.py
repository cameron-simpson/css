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

class GetterSetterProxy:
  ''' A proxy class to convert public snake case attribute access
      to C++ or Javaesque camel cased getter and setter method calls.

      Attributes which already exist on a proxied object are
      accessed in preference to the getter/setter methods.

      The proxied objects are available as the `._proxied` attribute
      and the primary proxy object as `.proxied0` for convenience.

      Trying to set a public name when no proxied object has a
      corresponding setter will set it on the primary proxied object
      i.e. `self._proxied[0]`.
  '''

  def __init__(self, *proxied):
    ''' Initialise a proxy for the objects in `proxied`.
    '''
    proxied = tuple(proxied)
    self.__dict__['_proxied'] = proxied
    self.__dict__['_proxied0'] = proxied[0]

  def __getattr__(self, attr):
    # try direct access first
    for proxied in self._proxied:
      try:
        return getattr(proxied, attr)
      except AttributeError:
        pass
    # not a native attribute, try a getter method
    # for a public name
    if not attr.startswith('_'):
      camel_attr = camelcase(attr)
      get_name = 'get' + camel_attr[0].upper() + camel_attr[1:]
      for proxied in self._proxied:
        try:
          pattr = getattr(proxied, get_name)
        except AttributeError:
          pass
        else:
          return pattr()
    # no getter, no attribute
    raise AttributeError(
        "%s(%s).%s: no .%s on %s" % (
            type(self).__name__, type(self._proxied
                                      ).__name__, attr, attr, r(self._proxied)
        )
    )

  def __setattr__(self, attr, value):
    # only intercept public names
    if not attr.startswith('_'):
      for proxied in self._proxied:
        if hasattr(proxied, attr):
          # if the proxied object has this attribute, set it there
          setattr(proxied, attr, value)
          return
      # not a native of the proxy, try the setter method
      camel_attr = camelcase(attr)
      set_name = 'set' + camel_attr[0].upper() + camel_attr[1:]
      for proxied in self._proxied:
        try:
          pattr = getattr(proxied, set_name)
        except AttributeError:
          pass
        else:
          # call the setter to set the value
          pattr(value)
          return
      # no setter, set it on the primary proxied object directly
      setattr(self._proxied[0], attr, value)
    else:
      # private attribute, set it in .__dict__
      self.__dict__[attr] = value
