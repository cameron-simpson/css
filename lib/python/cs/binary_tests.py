#!/usr/bin/python
#
# Self tests for cs.binary.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Unit tests for the cs.binary module.
'''

from __future__ import absolute_import
from inspect import isclass
import sys
import unittest
from cs.py.modules import module_attributes
from . import binary as binary_module
from .binary import PacketField

class _TestPacketFields(object):
  ''' Unit tests for the cs.binary module.
  '''

  def tearDown(self):
    ''' Tear down any setup.
    '''
    pass

  def roundtrip_from_bytes(self, cls, bs):
    ''' Perform a bytes => instance => bytes round trip test.
    '''
    # bytes -> field -> bytes
    P, offset = cls.from_bytes(bs)
    self.assertEqual(
        offset, len(bs),
        "incomplete parse, stopped at offset %d: parsed=%r, unparsed=%r" %
        (offset, bs[:offset], bs[offset:])
    )
    bs2 = bytes(P)
    self.assertEqual(bs, bs2, "bytes->%s->bytes fails" % (P,))

  def roundtrip_constructor(self, cls, test_case):
    ''' Perform a cls(args) => bytes => instance round trip test.
    '''
    # *args[, kwargs[, bytes]]
    args = list(test_case)
    kwargs = {}
    transcription = None
    if args and isinstance(args[-1], bytes):
      transcription = args.pop()
    if args and isinstance(args[-1], dict):
      kwargs = args.pop()
    P = cls(*args, **kwargs)
    bs2 = bytes(P)
    if transcription is not None:
      self.assertEqual(
          bs2, transcription,
          "bytes(%s) != %r (got %r)" % (P, transcription, bs2)
      )
    P2, offset = cls.from_bytes(bs2)
    self.assertEqual(
        offset, len(bs2),
        "incomplete parse, stopped at offset %d: parsed=%r, unparsed=%r" %
        (offset, bs2[:offset], bs2[offset:])
    )
    self.assertEqual(P, P2, "%s => bytes => %s not equal" % (P, P2))

  def test_PacketField_round_trip(self):
    ''' Perform round trip tests of the PacketFields for which we have test cases.
    '''
    M = self.module
    for attr, value in sorted(module_attributes(M)):
      if isclass(value):
        if PacketField in value.__mro__:
          with self.subTest(class_name=attr):
            cls = value
            test_cases = getattr(cls, 'TEST_CASES', None)
            if test_cases is not None:
              for test_case in test_cases:
                with self.subTest(test_case=test_case):
                  ##print("test %s vs %r" % (attr, test_case),
                  ##  file=open('/dev/tty', 'w'), flush=True)
                  if isinstance(test_case, bytes):
                    # bytes -> field -> bytes
                    self.roundtrip_from_bytes(cls, test_case)
                  elif isinstance(test_case, tuple):
                    # *args[, kwargs[, bytes]]
                    self.roundtrip_constructor(cls, test_case)
                  else:
                    raise ValueError("unhandled test case: %r" % (test_case,))

class TestCSBinaryPacketFields(_TestPacketFields, unittest.TestCase):

  def setUp(self):
    ''' We're testing the cs.binary module.
    '''
    self.module = binary_module

def selftest(argv):
  ''' Run the unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
