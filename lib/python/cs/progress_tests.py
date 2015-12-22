#!/usr/bin/python
#
# Self tests for cs.progress.
#       - Cameron Simpson <cs@zip.com.au> 23dec2015
#

from __future__ import absolute_import
import unittest
from cs.logutils import X
from .progress import Progress

class TestProgress(unittest.TestCase):

  def test00basic(self):
    P = Progress(total=1000, start=3, position=4, start_time=100, throughput_window=60)
    self.assertEqual(P.position, 4)
    self.assertEqual(P.start, 3)
    self.assertEqual(P.start_time, 100)
    self.assertEqual(P.total, 1000)
    self.assertEqual(P.throughput_window, 60)

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('__main__')
