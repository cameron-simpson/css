#!/usr/bin/python
#
# Self tests for cs.venti.dir.
#       - Cameron Simpson <cs@zip.com.au> 25aug2015
#

import sys
import unittest
from cs.logutils import D, X
from cs.randutils import rand0, randblock
from cs.py3 import bytes
from . import totext
from .store import MappingStore
from .dir import FileDirent, Dir, decodeDirent, decode_Dirent_text, decodeDirents

class TestAll(unittest.TestCase):

  def setUp(self):
    self.S = MappingStore({})

  def _round_trip_Dirent(self, D):
    encoded = D.encode()
    D2, offset = decodeDirent(encoded, 0)
    self.assertEqual(D, D2)
    text_encoded = D.textencode()
    D2 = decode_Dirent_text(text_encoded)
    self.assertEqual(D, D2)

  def test00FileDirent(self):
    with self.S:
      F = FileDirent('f1')
      self._round_trip_Dirent(F)
      self.assertEqual(F.name, 'f1')

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
