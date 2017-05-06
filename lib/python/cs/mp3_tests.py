#!/usr/bin/python
#
# Unit tests for cs.mp3.
#   - Cameron Simpson <cs@zip.com.au> 06may2017
#

from __future__ import print_function
import sys
import os
import os.path
import unittest
from .fileutils import read_from
from .logutils import D, X
from .mp3 import frames_from_chunks

TESTFILE = 'TEST.mp3'

class Test_MP3(unittest.TestCase):

  @unittest.skipUnless(os.path.exists(TESTFILE), 'no ' + TESTFILE)
  def test(self):
    S = os.stat(TESTFILE)
    mp3_size = S.st_size
    with open(TESTFILE, 'rb') as mp3fp:
      total_size = 0
      for frame in frames_from_chunks(read_from(mp3fp)):
        total_size += len(frame)
    self.assertEqual(total_size, mp3_size,
                     "file size = %d, frames total = %d" % (mp3_size, total_size))

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
