#!/usr/bin/python
#

''' Classes to support access to Beyonwiz TVWiz data structures.
'''

import os.path
import struct
from cs.logutils import Pfx, error, setup_logging

class Trunc(object):
  ''' A parser for the "trunc" file in a TVWiz directory.
      It is iterable, yielding tuples:
        wizOffset, fileNum, flags, offset, size
      as described at:
        http://openwiz.org/wiki/Recorded_Files#trunc_file
  '''
  def __init__(self, path):
    self.__path = path

  def __iter__(self):
    ''' The iterator to yield record tuples.
    '''
    fp = open(self.__path)
    while True:
      buf = fp.read(24)
      if len(buf) == 0:
        break
      assert len(buf) == 24
      yield struct.unpack("<QHHQL", buf)

class TVWiz(object):
  def __init__(self, wizdir):
    self.__dir = wizdir

  def trunc(self):
    ''' Obtain a Trunc object for this TVWiz dir.
    '''
    return Trunc(os.path.join(self.__dir, "trunc"))

  def data(self):
    ''' A generator that yields MPEG2 data from the stream.
    '''
    with Pfx("data(%s)" % (self.__dir,)):
      T = self.trunc()
      lastFileNum = None
      for wizOffset, fileNum, flags, offset, size in T:
        if lastFileNum is None or lastFileNum != fileNum:
          if lastFileNum is not None:
            fp.close()
          fp = open(os.path.join(self.__dir, "%04d"%fileNum))
          filePos = 0
          lastFileNum = fileNum
        if filePos != offset:
          fp.seek(offset)
        while size > 0:
          rsize = min(size, 8192)
          buf = fp.read(rsize)
          assert len(buf) <= rsize
          if len(buf) == 0:
            error("%s: unexpected EOF", fp)
            break
          yield buf
          size -= len(buf)
      if lastFileNum is not None:
        fp.close()

  def copyto(self, output):
    ''' Transcribe the uncropped content to a file named by output.
    '''
    if type(output) is str:
      with open(output, "w") as out:
        self.copyto(out)
    else:
      for buf in self.data():
        output.write(buf)
