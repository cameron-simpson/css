#!/usr/bin/env puython3

from collections import namedtuple
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  pass


from cs.excutils import logexc
from cs.pfx import Pfx

class PipeSpec(namedtuple('PipeSpec', 'name argv')):
  ''' A pipeline specification: a name and list of actions.
  '''

  @logexc
  def pipe_funcs(self, L, action_map, do_trace):
    ''' Compute a list of functions to implement a pipeline.

        It is important that this list is constructed anew for each
        new pipeline instance because many of the functions rely
        on closures to track state.
    '''
    with Pfx(self.name):
      pipe_funcs, errors = argv_pipefuncs(self.argv, L, action_map, do_trace)
    return pipe_funcs, errors
