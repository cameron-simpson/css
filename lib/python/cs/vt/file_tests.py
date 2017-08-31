#!/usr/bin/python
#
# File tests.
#       - Cameron Simpson <cs@cskk.id.au>
#

import sys
import unittest
from cs.fileutils import BackedFile_TestMethods
from . import defaults
from .blockify import blockify, top_block_for
from .store import MappingStore
from .file import File

class Test_RWFile(unittest.TestCase, BackedFile_TestMethods):

  def setUp(self):
    self.store_dict = {}
    self.S = MappingStore("Test_RWFile", self.store_dict)
    defaults.pushStore(self.S)
    # construct test backing block
    with open(__file__, "rb") as fp:
      self.backing_text = fp.read()
    self.vt_block = top_block_for(blockify([self.backing_text]))
    self.backed_fp = File(self.vt_block)

  def tearDown(self):
    self.backed_fp.close()
    defaults.popStore()

  def test_flush(self):
    self.test_BackedFile()
    bfp = self.backed_fp
    B2 = bfp.flush()
    self.assertEqual(B2, bfp.backing_block)
    self.assertEqual(bfp.front_range.end, 0)

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
