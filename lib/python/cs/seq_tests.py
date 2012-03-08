#!/usr/bin/python
#
# Self tests for cs.seq.
#       - Cameron Simpson <cs@zip.com.au>
#

from StringIO import StringIO
import sys
import unittest
from cs.seq import imerge

class TestSeq(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def testIMerge(self):
    self.assertEqual( list(imerge([1,2,3],[1,4,7],[2,5,6])),
                      [1,1,2,2,3,4,5,6,7]
                    )

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
