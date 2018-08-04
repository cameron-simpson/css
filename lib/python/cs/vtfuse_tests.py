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
from cs.logutils import X
from cs.vt import defaults
from cs.vt.dir import Dir
from cs.vt.vtfuse import mount
from cs.vt.store import MappingStore

TESTDIR = 'vtfuse_testdir'

class Test_VTFuse(unittest.TestCase):

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

  def test_FS(self):
    X("test_FS...")

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
