#!/usr/bin/python
#
# Self tests for cs.venti.datafile.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.venti.datafile import DataFile, DataDir

class TestAll(unittest.TestCase):

  def setUp(self):
    import os
    import random
    import tempfile
    tfd, pathname = tempfile.mkstemp(prefix="cs.venti.datafile.test", suffix=".vtd", dir='.')
    os.close(tfd)
    self.pathname = pathname
    self.data = DataFile(pathname)
    random.seed()

  def tearDown(self):
    import os
    self.data.close()
    os.remove(self.pathname)

  # TODO: tests:
  #   scan datafile

  def _genblock(self, maxsize=16383):
    import os
    import random
    return os.urandom(random.randint(0, maxsize))

  def test000IndexEntry(self):
    import random
    for count in range(100):
      rand_n = random.randint(0, 65536)
      rand_offset = random.randint(0, 65536)
      n, offset = DataDir.decodeIndexEntry(DataDir.encodeIndexEntry(rand_n, rand_offset))
      self.assertEqual(rand_n, n)
      self.assertEqual(rand_offset, offset)

  def test00store1(self):
    ''' Save a single block.
    '''
    self.data.savedata(self._genblock())

  def test01fetch1(self):
    ''' Save and the retrieve a single block.
    '''
    self.data.savedata(self._genblock())
    self.data.close()
    self.data.readdata(0)

  def test02randomblocks(self):
    ''' Save 100 random blocks, close, retrieve in random order.
    '''
    import random
    blocks = {}
    for _ in range(100):
      data = self._genblock()
      offset = self.data.savedata(data)
      blocks[offset] = data
    self.data.close()
    offsets = blocks.keys()
    random.shuffle(offsets)
    for offset in offsets:
      data = self.data.readdata(offset)
      self.assertTrue(data == blocks[offset])

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
