#!/usr/bin/env python3

''' Various shims to adapt one thing to another.
'''

from cs.lex import camelcase, r, s
from cs.pfx import Pfx

def call_setters(objs, settings):
  ''' A shim to call setter function on objects for 
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
