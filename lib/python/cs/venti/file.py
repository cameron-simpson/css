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

class ReadFile(RawIOBase):
  ''' A read-only file interface supporting seek(), read(), readline(),
      readlines() and tell() methods.
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
      return self.readall()
    for B, start, end in self.block.slices(self._offset, self._offset + n):
      return B.data[start:end]

  def readinto(self, b):
    nread = 0
    for B, start, end in self.block.slices(self._offset, self._offset + len(b)):
      Blen = end - start
      b[nread:nread+Blen] = B[start:end]
      nread += Blen
    return nread

class WriteNewFile:
  ''' A File-like class that supplies only write, close, flush.
      flush() forces any unstored data to the store.
      close() flushes all data and returns a BlockRef for the whole file.
  '''
  def __init__(self):
    self.__sink=IterableQueue(1)
    self.__topRef=Q1()
    self.__closed=False
    self.__drain=Thread(target=self.__storeBlocks,kwargs={'S':S})
    self.__drain.start()
    atexit.register(self.__cleanup)

  def __cleanup(self):
    if not self.__closed:
      self.close()

  def write(self,data):
    self.__sink.put(data)

  def flush(self):
    # TODO: flush unimplemented, should get an intermediate topblockref
    return None

  def close(self):
    assert not self.__closed
    self.__closed=True
    self.__sink.close()
    return self.__topRef.get()

  def __storeBlocks(self):
    self.__topRef.put(topIndirectBlock(blocksOf(self.__sink)))

class WriteOverFile:
  ''' A File-like class that overwrites an existing
  '''
  def __init__(self):
    raise NotImplementedError
