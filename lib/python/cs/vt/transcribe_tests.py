#!/usr/bin/python
#
# Transcribe tests.
# - Cameron Simpson <cs@cskk.id.au>
#

import sys
import random
import unittest
from uuid import uuid4
import cs.logutils
cs.logutils.X_via_tty = True
from .hash import _Hash, Hash_SHA1
from .transcribe import _TRANSCRIBE

class TestTranscribe(unittest.TestCase):

  def setUp(self):
    random.seed()

  def test1(self):
    T = _TRANSCRIBE
    for o in (
        '',
        0, 1, 127, '""', '"abc"', '"de\\f"', '"gh\\\"i"',
        uuid4(),
        Hash_SHA1.from_chunk(
            bytes( random.randint(0, 255) for _ in range(100) )
        ),
    ):
      s = T.transcribe_s(o, None)
      self.assertIsInstance(s, str)
      o2, offset = T.parse(s)
      self.assertEqual(offset, len(s),
        "UNPARSED: len(s)=%d, offset=%d" % (len(s), offset))
      self.assertIs(type(o), type(o2))
      ##self.assertEqual(o, o2)

def selftest(argv=None):
  if argv is None:
    argv = sys.argv
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest()
