#!/usr/bin/python -tt
#
# Tail text files.
#       - Cameron Simpson <cs@cskk.id.au>
#

import os
import sys
import time
from cs.logutils import D, error, warning, info

DEFAULT_READSIZE = 16384

def tail(fp,
         readsize=None, polltime=1,
         seekoffset=0, seekwhence=os.SEEK_END,
         quit_at_eof=False,
         follow_name=None):
  ''' Yield whole lines from a file.
  '''
  if readsize is None:
    readsize = DEFAULT_READSIZE
  fp.seek(seekoffset, seekwhence)
  partline = ''
  while True:
    pos = fp.tell()
    fs = os.fstat(fp.fileno())
    size = fs.st_size
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
      if follow_name is not None:
        try:
          s = os.stat(follow_name)
        except OSError as e:
          warning("%s: stat: %s", follow_name, e)
        else:
          if s.st_ino != fs.st_ino or s.st_dev != fs.st_dev:
            info("switching to new file: %s", follow_name)
            try:
              nfp = open(follow_name)
            except OSError as e:
              warning("%s: open: %s", follow_name, e)
            else:
              close(fp)
              fp = nfp
              nfp = None

if __name__ == '__main__':
  import unittest
  from io import StringIO

  class TestTail(unittest.TestCase):

    def setUp(self):
      pass

    def tearDown(self):
      pass

    def test01selfread(self):
      with open(__file__) as src:
        lines = src.readlines()
      with open(__file__) as src:
        tlines = [ _ for _ in tail(src, seekwhence=os.SEEK_SET, quit_at_eof=True) ]
      self.assertEqual(lines, tlines)

  unittest.main()
