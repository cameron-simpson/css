#!/usr/bin/python
#
# Self tests for cs.app.mailfiler.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import sys
from os.path import basename, dirname, join as joinpath
import unittest
from cs.app.mailfiler import parserules
from cs.logutils import D
from cs.obj import O, slist

if not os.environ.get('DEBUG', ''):
  def D(*a):
    pass

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
    self.assertEqual(len(R.actions), 1)
    self.assertEqual(R.actions[0], action_tuple)
    self.assertEqual(R.label, labelstr)
    self.assertEqual(R.flags.alert, flags.alert)
    self.assertEqual(R.flags.halt, flags.halt)
    if conditions is not None:
      self.assertEqual(len(R.conditions), len(conditions))
      for i in range(len(conditions)):
        C = conditions[i]
        RC = R.conditions[i]
        D("C = %s", C)
        for attr in dir(C):
          if attr[0].isalpha():
            D("test attr %s: %s VS %s", attr, getattr(C, attr), getattr(RC, attr))
            if attr == 'flags':
              Cflags = C.flags
              RCflags = RC.flags
              for flag_name in dir(RC.flags):
                if flag_name == 'D':
                  continue
                if flag_name[0].isalpha():
                  if getattr(RCflags, flag_name):
                    self.assertTrue(flag_name in Cflags, "\"%s\" in Rule but not expected" % (flag_name,))
                  else:
                    self.assertTrue(flag_name not in Cflags, "\"%s\" expected, but not in Rule" % (flag_name,))
            else:
              av1 = getattr(C, attr)
              av2 = getattr(RC, attr)
              if not callable(av1) and not callable(av2):
                self.assertEqual(getattr(C, attr), getattr(RC, attr))

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
    self._testSingleRule( "=!target labelstr .",
                          ('TARGET', 'target'), 'labelstr',
                          (), O(alert=True, halt=True) )
    self._testSingleRule( "=!target labelstr .",
                          ('TARGET', 'target'), 'labelstr',
                          (), O(alert=True, halt=True) )
    self._testSingleRule( "target . foo@bar",
                          ('TARGET', 'target'), '',
                          ( O(addrkeys=('foo@bar',), flags=(), header_names=('to', 'cc', 'bcc')), ) )
    self._testSingleRule( "target . ! foo@bar",
                          ('TARGET', 'target'), '',
                          ( O(addrkeys=('foo@bar',), flags=('invert',), header_names=('to', 'cc', 'bcc')), ) )
    self._testSingleRule( "target . from:foo@bar",
                          ('TARGET', 'target'), '',
                          ( O(addrkeys=('foo@bar',), header_names=('from',)), ) )
    self._testSingleRule( "target . to,cc:foo@bar",
                          ('TARGET', 'target'), '',
                          ( O(addrkeys=('foo@bar',), header_names=('to', 'cc')), ) )
    self._testSingleRule( "target . to,cc:joe blogs <joe@bar>",
                          ('TARGET', 'target'), '',
                          ( O(addrkeys=('joe@bar',), header_names=('to', 'cc')), ) )
    self._testSingleRule( "target . list-id.contains(\"<squid-users.squid-cache.org>\")",
                          ('TARGET', 'target'), '',
                          ( O(funcname='contains',
                              header_names=('list-id',),
                              test_string='<squid-users.squid-cache.org>'), ) )

  def testRulesParseFile(self):
    rules = slist(parserules(test_rules_file))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
