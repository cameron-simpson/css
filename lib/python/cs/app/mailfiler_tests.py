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

  def testRulesParse(self):
    rules = slist(parserules(test_rules_file))
    ##D("rules = %s", rules)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
