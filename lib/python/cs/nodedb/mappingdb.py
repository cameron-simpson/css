#!/usr/bin/python
#
# A backend made from a mapping.
#       - Cameron Simpson <cs@cskk.id.au> 25may2012
#

import sys
from cs.logutils import error, warning
from cs.pfx import Pfx, pfx_method
from . import Node
from .backend import Backend

class MappingBackend(Backend):

  def __init__(self, mapping, readonly=False):
    Backend.__init__(self, readonly=readonly, raw=True)
    self.mapping = mapping

  @pfx_method(use_str=True)
  def init_nodedb(self):
    nodedb = self.nodedb
    for nodekey, nodeish in self.mapping.items():
      node = nodedb.make(nodekey)
      for attr, values in nodeish.items():
        getattr(node, attr + 's').extend(values)

  def _open(self):
    pass

  def close(self):
    pass

  def items(self):
    return self.mapping.items()

  def keys(self):
    return self.mapping.keys()

  def values(self):
    return self.mapping.values()

  def __getitem__(self, key):
    return self.mapping[key]

  def _update(self, update):
    ''' Apply a cs.nodedb.backend.Update.
    '''
    if update.do_append:
      self.extendAttr(update.type, update.name, update.attr, update.values)
    else:
      self.setAttr(update.type, update.name, update.attr, update.values)

  def __setitem__(self, key, value):
    if not isinstance(value, Node):
      raise ValueError(
          "MappingBackend.__setitem__: value is not a Node: key=%r, value=%r" %
          (key, value)
      )
    self.mapping[key] = dict(value)

  def __delitem__(self, key):
    if key in self.mapping:
      del self.mapping[key]

  def setAttr(self, t, name, attr, values):
    self.mapping.setdefault((t, name), {})[attr] = list(values)

  @pfx_method
  def extendAttr(self, t, name, attr, values):
    self.mapping.setdefault((t, name), {}).setdefault(attr, []).extend(values)

if __name__ == '__main__':
  import cs.nodedb.mappingdb_tests
  cs.nodedb.mappingdb_tests.selftest(sys.argv)
