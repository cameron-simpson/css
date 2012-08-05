#!/usr/bin/python
#
# Self tests for cs.configutils.
#       - Cameron Simpson <cs@zip.com.au>
#

from os.path import basename, dirname, join as joinpath
import sys
from shutil import copy, rmtree
from tempfile import mkdtemp
import unittest
from cs.configutils import ConfigWatcher, ConfigSectionWatcher

testdatadir = joinpath(dirname(__file__), 'testdata', 'cs.configutils')
test_config_file = joinpath(testdatadir, 'test.ini')

class TestConfigUtils(unittest.TestCase):

  def setUp(self):
    self.tmpdir = mkdtemp()
    self.tmpfile = joinpath(self.tmpdir, basename(test_config_file))
    copy(test_config_file, self.tmpfile)

  def tearDown(self):
    rmtree(self.tmpdir)

  def testWatcher(self):
    cfg = ConfigWatcher(self.tmpfile)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  print "testdatadir =", testdatadir
  selftest(sys.argv)
