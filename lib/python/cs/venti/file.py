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
    ''' Initialise with Block `block`.
    '''
    self.isdir = False
    self.block = block
    self._offset = 0

  def __len__(self):
    ''' Length of the file, as the length of the backing Block.
    '''
    return len(self.block)

  def seek(self, offset, whence=0):
    ''' Set the current file offset.
    '''
    if whence == 1:
      offset += self.tell()
    elif whence == 2:
      offset += len(self)
    self._offset = offset
    return offset

  def tell(self):
    ''' Return the current file offset.
    '''
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
    ''' Read data into the bytearray `b`.
    '''
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
      The .flush and .close methods return a new Block representing the commited data.
  '''

  def __init__(self, backing_block=None):
    ''' Initialise File with optional backing Block `backing_block`.
    '''
    if backing_block is None:
      backing_block = Block(data=b'')
    self._backing_block = backing_block
    BackedFile.__init__(self, BlockFile(backing_block))

  @property
  @locked
  def backing_block(self):
    return self._backing_block

  @backing_block.setter
  @locked
  def backing_block(self, new_block):
    X("File: update _backing_block to %s from %s", new_block, self._backing_block)
    self._backing_block = new_block
    self._reset(BlockFile(new_block))

  @locked
  def flush(self):
    ''' Push the current state to the Store and update the current top block.
        Returns the new top Block.
    '''
    if not self.front_range.isempty():
      # Recompute the top Block from the current high level blocks.
      # As a side-effect of setting .backing_block we discard the
      # front file data, which are now saved to the Store.
      self.backing_block = top_block_for(self.high_level_blocks())
      # TODO: truncate the front file?
    return self.backing_block

  @locked
  def truncate(self, length):
    ''' Truncate the File to the specified `length`.
    '''
    if length < 0:
      raise ValueError("length must be >= 0, received %s" % (length,))
    cur_len = len(self)
    front_range = self.front_range
    backing_block0 = self.backing_block
    if length < cur_len:
      # shorten file
      if front_range.end > length:
        front_range.discard_span(cur_len, front_range.end)
        # the front_file should also be too big
        self.front_file.truncate(length)
      if len(backing_block0) > length:
        # new top Block built on previous Block
        # this might overlap some of the front_range but the only new blocks
        # should be the partial direct block at the end of the range, and
        # whatever new indirect blocks get made to span things
        self.backing_block \
          = top_block_for(backing_block0.top_blocks(0, length))
    elif length > cur_len:
      # extend the front_file and front_range
      self.front_file.truncate(length)
      front_range.add_span(front_range.end, length)

  @locked
  def close(self):
    ''' Close the File, return the top Block.
    '''
    B = self.flush()
    BackedFile.close(self)
    return B

  def read(self, size = -1):
    ''' Read up to `size` bytes, honouring the "single system call" spirit.
    '''
    if size == -1:
      return self.readall()
    if size < 1:
      raise ValueError("%s.read: size(%r) < 0 but not -1", self, size)
    start = self._offset
    end = start + size
    for inside, span in self.front_range.slices(start, end):
      if inside:
        # data from the front file; return the first chunk
        for chunk in filedata(self.front_file, start=span.start, end=span.end):
          self._offset += len(chunk)
          return chunk
      else:
        # data from the backing block: return the first chunk
        for B, Bstart, Bend in self.backing_block.slices(span.start, span.end):
          data = B[Bstart:Bend]
          self._offset += len(data)
          return data
    return b''

  def readall(self):
    ''' Concatenate all the data from the current offset to the end of the file.
    '''
    bss = []
    for inside, span in self.front_range.slices(self._offset, len(self)):
      if inside:
        # data from the front file; return the spanned chunks
        for chunk in filedata(self.front_file, start=span.start, end=span.end):
          self._offset += len(chunk)
          bss.append(chunk)
      else:
        # data from the backing block: return the first chunk
        for B, Bstart, Bend in self.backing_block.slices(span.start, span.end):
          chunk = B[Bstart:Bend]
          self._offset += len(chunk)
          bss.append(chunk)
    return b''.join(bss)

  @locked
  def high_level_blocks(self, start=None, end=None):
    ''' Return an iterator of new high level Blocks covering the specified data span, by default the entire current file data.
    '''
    with Pfx("File.high_level_blocks(%s..%s)", start, end):
      if start is None:
        start = 0
      if end is None:
        end = self.front_range.end
      for in_front, span in self.front_range.slices(start, end):
        if in_front:
          # blockify the new data and yield the top block
          B = top_block_for(blockify(filedata(self.front_file,
                                              start=span.start,
                                              end=span.end)))
          yield B
        else:
          for B in self.backing_block.top_blocks(span.start, span.end):
            yield B

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

def file_top_block(fp, rsize=8192, start=None, end=None):
  ''' Return a top Block for the data from an open file.
  '''
  return top_block_for(blockify(filedata(fp, rsize=rsize, start=start, end=end)))

if __name__ == '__main__':
  from cs.venti.file_tests import selftest
  selftest(sys.argv)
