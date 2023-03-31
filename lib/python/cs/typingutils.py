#!/usr/bin/env python3

''' Trite hacks for use with typing.
'''

from typing import TypeVar

__version__ = '20230331'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
}

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
