#!/usr/bin/python
#

''' Self tests for cs.app.mailfiler.
    - Cameron Simpson <cs@cskk.id.au>
'''

import sys
from os.path import dirname, join as joinpath
from types import SimpleNamespace as NS
import unittest
from cs.app.mailfiler import (
    get_targets, get_target, Target_Assign, Target_PipeLine,
    Target_Substitution, Target_SetFlag, Target_Function, Target_MailAddress,
    Target_MailFolder, parserules
)
from cs.logutils import D

testdatadir = joinpath(dirname(__file__), 'testdata', 'cs.app.mailfiler')
test_rules_file = joinpath(testdatadir, 'rules')

class TestMailFiler(unittest.TestCase):
  ''' Tests for `cs.app.mailfiler`.
  '''

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def _test_get_target(self, target, target_type):
    for sfx in '', ',', ' ':
      for pfx in '', ':::':
        target_str = pfx + target + sfx
        offset0 = len(pfx)
        offset_expected = offset0 + len(target)
        T, offset = get_target(target_str, offset0)
        self.assertEqual(
            offset, offset_expected, "%r[%d:] ==> offset=%d, expected %d" %
            (target_str, offset0, offset, offset_expected)
        )
        self.assertIsInstance(
            T, target_type, "wrong type for %r: got %s, expected %s" %
            (target_str, type(T), target_type)
        )
    return T

  def test00parseTargets(self):
    self._test_get_target('foo', Target_MailFolder)
    self._test_get_target('foo@bar', Target_MailAddress)
    self._test_get_target('foo=bar', Target_Assign)
    self._test_get_target('|shcmd', Target_PipeLine)
    self._test_get_target('s/this/that/', Target_Substitution)
    self._test_get_target('from:s/this/that/', Target_Substitution)
    self._test_get_target('from:learn_addresses', Target_Function)
    self._test_get_target('from:learn_addresses()', Target_Function)
    for flag_letter, flag_attr in (
        ('D', 'draft'),
        ('F', 'flagged'),
        ('P', 'passed'),
        ('R', 'replied'),
        ('S', 'seen'),
        ('T', 'trashed'),
    ):
      T = self._test_get_target(flag_letter, Target_SetFlag)
      self.assertEqual(T.flag_attr, flag_attr)

  def _testSingleRule(
      self, rule_lines, target_types, labelstr, conditions=None, flags=None
  ):
    if isinstance(rule_lines, str):
      rule_lines = (rule_lines,)
    if flags is None:
      flags = NS(alert=False, halt=False)
    R, = list(parserules(rule_lines))
    D("R = %s", R)
    self.assertEqual(len(R.targets), len(target_types))
    for T, Ttype in zip(R.targets, target_types):
      self.assertIsInstance(T, Ttype)
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
            D(
                "test attr %s: %s VS %s", attr, getattr(C, attr),
                getattr(RC, attr)
            )
            if attr == 'flags':
              Cflags = C.flags
              RCflags = RC.flags
              for flag_name in dir(RC.flags):
                if flag_name == 'D':
                  continue
                if flag_name[0].isalpha():
                  if getattr(RCflags, flag_name):
                    self.assertTrue(
                        flag_name in Cflags,
                        "\"%s\" in Rule but not expected" % (flag_name,)
                    )
                  else:
                    self.assertTrue(
                        flag_name not in Cflags,
                        "\"%s\" expected, but not in Rule" % (flag_name,)
                    )
            else:
              av1 = getattr(C, attr)
              av2 = getattr(RC, attr)
              if not callable(av1) and not callable(av2):
                self.assertEqual(getattr(C, attr), getattr(RC, attr))

  def test10parseRules(self):
    targets, offset = get_targets("subject:s/this/that/", 0)
    self._testSingleRule("varname=value", (Target_Assign,), '', ())
    self._testSingleRule("target . .", (Target_MailFolder,), '', ())
    self._testSingleRule(
        "target labelstr .", (Target_MailFolder,), 'labelstr', ()
    )
    self._testSingleRule(
        "=target labelstr .", (Target_MailFolder,), 'labelstr', (),
        NS(alert=False, halt=True)
    )
    self._testSingleRule(
        "+target labelstr .", (Target_MailFolder,), 'labelstr', (),
        NS(alert=False, halt=False)
    )
    self._testSingleRule(
        "!target labelstr .", (Target_MailFolder,), 'labelstr', (),
        NS(alert=True, halt=False)
    )
    self._testSingleRule(
        "=!target labelstr .", (Target_MailFolder,), 'labelstr', (),
        NS(alert=True, halt=True)
    )
    self._testSingleRule(
        "=!target labelstr .", (Target_MailFolder,), 'labelstr', (),
        NS(alert=True, halt=True)
    )
    self._testSingleRule(
        "target . foo@bar", (Target_MailFolder,), '', (
            NS(
                addrkeys=('foo@bar',),
                flags=(),
                header_names=('to', 'cc', 'bcc')
            ),
        )
    )
    self._testSingleRule(
        "target . ! foo@bar", (Target_MailFolder,), '', (
            NS(
                addrkeys=('foo@bar',),
                flags=('invert',),
                header_names=('to', 'cc', 'bcc')
            ),
        )
    )
    self._testSingleRule(
        "target . from:foo@bar", (Target_MailFolder,), '',
        (NS(addrkeys=('foo@bar',), header_names=('from',)),)
    )
    self._testSingleRule(
        "target . to,cc:foo@bar", (Target_MailFolder,), '',
        (NS(addrkeys=('foo@bar',), header_names=('to', 'cc')),)
    )
    self._testSingleRule(
        "target . to,cc:joe blogs <joe@bar>", (Target_MailFolder,), '',
        (NS(addrkeys=('joe@bar',), header_names=('to', 'cc')),)
    )
    self._testSingleRule(
        "target . list-id.contains(\"<squid-users.squid-cache.org>\")",
        (Target_MailFolder,), '', (
            NS(
                funcname='contains',
                header_names=('list-id',),
                test_string='<squid-users.squid-cache.org>'
            ),
        )
    )

  def testRulesParseFile(self):
    ''' Test parse.
    '''
    list(parserules(test_rules_file))

def selftest(argv):
  ''' Run the unittest main function.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
