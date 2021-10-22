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
from .file import RWBlockFile

class Test_RWFile(unittest.TestCase, BackedFile_TestMethods):
  ''' Tests for `RWBlockFile`.
  '''

  def setUp(self):
    self.store_dict = {}
    self.S = MappingStore("Test_RWFile", self.store_dict)
    defaults.pushStore(self.S)
    # construct test backing block
    with open(__file__, "rb") as fp:
      self.backing_text = fp.read()
    self.vt_block = top_block_for(blockify([self.backing_text]))
    self.vt_file = RWBlockFile(self.vt_block)
    self.backed_fp = self.vt_file._file

  def tearDown(self):
    self.vt_file.close()
    defaults.popStore()

  def test_flush(self):
    self.test_BackedFile()
    B2 = self.vt_file.sync()
    self.backed_fp = self.vt_file._file
    self.assertEqual(B2, self.vt_file._backing_block)
    bfp = self.backed_fp
    self.assertEqual(
        bfp.front_range.end, 0,
        "bfp(id=%d).front_range.end should be 0, range is: %s" % (
            id(bfp),
            bfp.front_range,
        )
    )

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
