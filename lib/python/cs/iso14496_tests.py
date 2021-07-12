#!/usr/bin/python

''' Unit tests for cs.iso14496.
'''

from __future__ import print_function
import sys
import os
import os.path
import unittest
from cs.logutils import setup_logging
from .binary_tests import _TestPacketFields
from .iso14496 import parse
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
    with open(TESTFILE, 'rb') as mp4fp:
      over_box = parse(mp4fp)
      self.assertEqual(over_box.end_offset - over_box.offset, mp4fp.tell())
    self.assertEqual(over_box.offset, 0)
    self.assertEqual(
        over_box.end_offset, mp4_size,
        "over_box.end_offset=%d, mp4fp.tell=%d" %
        (over_box.end_offset, mp4_size)
    )

class TestISO14496PacketFields(_TestPacketFields, unittest.TestCase):
  ''' Test the `PacketField`s in `cs.iso14496`.
      Subclasses `cs.binary_tests._TestPacketFields`
      which locates all `PacketFields` in the associated module.
  '''

  def setUp(self):
    ''' We're testing the cs.binary module.
    '''
    self.module = iso14496_module

def selftest(argv, **kw):
  ''' Run the unit tests.
  '''
  setup_logging(__file__)
  sys.argv = argv
  unittest.main(__name__, defaultTest=None, argv=argv, failfast=True, **kw)

if __name__ == '__main__':
  selftest(sys.argv)
