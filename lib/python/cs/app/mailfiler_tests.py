#!/usr/bin/python
#
# Self tests for cs.app.mailfiler.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
from os.path import basename, dirname, join as joinpath
import unittest
from cs.app.mailfiler import parserules
from cs.logutils import D
from cs.misc import slist

testdatadir = joinpath(dirname(__file__), 'testdata', 'cs.app.mailfiler')
test_rules_file = joinpath(testdatadir, 'rules')

class TestMailFiler(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def _testSingleRule(self, rule_lines, action_tuple):
    if isinstance(rule_lines, str):
      rule_lines = (rule_lines,)
    R, = list(parserules(rule_lines))
    D("R = %s", R)
    self.assertEquals(len(R.actions), 1)
    self.assertEquals(R.actions[0], action_tuple)

  def testParseRules(self):
    self._testSingleRule( "varname=value", ('ASSIGN', ('varname', 'value')) )

  def testRulesParseFile(self):
    rules = slist(parserules(test_rules_file))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
