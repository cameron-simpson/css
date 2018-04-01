#!/usr/bin/python
#
# File interfaces.      - Cameron Simpson <cs@cskk.id.au>
#

''' Classes to present blocks as file-like objects with write support.
'''

from __future__ import print_function, absolute_import
from io import RawIOBase
from os import SEEK_SET
import sys
from threading import RLock
from cs.fileutils import BackedFile, ReadMixin
from cs.logutils import warning
from cs.pfx import Pfx, PfxThread
from cs.resources import MultiOpenMixin
from cs.threads import locked, LockableMixin
from cs.x import X
from . import defaults
from .block import Block
from .blockify import top_block_for, blockify, DEFAULT_SCAN_SIZE

# arbitrary threshold to generate blockmaps
AUTO_BLOCKMAP_THRESHOLD = 1024 * 1024

class BlockFile(RawIOBase, ReadMixin):
  ''' A read-only file interface to a Block based on io.RawIOBase.
  '''

  def __init__(self, block):
    ''' Initialise with Block `block`.
    '''
    RawIOBase.__init__(self)
    self.isdir = False
    self.block = block
    self._offset = 0

  def __len__(self):
    ''' Length of the file, as the length of the backing Block.
    '''
    return len(self.block)

  def _auto_blockmap(self):
    backing_block = self.block
    if len(backing_block) >= AUTO_BLOCKMAP_THRESHOLD:
      backing_block.get_blockmap()

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
    self._auto_blockmap()
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
    self._auto_blockmap()
    nread = 0
    for B, start, end in self.block.slices(self._offset, self._offset + len(b)):
      Blen = end - start
      b[nread:nread + Blen] = B[start:end]
      nread += Blen
    self._offset += nread
    return nread

class File(MultiOpenMixin, LockableMixin, ReadMixin):
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
    self._syncer = None     # syncing Thread, close waits for it
    self._backing_block = None
    self._blockmap = None
    self._reset(backing_block)
    self._lock = RLock()
    MultiOpenMixin.__init__(self, lock=self._lock)
    self.open()
    self.flush_count = 0

  def __str__(self):
    return "File(backing_block=%s)" % (self._backing_block,)

  def _reset(self, new_backing_block):
    old_backing_block = self._backing_block
    if old_backing_block is not new_backing_block:
      try:
        del old_backing_block.blockmap
      except AttributeError:
        pass
      self._backing_block = new_backing_block
      self._file = BackedFile(BlockFile(new_backing_block))
      self._file.flush = self.flush

  def startup(self):
    ''' Startup actions.
    '''
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
    flushnum = self.flush_count
    self.flush_count += 1
    old_file = self._file
    old_syncer = self._syncer
    # only do work if there are new data in the file or pending syncs
    if not old_syncer and not old_file.front_range:
      return
    with Pfx("%s.flush(scanner=%r)...", self.__class__.__qualname__, scanner):
      def update_store():
        ''' Commit unsynched file contents to the Store.
        '''
        # wait for previous sync to complete, if any
        if old_syncer:
          old_syncer.join()
        old_block = old_file.back_file.block
        # Recompute the top Block from the current high level blocks.
        # As a side-effect of setting .backing_block we discard the
        # front file data, which are now saved to the Store.
        with S:
          B = top_block_for(
              self._high_level_blocks_from_front_back(
                  old_file.front_file, old_block,
                  old_file.front_range,
                  scanner=scanner))
        old_file.close()
        with self._lock:
          # if we're still current, update the front settings
          if self._file is new_file:
            self._reset(B)
        S.close()
      S = defaults.S
      T = PfxThread(name="%s.flush(): update_store" % (self,),
                    target=update_store)
      # push the current state as the backing file
      # and initiate a sync to the Store
      old_file.read_only = True
      new_file = BackedFile(old_file)
      self._syncer = T
      self._file = new_file
      self._file.flush = self.flush
      S.open()
      T.start()

  def sync(self):
    ''' Dispatch a flush, return the flushed backing block.
        Wait for any flush to complete before returing the backing block.
    '''
    self.flush()
    T = self._syncer
    if T:
      T.join()
    B = self.backing_block
    return B

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

  def tell(self):
    ''' Return the file read/write position.
    '''
    return self._file.tell()

  def seek(self, offset, whence=SEEK_SET):
    ''' Adjust the file read/write position.
    '''
    return self._file.seek(offset, whence=whence)

  def write(self, data):
    ''' Write `data` to the File.
    '''
    return self._file.write(data)

  def _auto_blockmap(self):
    backing_block = self._backing_block
    if len(backing_block) >= AUTO_BLOCKMAP_THRESHOLD:
      backing_block.get_blockmap()

  def read(self, size=-1, offset=None, longread=False):
    ''' Read up to `size` bytes, honouring the "single system call" spirit.
    '''
    ##X("File.read(size=%s,offset=%s)...", size, offset)
    self._auto_blockmap()
    f = self._file
    if offset is not None:
      with self._lock:
        self.seek(offset)
        return self.read(size=size)
    if size == -1:
      return self.readall()
    if size < 1:
      raise ValueError("%s.read: size(%r) < 1 but not -1", self, size)
    start = f.tell()
    end = start + size
    chunks = []
    ##X("File.read: front_range=%s", f.front_range)
    for inside, span in f.front_range.slices(start, end):
      if inside:
        # data from the front file; return the first chunk
        for chunk in filedata(f.front_file, start=span.start, end=span.end):
          chunks.append(chunk)
          if not longread:
            break
      else:
        # data from the backing block: return the first chunk
        ##X("File.read: backing_block span=%s", span)
        for B, Bstart, Bend in self.backing_block.slices(span.start, span.end):
          chunks.append(B[Bstart:Bend])
          if not longread:
            break
    data = b''.join(chunks)
    f.seek(start + len(data))
    return data

  @locked
  def readall(self):
    ''' Concatenate all the data from the current offset to the end of the file.
    '''
    self._auto_blockmap()
    f = self._file
    offset = self.tell()
    bss = []
    for inside, span in f.front_range.slices(offset, len(self)):
      if inside:
        # data from the front file; return the spanned chunks
        for chunk in filedata(f.front_file, start=span.start, end=span.end):
          bss.append(chunk)
          offset += len(chunk)
      else:
        # data from the backing block: return the first chunk
        for B, Bstart, Bend in self.backing_block.slices(span.start, span.end):
          chunk = B[Bstart:Bend]
          bss.append(chunk)
          offset += len(chunk)
    f.seek(offset)
    return b''.join(bss)

  @locked
  def high_level_blocks(self, start=None, end=None, scanner=None):
    ''' Return an iterator of new high level Blocks covering the specified data span.
        The default is the entire current file data.
    '''
    return self._high_level_blocks_from_front_back(
        self.front_file, self.backing_block, self.front_range,
        start, end, scanner=scanner)

  @staticmethod
  def _high_level_blocks_from_front_back(
      front_file, back_block, front_range,
      start=None, end=None, scanner=None
  ):
    ''' Generator yielding high level blocks spanning the content of `front_file` and `back_block`, chosen through the filter of `front_range`.
    '''
    with Pfx("File.high_level_blocks(%s..%s)", start, end):
      if start is None:
        start = 0
      if end is None:
        end = max(front_range.end, len(back_block))
      ##X("_HLB: front_file=%s, back_block=%s, front_range=%s, start=%s, end=%s...",
      ##  front_file, back_block, front_range, start, end)
      offset = start
      for in_front, span in front_range.slices(start, end):
        if in_front:
          # blockify the new data and yield the top block
          B = top_block_for(blockify(filedata(front_file,
                                              start=span.start,
                                              end=span.end),
                                     scanner))
          yield B
          offset += len(B)
        else:
          for B in back_block.top_blocks(span.start, span.end):
            yield B
            offset += len(B)
        if offset < end:
          warning("only got data to offset %d", offset)

def filedata(fp, rsize=None, start=None, end=None):
  ''' A generator to yield chunks of data from a file.
      These chunks don't need to be preferred-edge aligned;
      blockify() does that.
  '''
  if rsize is None:
    rsize = DEFAULT_SCAN_SIZE
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

def file_top_block(fp, rsize=None, start=None, end=None):
  ''' Return a top Block for the data from an open file.
  '''
  return top_block_for(blockify(filedata(fp, rsize=rsize, start=start, end=end)))

if __name__ == '__main__':
  from .file_tests import selftest
  selftest(sys.argv)
