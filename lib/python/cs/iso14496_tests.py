#!/usr/bin/python

''' Unit tests for cs.iso14496.
'''

import sys
import os
import os.path
import unittest
from cs.logutils import setup_logging
from .binary_tests import BaseTestBinaryClasses
from .iso14496 import Box
from . import iso14496 as iso14496_module

TESTFILE = 'TEST.mp4'

class Test_iso14496(unittest.TestCase):
  ''' Test `cs.iso14496`.
  '''

  @unittest.skipUnless(os.path.exists(TESTFILE), 'no ' + TESTFILE)
  def test(self):
    ''' Basic scan of the test MP4 file.
    '''
    S = os.stat(TESTFILE)
    mp4_size = S.st_size
    nboxes = 0
    for box in Box.scan(TESTFILE):
      nboxes += 1
      print(box.box_type_s)
    self.assertEqual(box.end_offset, mp4_size)

class TestISO14496BinaryClasses(BaseTestBinaryClasses, unittest.TestCase):
  ''' Test the `PacketField`s in `cs.iso14496`.
      Subclasses `cs.binary_tests.BaseTestBinaryClasses`
      which locates all `PacketFields` in the associated module.
  '''
  test_module = iso14496_module

def selftest(argv, **kw):
  ''' Run the unit tests.
  '''
  setup_logging(__file__)
  sys.argv = argv
  unittest.main(__name__, defaultTest=None, argv=argv, failfast=True, **kw)

if __name__ == '__main__':
  selftest(sys.argv)
