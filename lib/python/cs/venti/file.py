#!/usr/bin/python
#
# File interfaces.      - Cameron Simpson <cs@zip.com.au>
#

from __future__ import print_function, absolute_import
from io import RawIOBase
from os import SEEK_SET
import sys
from threading import RLock
from cs.fileutils import BackedFile, ReadMixin
from cs.pfx import Pfx, PfxThread, XP
from cs.resources import MultiOpenMixin
from cs.threads import locked, LockableMixin
from . import defaults
from .block import Block
from .blockify import top_block_for, blockify

class BlockFile(RawIOBase,ReadMixin):
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
      b[nread:nread + Blen] = B[start:end]
      nread += Blen
    self._offset += nread
    return nread

class File(MultiOpenMixin,LockableMixin,ReadMixin):
  ''' A read/write file-like object based on cs.fileutils.BackedFile.
      An initial Block is supplied for use as the backing data.
      The .flush and .close methods return a new Block representing the commited data.
      Note that a File starts open and must be closed.
  '''

  def __init__(self, backing_block=None):
    ''' Initialise File with optional backing Block `backing_block`.
    '''
    if backing_block is None:
      backing_block = Block(data=b'')
    self.filename = None
    self._syncer = None # syncing Thread, close waits for it
    self._reset(backing_block)
    self._lock = RLock()
    MultiOpenMixin.__init__(self, lock=self._lock)
    self.open()

  def __str__(self):
    return "File(backing_block=%s)" % (self._backing_block,)

  def _reset(self, new_backing_block):
    self._backing_block = new_backing_block
    self._file = BackedFile(BlockFile(new_backing_block))

  def startup(self):
    pass

  def shutdown(self):
    ''' Close the File, return the top Block.
    '''
    B = self.sync()
    return B

  def __len__(self):
    return len(self._file)

  @property
  @locked
  def backing_block(self):
    ''' Return the current backing block.
        The backing block may be out of date with respect to any
        pending flushes; call .sync() to obtain an up to date flushed
        block.
    '''
    return self._backing_block

  @locked
  def flush(self, scanner=None):
    ''' Push the current state to the Store and update the current top block.
        We dispatch the sync in the background within a lock.
        `scanner`: optional scanner for new file data to locate preferred block boundaries.
    '''
    with Pfx("%s.flush(scanner=%r)...", self.__class__.__qualname__, scanner):
      old_file = self._file
      if not old_file.front_range:
        XP("empty front_range, no action")
      else:
        # only do work if there are new data in the file
        XP("front_range=%s", old_file.front_range)
        # push the current state as the backing file
        # and initiate a sync to the Store
        old_file.read_only = True
        old_syncer = self._syncer
        new_file = BackedFile(old_file)
        S = defaults.S
        with S:
          def update_store():
            # Recompute the top Block from the current high level blocks.
            # As a side-effect of setting .backing_block we discard the
            # front file data, which are now saved to the Store.
            with S:
              XP("File.update_store: syncing to Store...")
              B = top_block_for(
                    self._high_level_blocks_from_front_back(
                        old_file.front_file, self.backing_block,
                        old_file.front_range,
                        scanner=scanner))
            old_file.close()
            XP("File.update_store: syncing to Store: stored")
            with self._lock:
              if self._file is old_file:
                XP("File.update_store: syncing to Store: still using old _file, update to use new stored Block")
                self._reset(B)
              else:
                XP("File.update_store: self has moved on, do not update _file")
            if old_syncer:
              XP("File.update_store: wait for previous _syncher...")
              old_syncer.join()
            XP("File.update_store: syncing to Store DONE")
          T = PfxThread(name="%s.flush(): update_store" % (self,),
                        target=update_store)
          T.start()
        self._syncher = T
        self._file = new_file
    XP("DONE")

  def sync(self):
    ''' Dispatch a flush, return the flushed backing block.
        Wait for any flush to complete before returing the backing block.
    '''
    self.flush()
    T = self._syncer
    if T:
      T.join()
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

  def seek(self, offset, whence=SEEK_SET):
    return self._file.seek(offset, whence=whence)

  @locked
  def read(self, size=-1, offset=None):
    ''' Read up to `size` bytes, honouring the "single system call" spirit.
    '''
    if offset is not None:
      with self._lock:
        self.seek(offset)
        return self.read(size=size)
    if size == -1:
      return self.readall()
    if size < 1:
      raise ValueError("%s.read: size(%r) < 1 but not -1", self, size)
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

  @locked
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
  def high_level_blocks(self, start=None, end=None, scanner=None):
    ''' Return an iterator of new high level Blocks covering the specified data span, by default the entire current file data.
    '''
    return self._high_level_blocks_from_front_back(
                  self.front_file, back_block, self.front_range,
                  start, end, scanner=scanner)

  @staticmethod
  def _high_level_blocks_from_front_back(
        front_file, back_block, front_range,
        start=None, end=None, scanner=None):
    ''' Generator yielding high level blocks spanning the content of `front_file` and `back_block`, chosen through the filter of `front_range`.
    '''
    with Pfx("File.high_level_blocks(%s..%s)", start, end):
      if start is None:
        start = 0
      if end is None:
        end = front_range.end
      ##X("_HLB: front_file=%s, back_block=%s, front_range=%s, start=%s, end=%s...",
      ##  front_file, back_block, front_range, start, end)
      for in_front, span in front_range.slices(start, end):
        if in_front:
          # blockify the new data and yield the top block
          B = top_block_for(blockify(filedata(front_file,
                                              start=span.start,
                                              end=span.end),
                                     scanner))
          yield B
        else:
          for B in back_block.top_blocks(span.start, span.end):
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
