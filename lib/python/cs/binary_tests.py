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
import cs.binary
from cs.binary import PacketField
from cs.py.modules import module_names

class TestBinary(unittest.TestCase):
  ''' Unit tests for the cs.binary module.
  '''

  def setUp(self):
    ''' Do any set up.
    '''
    pass

  def tearDown(self):
    ''' Tear down any setup.
    '''
    pass

  def roundtrip_from_bytes(self, cls, bs):
    # bytes -> field -> bytes
    P, offset = cls.from_bytes(bs)
    self.assertEqual(
        offset, len(bs),
        "incomplete parse, stopped at offset %d: parsed=%r, unparsed=%r"
        % (offset, bs[:offset], bs[offset:]))
    bs2 = bytes(P)
    self.assertEqual(
        bs, bs2,
        "bytes->%s->bytes fails" % (P,))

  def roundtrip_constructor(self, cls, test_case):
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
          "bytes(%s) != %r (got %r)"
          % (P, transcription, bs2))
    P2, offset = cls.from_bytes(bs2)
    self.assertEqual(
        offset, len(bs2),
        "incomplete parse, stopped at offset %d: parsed=%r, unparsed=%r"
        % (offset, bs2[:offset], bs2[offset:]))
    self.assertEqual(
        P, P2,
        "%s => bytes => %s not equal"
        % (P, P2))

  def test_PacketField_round_trip(self):
    ''' Perform round trip tests of the PacketFields for which we have test cases.
    '''
    M = cs.binary
    for Mname in sorted(module_names(M)):
      o = getattr(M, Mname, None)
      if isclass(o):
        if PacketField in o.__mro__:
          with self.subTest(class_name=Mname):
            cls = o
            test_cases = getattr(cls, 'TEST_CASES', None)
            if test_cases is not None:
              for test_case in test_cases:
                with self.subTest(test_case=test_case):
                  if isinstance(test_case, bytes):
                    # bytes -> field -> bytes
                    self.roundtrip_from_bytes(cls, test_case)
                  elif isinstance(test_case, tuple):
                    # *args[, kwargs[, bytes]]
                    self.roundtrip_constructor(cls, test_case)
                  else:
                    raise ValueError("unhandled test case: %r" % (test_case,))

def selftest(argv):
  ''' Run the unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
