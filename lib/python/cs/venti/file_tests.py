#!/usr/bin/python
#
# Unit tests for cs.venti.file.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from random import randint
from . import defaults
from .block import Block
from .store import MappingStore
from .file import BlockFile, File
from cs.logutils import X

class TestAll(unittest.TestCase):

  def setUp(self):
    self.S = MappingStore({}, name='tesing MappingStore')
    defaults.pushStore(self.S)

  def tearDown(self):
    defaults.popStore()

  def testFile(self):
    # use this unit test as test data
    with open(__file__) as testfp:
      test_text = testfp.read()
    # an empty read-only file
    BF = BlockFile(Block(data=b''))
    self.assertEqual(b'', BF.read())

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
