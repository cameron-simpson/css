#!/usr/bin/python
#
# Self tests for cs.binary.
#       - Cameron Simpson <cs@cskk.id.au>
#

from __future__ import absolute_import
from inspect import isclass
from io import BytesIO
import sys
import unittest
import cs.binary
from cs.binary import PacketField
from cs.randutils import rand0, randbool, randblock
from cs.serialise import get_bs, put_bs, \
                         get_bsdata, put_bsdata, \
                         get_bss, put_bss
from cs.py.modules import module_names
from cs.py3 import bytes
import cs.x
cs.x.X_via_tty = True
from cs.x import X

class TestBinary(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def test_round_trip(self):
    M = cs.binary
    for Mname in sorted(module_names(M)):
      X("Mname = %r", Mname)
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
                    bs = test_case
                    P, offset = cls.from_bytes(bs)
                    self.assertEqual(
                        offset, len(bs),
                        "incomplete parse of %r: total length=%d, offset=%d"
                        % (bs, len(bs), offset))
                    bs2 = bytes(P)
                    self.assertEqual(
                        bs, bs2,
                        "bytes->%s->bytes fails" % (P,))
                  elif isinstance(

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
