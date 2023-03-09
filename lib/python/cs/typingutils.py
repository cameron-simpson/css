#!/usr/bin/env python3

''' Hacks for use with typing.
'''

from typing import TypeVar

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
