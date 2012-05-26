#!/usr/bin/python
#
# A backend made from a mapping.
#       - Cameron Simpson <cs@zip.com.au> 25may2012
#

import sys
from cs.logutils import Pfx, error, warning , info, D
from . import Node
from .backend import Backend

class MappingBackend(Backend):

  def __init__(self, mapping, readonly=False):
    Backend.__init__(self, readonly=readonly)
    self.mapping = mapping

  def close(self):
    pass

  def sync(self):
    pass

  def iteritems(self):
    return self.mapping.iteritems()

  def iterkeys(self):
    return self.mapping.iterkeys()

  def itervalues(self):
    for item in self.iteritems():
      yield item[1]

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
  import cs.nodedb.csvdb_tests
  cs.nodedb.csvdb_tests.selftest(sys.argv)
