#!/usr/bin/python
#
# Self tests for cs.venti.dir.
#       - Cameron Simpson <cs@zip.com.au> 25aug2015
#

from random import shuffle
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
      F = FileDirent('test00')
      self._round_trip_Dirent(F)
      self.assertEqual(F.name, 'test00')

  def test01Dir(self):
    with self.S:
      D = Dir('test01')
      self._round_trip_Dirent(D)
      self.assertEqual(D.name, 'test01')

  def test02DirRandomNames(self):
      # add random nodes
      with self.S:
        D = Dir('test02')
        self._round_trip_Dirent(D)
        dirnodes = []
        filenodes = []
        ordinals = list(range(16))
        shuffle(ordinals)
        for n in ordinals:
          dofile = True if rand0(1) == 0 else False
          if dofile:
            name = 'file' + str(n)
            E = FileDirent(name)
            filenodes.append(E)
          else:
            name = 'dir' + str(n)
            E = Dir(name)
            dirnodes.append(E)
          self._round_trip_Dirent(E)
          D.add(E)
          self._round_trip_Dirent(D)
          self._round_trip_Dirent(E)
        # check that all nodes are listed as expected
        entries = dirnodes + filenodes
        shuffle(entries)
        for E in entries:
          self.assertIn(E.name, D)
          E2 = D[E.name]
          self.assertEqual(E, E2)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
