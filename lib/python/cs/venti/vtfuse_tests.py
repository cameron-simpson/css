#!/usr/bin/python
#
# Unit tests for cs.venti.vtfuse.
#       - Cameron Simpson <cs@zip.com.au> 10jul2014
#

import os
import sys
import unittest
from random import randint
from cs.fileutils import BackedFile, BackedFile_TestMethods
from . import defaults
from .dir import Dir
from .vtfuse import mount
from .store import MappingStore
from cs.logutils import X

TESTDIR = 'vtfuse_testdir'

class Test_VTFuse(unittest.TestCase):

  def setUp(self):
    self.store_dict = {}
    self.S = MappingStore(self.store_dict, name='tesing MappingStore')
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
