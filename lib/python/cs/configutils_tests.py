#!/usr/bin/python
#
# Self tests for cs.configutils.
#       - Cameron Simpson <cs@zip.com.au>
#

from copy import deepcopy
from os.path import basename, dirname, join as joinpath
import sys
from shutil import copy, rmtree
from tempfile import mkdtemp
from time import sleep
import unittest
from cs.configutils import ConfigWatcher, ConfigSectionWatcher

testdatadir = joinpath(dirname(__file__), 'testdata', 'cs.configutils')
test_config_file = joinpath(testdatadir, 'test.ini')

class TestConfigUtils(unittest.TestCase):

  def setUp(self):
    self.tmpdir = mkdtemp()
    self.tmpfile = joinpath(self.tmpdir, basename(test_config_file))
    copy(test_config_file, self.tmpfile)
    self.state0 = { 'clause1': { 'clause1_value1': '1' },
                    'clause2': { 'clause2_value1': '2' },
                  }

  def tearDown(self):
    rmtree(self.tmpdir)

  def testWatcher(self):
    expected1 = self.state0
    expected2 = deepcopy(expected1)
    expected2['clause2']['clause2_value2'] = '3'
    cfg = ConfigWatcher(self.tmpfile)
    state1 = cfg.as_dict()
    self.assertEqual(state1, expected1)
    with open(self.tmpfile, "a") as fp:
      fp.write("clause2_value2 = 3\n")
    # too early to repoll, get cached content
    state2 = cfg.as_dict()
    self.assertEqual(state2, expected1)
    # later - should reload new content
    sleep(2)
    state3 = cfg.as_dict()
    self.assertEqual(state3, expected2)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
