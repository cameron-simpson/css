#!/usr/bin/python
#
# Self tests for cs.progress.
#       - Cameron Simpson <cs@cskk.id.au> 23dec2015
#

from __future__ import absolute_import
import time
import unittest
from .progress import Progress, DEFAULT_THROUGHPUT_WINDOW

class TestProgress(unittest.TestCase):
  ''' Test `cs.progress.Progress`.

      TODO: generalise for `OverProgress`.
  '''

  def test00basic(self):
    P = Progress(
        total=1000, start=3, position=4, start_time=100, throughput_window=60
    )
    self.assertEqual(P.position, 4)
    self.assertEqual(P.start, 3)
    self.assertEqual(P.start_time, 100)
    self.assertEqual(P.total, 1000)
    self.assertEqual(P.throughput_window, 60)

  def test01defaults(self):
    P = Progress()
    self.assertEqual(P.position, 0)
    self.assertEqual(P.start, 0)
    self.assertLessEqual(P.start_time, time.time())
    self.assertIsNone(P.total)
    self.assertEqual(P.throughput_window, DEFAULT_THROUGHPUT_WINDOW)
    P2 = Progress(5)
    self.assertEqual(P2.position, 5)
    self.assertEqual(P2.start, 5)

  def test02intlike(self):
    x = 3
    x += 2
    self.assertEqual(x, 5)
    x = Progress(x)
    self.assertEqual(int(x), 5)
    self.assertEqual(x, 5)
    x += 3
    self.assertEqual(x, 8)
    x -= 1
    self.assertEqual(x, 7)
    self.assertLess(x, 8)

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('__main__')
