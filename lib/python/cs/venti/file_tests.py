#!/usr/bin/python
#
# Unit tests for cs.venti.file.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from random import randint
from cs.fileutils import BackedFile, BackedFile_TestMethods
from . import defaults
from .block import Block
from .store import MappingStore
from .file import BlockFile, File
from cs.logutils import X

class Test_RWFile(unittest.TestCase, BackedFile_TestMethods):

  def setUp(self):
    self.store_dict = {}
    self.S = MappingStore(self.store_dict, name='tesing MappingStore')
    defaults.pushStore(self.S)
    # construct test backing block
    with open(__file__, "rb") as fp:
      self.backing_text = fp.read()
    self.vt_block = Block(data=self.backing_text)
    self.backed_fp = File(self.vt_block)

  def tearDown(self):
    X("tearDown: Store=%r", self.store_dict)
    self.backed_fp.close()
    defaults.popStore()

  def test_Sync(self):
    self.test_BackedFile()
    bfp = self.backed_fp
    B2 = bfp.sync()
    X("B2 = %s", B2)
    self.assertEqual(B2, bfp.backing_block)
    self.assertIsNone(bfp._front_file)

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
