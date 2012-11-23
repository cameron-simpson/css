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
from cs.misc import O, slist

testdatadir = joinpath(dirname(__file__), 'testdata', 'cs.app.mailfiler')
test_rules_file = joinpath(testdatadir, 'rules')

class TestMailFiler(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def _testSingleRule(self, rule_lines, action_tuple, labelstr, conditions=None, flags=None):
    if isinstance(rule_lines, str):
      rule_lines = (rule_lines,)
    if flags is None:
      flags = O(alert=False, halt=False)
    R, = list(parserules(rule_lines))
    D("R = %s", R)
    self.assertEquals(len(R.actions), 1)
    self.assertEquals(R.actions[0], action_tuple)
    self.assertEquals(R.label, labelstr)
    self.assertEquals(R.flags.alert, flags.alert)
    self.assertEquals(R.flags.halt, flags.halt)
    if conditions is not None:
      self.assertEquals(len(R.conditions), len(conditions))
      for i in range(len(conditions)):
        C = conditions[i]
        RC = R.conditions[i] = conditions[i]
        for attr in dir(C):
          self.assertEquals(getattr(C, attr), getattr(RC, attr))

  def testParseRules(self):
    self._testSingleRule( "varname=value", ('ASSIGN', ('varname', 'value')), '', () )
    self._testSingleRule( "target . .", ('TARGET', 'target'), '', () )
    self._testSingleRule( "target labelstr .", ('TARGET', 'target'), 'labelstr', () )
    self._testSingleRule( "=target labelstr .",
                          ('TARGET', 'target'), 'labelstr',
                          (), O(alert=False, halt=True) )
    self._testSingleRule( "+target labelstr .",
                          ('TARGET', 'target'), 'labelstr',
                          (), O(alert=False, halt=False) )
    self._testSingleRule( "!target labelstr .",
                          ('TARGET', 'target'), 'labelstr',
                          (), O(alert=True, halt=False) )
    self._testSingleRule( "!=target labelstr .",
                          ('TARGET', 'target'), 'labelstr',
                          (), O(alert=True, halt=True) )
    self._testSingleRule( "=!target labelstr .",
                          ('TARGET', 'target'), 'labelstr',
                          (), O(alert=True, halt=True) )
    self._testSingleRule( "target . foo@bar",
                          ('TARGET', 'target'), '',
                          ( O(addrkeys='foo@bar', header_names=('to', 'cc', 'bcc')), ) )
    self._testSingleRule( "target . from:foo@bar",
                          ('TARGET', 'target'), '',
                          ( O(addrkeys='foo@bar', header_names=('from',)), ) )
    self._testSingleRule( "target . to,cc:foo@bar",
                          ('TARGET', 'target'), '',
                          ( O(addrkeys='foo@bar', header_names=('to', 'cc')), ) )

  def testRulesParseFile(self):
    rules = slist(parserules(test_rules_file))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
