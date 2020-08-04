#!/usr/bin/python
#
# A backend made from a mapping.
#       - Cameron Simpson <cs@cskk.id.au> 25may2012
#

import sys
from cs.py3 import iteritems as map_iteritems, \
                   iterkeys as map_iterkeys, \
                   itervalues as map_itervalues
from cs.logutils import error, warning , info, D
from cs.pfx import Pfx
from . import Node
from .backend import Backend

class MappingBackend(Backend):

  def __init__(self, mapping, readonly=False):
    Backend.__init__(self, readonly=readonly, raw=True)
    self.mapping = mapping

  def init_nodedb(self):
    pass

  def _open(self):
    pass

  def close(self):
    pass

  def iteritems(self):
    return map_iteritems(self.mapping)

  def iterkeys(self):
    return map_iterkeys(self.mapping)

  def itervalues(self):
    return map_itervalues(self.mapping)

  def __getitem__(self, key):
    return self.mapping[key]

  def __setitem__(self, key, value):
    if not isinstance(value, Node):
      raise ValueError(
              "MappingBackend.__setitem__: value is not a Node: key=%r, value=%r"
              % (key, value))
    self.mapping[key] = dict(value)

  def __delitem__(self, key):
    del self.mapping[key]

  def setAttr(self, t, name, attr, values):
    self.mapping[t, name][attr] = list(values)

  def extendAttr(self, t, name, attr, values):
    self.mapping[t, name].setdefault(attr, []).extend(values)

if __name__ == '__main__':
  import cs.nodedb.mappingdb_tests
  cs.nodedb.mappingdb_tests.selftest(sys.argv)
