#!/usr/bin/python
#
# Unit tests for cs.mp3.
#   - Cameron Simpson <cs@cskk.id.au> 06may2017
#

import sys
import os
import os.path
import unittest
from cs.buffer import CornuCopyBuffer
from .mp3 import MP3Frame, MP3AudioFrame
from cs.x import X

TESTFILE = os.environ.get('TEST_CS_MP3_TESTFILE', 'TEST.mp3')

class Test_MP3(unittest.TestCase):
  ''' Tests for `cs.mp3`.
  '''

  @unittest.skipUnless(os.path.exists(TESTFILE), 'no ' + TESTFILE)
  def test(self):
    if False:
      # to help with debugging:
      # print the first 16 sync points - some _may_ be in the audio data
      bfr = CornuCopyBuffer.from_filename(TESTFILE)
      count = 16
      while not bfr.at_eof() and count > 0:
        bs=b''.join(MP3AudioFrame.scan_for_sync(bfr))
        X("AUDIO at %d after %d bytes",bfr.offset,len(bs))
        bfr.take(1)
        count -= 1
    S = os.stat(TESTFILE)
    mp3_size = S.st_size
    bfr = CornuCopyBuffer.from_filename(TESTFILE)
    for offset, frame, post_offset in MP3Frame.scan_with_offsets(bfr):
      frame_size = post_offset - offset
      frame_bs = bytes(frame)
      ##frame2 = MP3Frame.from_bytes(frame_bs)
      ##self.assertIs(type(frame), type(frame2))
      # There used to be a round trip size check, but we repair
      # some input data and write it out correctly, so the size can
      # change. Example: a USC-2 text field missing its BOM.
    self.assertEqual(
        bfr.offset, mp3_size,
        "file size = %d, buffer offset = %d" % (mp3_size, bfr.offset)
    )
    self.assertTrue(bfr.at_eof())
    bfr.close()

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
