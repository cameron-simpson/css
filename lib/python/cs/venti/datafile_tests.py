#!/usr/bin/python
#
# Self tests for cs.venti.datafile.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.venti.datafile import DataFile

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

  def test00store1(self):
    ''' Save a single block.
    '''
    self.data.saveData(self._genblock())

  def test01fetch1(self):
    ''' Save and the retrieve a single block.
    '''
    self.data.saveData(self._genblock())
    self.data.close()
    self.data.readData(0)

  def test02randomblocks(self):
    ''' Save 100 random blocks, close, retrieve in random order.
    '''
    import random
    blocks = {}
    for _ in range(100):
      data = self._genblock()
      offset = self.data.saveData(data)
      blocks[offset] = data
    self.data.close()
    offsets = blocks.keys()
    random.shuffle(offsets)
    for offset in offsets:
      data = self.data.readData(offset)
      self.assertTrue(data == blocks[offset])

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
