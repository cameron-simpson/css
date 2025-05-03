#!/usr/bin/env python3

''' Trite hacks for use with typing.
'''

from typing import get_args, get_origin, TypeVar, Union

__version__ = '20250428-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
}

def is_optional(annotation):
  ''' Check if `annotation` is an `Optional[type]`.
      Return `type` if so, `None` otherwise.
  '''
  origin = get_origin(annotation)
  if origin is not Union:
    return None
  try:
    t, none = get_args(annotation)
  except ValueError:
    # an Optional is [type,None]
    return None
  if none is not None and none is not type(None):
    # [type1,type2] is not an Optional
    return None
  return t

def subtype(t, name=None):
  ''' Construct a `TypeVar` for subtypes of the type `t`.

      Parameters:
      * `t`: the type which bounds the `TypeVar`
      * `name`: optional name for the `TypeVar`,
        default `t.__name__ + 'SubType'`
  '''
  if name is None:
    name = t.__name__ + 'SubType'
  return TypeVar(name, bound=t)
