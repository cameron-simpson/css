#!/usr/bin/python -tt
#
# Tail text files.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import sys
import time

from cs.logutils import D

def tail(fp,
         readsize=8192, polltime=1,
         seekoffset=0, seekwhence=os.SEEK_END,
         quit_at_eof=False):
  ''' Yield whole lines from a file.
  '''
  fp.seek(seekoffset, seekwhence)
  partline = ''
  while True:
    pos = fp.tell()
    size = os.fstat(fp.fileno())[6]
    busy = False
    while size > pos:
      rsize = min(size - pos, readsize)
      data = fp.read(rsize)
      if len(data) == 0:
        break
      pos = fp.tell()
      busy = True
      fpos = 0
      while True:
        nlpos = data.find('\n', fpos)
        if nlpos < fpos:
          break
        nlpos += 1
        line = data[fpos:nlpos]
        if len(partline):
          yield partline+line
          partline = ''
        else:
          yield line
        fpos = nlpos
      partline = data[fpos:]
    if quit_at_eof:
      if len(partline):
        yield partline
      return
    if not busy:
      time.sleep(polltime)

if __name__ == '__main__':
  import unittest
  from cStringIO import StringIO

  class TestTail(unittest.TestCase):

    def setUp(self):
      pass

    def tearDown(self):
      pass

    def test01selfread(self):
      lines = open(__file__).readlines()
      tlines = [ _ for _ in tail(open(__file__), seekwhence=os.SEEK_SET, quit_at_eof=True) ]
      self.assertEquals(lines, tlines)

  unittest.main()
