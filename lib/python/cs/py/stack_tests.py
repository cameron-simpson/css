#!/usr/bin/python
#
# Self tests for cs.py/stack.
#       - Cameron Simpson <cs@cskk.id.au>
#

import sys
import time
import unittest
from cs.py.stack import frames, caller
from cs.logutils import setup_logging

class TestStack(unittest.TestCase):
  ''' Tests for `cs.py.stack`.
  '''

  def test00stack(self):
    Fs = frames()

  def test01caller(self):
    F = caller()

def selftest(argv):
  setup_logging()
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
