#!/usr/bin/python -tt
#

import os
import sys
import time
from cs.misc import TODO

class Tail:
  TODO("tail -F behaviour to follow logrotates")
  def __init__(self,fp,bsize=8192,polltime=1):
    assert bsize > 0
    self.__bsize=bsize
    self.__polltime=polltime
    if type(fp) is str:
      fp=open(fp)
      fp.seek(0,os.SEEK_END)
    self.__fp=fp
    self.__pos=fp.tell()
  def __iter__(self):
    ''' Yield whole lines from the file.
    '''
    partline=''
    while True:
      ##print >>sys.stderr, "T1"
      size=os.fstat(self.__fp.fileno())[6]
      busy=False
      while size > self.__pos:
        ##print >>sys.stderr, "T2"
        rsize=min(size-self.__pos, self.__bsize)
        s=self.__fp.read(rsize)
        if len(s) == 0:
          print >>sys.stderr, "%s: size=%d, self.__pos=%s, read(%d) got 0 bytes" % (fp, size, self.__pos, rsize)
          break
        self.__pos=self.__fp.tell()
        busy=True
        nlpos=s.find('\n')
        while nlpos >= 0:
          ##print >>sys.stderr, "T3"
          line=s[:nlpos+1]
          s=s[nlpos+1:]
          if len(partline) > 0:
            line=partline+line
            partline=''
          yield line
          nlpos=s.find('\n')
      if not busy:
        ##print >>sys.stderr, "sleep(1)"
        time.sleep(self.__polltime)
