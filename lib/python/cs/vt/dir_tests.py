#!/usr/bin/python
#
# Dir tests.
#       - Cameron Simpson <cs@cskk.id.au> 25aug2015
#

from random import shuffle
import sys
import unittest
from cs.randutils import randbool
from .dir import FileDirent, Dir, decode_Dirent
from .paths import decode_Dirent_text
from .store import MappingStore

class TestAll(unittest.TestCase):

  def setUp(self):
    self.S = MappingStore("TestAll", {})
    self.S.open()

  def tearDown(self):
    self.S.close()

  def _round_trip_Dirent(self, D):
    encoded = D.encode()
    D2, offset = decode_Dirent(encoded, 0)
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
          dofile = randbool()
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
