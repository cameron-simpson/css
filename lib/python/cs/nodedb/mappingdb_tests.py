#!/usr/bin/python
#
# Unit tests for cs.nodedb.mappingdb.
#       - Cameron Simpson <cs@cskk.id.au>
#

import os
import sys
import unittest
from cs.logutils import D
from cs.debug import thread_dump
from cs.timeutils import sleep
from . import NodeDB
from .mappingdb import MappingBackend
from .node_tests import TestAll as NodeTestAll

class MappingDBTestAll(NodeTestAll):
  ''' Tests for `cs.nodedb.mappingdb`.
  '''
  pass

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
