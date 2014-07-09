#!/usr/bin/python
#
# File interfaces.      - Cameron Simpson <cs@zip.com.au>
#

from __future__ import print_function, absolute_import
from io import RawIOBase
import os
import sys
from threading import Thread
from cs.threads import locked
from cs.logutils import Pfx, info, X
from cs.fileutils import BackedFile
from cs.queues import IterableQueue
from .meta import Meta
from .block import Block
from .blockify import top_block_for, blockify

class BlockFile(RawIOBase):
  ''' A read-only file interface to a Block based on io.RawIOBase.
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
      data = b''
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

class File(BackedFile):
  ''' A read/write file-like object based on cs.fileutils.BackedFile.
      An initial Block is supplied for use as the backing data.
      The .sync and .close methods return a new Block representing the commited data.
  '''

  def __init__(self, backing_block=None):
    if backing_block is None:
      backing_block = Block(data=b'')
    self.backing_block = backing_block
    BackedFile.__init__(self, BlockFile(backing_block))

  @locked
  def sync(self):
    ''' Commit the current state to the Store and update the current top block.
        Returns the new top Block.
    '''
    if not self.front_range.isempty():
      # recompute the top Block from the current high level blocks
      # discard the current changes, not saved to the Store
      self.backing_block = top_block_for(self.high_level_blocks())
      self._discard_front_file()
    return self.backing_block

  @locked
  def high_level_blocks(self):
    ''' Return an iterator of new high level Blocks covering the current file data.
    '''
    for inside, span in self.front_range.slices(0, self.front_range.end):
      if inside:
        # blockify the new data and yield the top block
        yield top_block_for(blockify(filedata(self.front_file,
                                              start=span.start,
                                              end=span.end)))
      else:
        # yield high level blocks and new partial Blocks
        # from the old data
        for B, start, end in self.backing_block.top_slices(span.start, span.end):
          if start == 0 and end == len(B):
            # an extant high level block
            yield B
          else:
            # should be a new partial block
            if B.indirect:
              raise RuntimeError("got slice for partial Block %s start=%r end=%r but Block is indirect! should be a partial leaf" % (B, start, end))
            yield Block(data=B[start:end])

def filedata(fp, rsize=8192, start=None, end=None):
  ''' A generator to yield chunks of data from a file.
      These chunks don't need to be preferred-edge aligned;
      blockify() does that.
  '''
  if start is None:
    pos = fp.tell()
  else:
    pos = start
    fp.seek(pos)
  while end is None or pos < end:
    if end is None:
      toread = rsize
    else:
      toread = min(rsize, end - pos)
    data = fp.read(toread)
    if len(data) == 0:
      break
    pos += len(data)
    yield data

if __name__ == '__main__':
  from cs.venti.file_tests import selftest
  selftest(sys.argv)
