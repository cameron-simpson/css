#!/usr/bin/env python3

''' Functions associated with classes.
'''

from contextlib import contextmanager
from typing import Any, List

import railroad
from railroad import Diagram, Group, Sequence, Stack

from cs.context import stackattrs

class _RRDiagram(Diagram):

  def __str__(self, **kw):
    ''' `str()` of a diagram returns the SVG.
    '''
    if kw:
      with self(**kw):
        return str(self)
    svg = []
    self.writeStandalone(svg.append)
    return ''.join(svg)

  @contextmanager
  def __call__(self, **kw):
    ''' Calling the diagram patches `railroad` as a context manager.

        Example:

            D = mro_rr(DataDirStore)
            with D(INTERNAL_ALIGNMENT="left"):
              print(D)
    '''
    with stackattrs(railroad,
                    **{k: v or getattr(railroad, k) for k, v in kw.items()}):
      yield

def mro_rr(cls):
  ''' Return a `railroad.Diagram` to render the MRO of `cls`.
  '''
  diagram_items = cls_rr_diagram_items(cls)
  is_simple = len(diagram_items) == 1 and isinstance(diagram_items[0], str)
  return _RRDiagram(*diagram_items, type="simple" if is_simple else "complex")

def cls_rr_diagram_items(cls) -> List[Any]:
  ''' Return a `railroad.Diagram` for the MRO of `cls`,
      i.e. a `railroad.Diagram` from the `railroad-diagrams` package.
  '''
  bases = [base for base in cls.__bases__ if base is not object]
  children = []
  if not bases:
    children.append(cls.__name__)
  elif len(bases) == 1:
    base, = bases
    children.append(cls.__name__)
    children.extend(cls_rr_diagram_items(base))
  else:
    # a stacked group named after cls
    stacked = []
    for base in bases:
      if (base.__bases__ == (object,)
          or base.__module__ in ('collections.abc', 'typing')):
        stacked.append(base.__name__)
      else:
        stacked.append(Sequence(*cls_rr_diagram_items(base)))
    children.append(Sequence(cls.__name__))
    children.append(Group(Stack(*stacked)))
  return children

if __name__ == '__main__':
  D = mro_rr(str)
  from cs.vt.store import DataDirStore
  D = mro_rr(DataDirStore)
  ##from cs.vt.datadir import DataDir
  ##D = mro_rr(DataDir)
  with D(INTERNAL_ALIGNMENT="left"):
    print(D)
