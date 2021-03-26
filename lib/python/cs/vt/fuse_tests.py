#!/usr/bin/python
#
# FUSE tests.
#       - Cameron Simpson <cs@cskk.id.au> 10jul2014
#

import os
import sys
import unittest
from random import randint
from tempfile import TemporaryDirectory
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
    testname = type(self).__name__
    self.store_dict = {}
    self.S = MappingStore(testname, self.store_dict)
    defaults.pushStore(self.S)
    self.tmpdir = TemporaryDirectory(prefix=testname + '-', dir='.')
    self.testdirpath = self.tmpdir.name
    self.E = Dir(self.testdirpath)
    mount(self.testdirpath, self.E, S=self.S)

  def tearDown(self):
    os.system("set -x; umount '%s'" % self.testdirpath)
    del self.tmpdir

  @unittest.skipIf(mount is None, "no FUSE mount function")
  def test_FS(self):
    X("test_FS...")

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
