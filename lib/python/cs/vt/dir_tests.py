#!/usr/bin/python
#
# Dir tests.
#       - Cameron Simpson <cs@cskk.id.au> 25aug2015
#

''' Unit tests for cs.vt.dir.
'''

from random import shuffle
import sys
import unittest
from cs.randutils import randbool
from .dir import FileDirent, Dir, _Dirent
from .store import MappingStore
from .transcribe import parse

class TestAll(unittest.TestCase):
  ''' Tests for _Dirent and subclasses.
  '''

  def setUp(self):
    ''' Make a dict based Store for testing.
    '''
    self.S = MappingStore("TestAll", {})
    self.S.open()

  def tearDown(self):
    ''' Close the scratch Store.
    '''
    self.S.close()

  def _round_trip_Dirent(self, D):
    ''' Round trip the binary encode/decode.
    '''
    encoded = D.encode()
    D2, offset = _Dirent.from_bytes(encoded)
    self.assertEqual(offset, len(encoded))
    self.assertEqual(D, D2)
    Ds = str(D)
    D2, offset = parse(Ds)
    self.assertEqual(offset, len(Ds))
    self.assertEqual(D, D2, "%s != %s" % (str(D), str(D2)))

  def test00FileDirent(self):
    ''' Trite FileDirent test.
    '''
    with self.S:
      F = FileDirent('test00')
      self._round_trip_Dirent(F)
      self.assertEqual(F.name, 'test00')

  def test01Dir(self):
    ''' Trite Dir test.
    '''
    with self.S:
      D = Dir('test01')
      self._round_trip_Dirent(D)
      self.assertEqual(D.name, 'test01')

  def test02DirRandomNames(self):
    ''' Add random entries to a Dir.
    '''
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
        D.snapshot()
        self._round_trip_Dirent(D)
      # check that all nodes are listed as expected
      entries = dirnodes + filenodes
      shuffle(entries)
      for E in entries:
        self.assertIn(E.name, D)
        E2 = D[E.name]
        self.assertEqual(E, E2)

def selftest(argv):
  ''' Run the unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
