#!/usr/bin/python
#
# FUSE tests.
#       - Cameron Simpson <cs@cskk.id.au> 10jul2014
#

import os
import sys
import unittest
from random import randint
from cs.fileutils import BackedFile, BackedFile_TestMethods
from cs.logutils import warning
from cs.x import X
from . import defaults
from .dir import Dir
try:
  from .fuse import mount
except ImportError as e:
  warning("import fails, no mount function: %s", e)
  mount = None
from .store import MappingStore

TESTDIR = 'vtfuse_testdir'

class Test_VTFuse(unittest.TestCase):
  ''' Tests for `cs.vt.fuse`.
  '''

  def setUp(self):
    self.store_dict = {}
    self.S = MappingStore('Test_VTFuse', self.store_dict)
    defaults.pushStore(self.S)
    if os.path.exists(TESTDIR):
      X("rmdir %s", TESTDIR)
      os.rmdir(TESTDIR)
    os.mkdir(TESTDIR)
    self.E = Dir(TESTDIR)
    mount(TESTDIR, self.E, self.S)

  def tearDown(self):
    os.rmdir(TESTDIR)

  @unittest.skipIf(mount is None, "no FUSE mount function")
  def test_FS(self):
    X("test_FS...")

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
