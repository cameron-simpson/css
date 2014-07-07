#!/usr/bin/python
#
# Unit tests for cs.venti.file.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from random import randint
from .file import BlockFile, File
from cs.logutils import X

class TestAll(unittest.TestCase):

  def setUp(self):
    self.S = MappingStore({}, name='tesing MappingStore')

  def tearDown(self):
    pass

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
