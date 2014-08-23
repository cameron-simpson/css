#!/usr/bin/python
#
# Self tests for cs.venti.datafile.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import sys
import random
import tempfile
import unittest
from cs.logutils import D
from .datafile import DataFile, DataDir

def genblock( maxsize=16383):
  ''' Generate a pseudorandom block of data.
  '''
  return os.urandom(random.randint(0, maxsize))

class TestDataFile(unittest.TestCase):

  def setUp(self):
    tfd, pathname = tempfile.mkstemp(prefix="cs.venti.datafile.test", suffix=".vtd", dir='.')
    os.close(tfd)
    self.pathname = pathname
    self.data = DataFile(pathname)
    random.seed()

  def tearDown(self):
    os.remove(self.pathname)

  # TODO: tests:
  #   scan datafile

  def test00store1(self):
    ''' Save a single block.
    '''
    with self.data:
      self.data.savedata(genblock())

  def test01fetch1(self):
    ''' Save and the retrieve a single block.
    '''
    with self.data:
      self.data.savedata(genblock())
    self.data.readdata(0)

  def test02randomblocks(self):
    ''' Save 100 random blocks, close, retrieve in random order.
    '''
    import random
    blocks = {}
    with self.data:
      for _ in range(100):
        data = genblock()
        offset = self.data.savedata(data)
        blocks[offset] = data
    offsets = list(blocks.keys())
    random.shuffle(offsets)
    with self.data:
      for offset in offsets:
        data = self.data.readdata(offset)
        self.assertTrue(data == blocks[offset])

class TestDataDir(unittest.TestCase):

  def test000IndexEntry(self):
    for count in range(100):
      rand_n = random.randint(0, 65536)
      rand_offset = random.randint(0, 65536)
      n, offset = DataDir.decodeIndexEntry(DataDir.encodeIndexEntry(rand_n, rand_offset))
      self.assertEqual(rand_n, n)
      self.assertEqual(rand_offset, offset)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
