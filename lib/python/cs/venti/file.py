#!/usr/bin/python
#
# File interfaces.      - Cameron Simpson <cs@zip.com.au>
#

from __future__ import print_function, absolute_import
from io import RawIOBase
import os
import sys
from threading import Thread
from cs.logutils import Pfx, info
from .meta import Meta
from .blockify import blockFromFile
from cs.queues import IterableQueue

class File(RawIOBase):
  ''' A file interface based on io.RawIOBase.
  '''
  def __init__(self, block):
    self.isdir = False
    self.block = block
    self._offset = 0

  def __len__(self):
    return len(self.block)

  def seek(self, offset, whence=0):
    if whence == 1:
      offset += self.tell()
    elif whence == 2:
      offset += len(self)
    self._offset = offset

  def tell(self):
    return self._offset

  def read(self, n=-1):
    ''' Read up to `n` bytes in one go.
	Only bytes from the first subslice are returned, taking the
	flavour of RawIOBase, which should only make one underlying
	read system call.
    '''
    if n == -1:
      data = self.readall()
    else:
      data = ''
      for B, start, end in self.block.slices(self._offset, self._offset + n):
        data = B.data[start:end]
        break
    self._offset += len(data)
    return data

  def readinto(self, b):
    nread = 0
    for B, start, end in self.block.slices(self._offset, self._offset + len(b)):
      Blen = end - start
      b[nread:nread+Blen] = B[start:end]
      nread += Blen
    self._offset += nread
    return nread
