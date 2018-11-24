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
from cs.buffer import CornuCopyBuffer
from cs.excutils import logexc
from cs.fileutils import BackedFile, ReadMixin, datafrom
from cs.logutils import warning
from cs.pfx import Pfx, PfxThread
from cs.resources import MultiOpenMixin
from cs.result import bg, Result
from cs.threads import locked, LockableMixin
from cs.x import X
from . import defaults, RLock
from .block import Block, IndirectBlock, RLEBlock
from .blockify import top_block_for, blockify, DEFAULT_SCAN_SIZE

# arbitrary threshold to generate blockmaps
AUTO_BLOCKMAP_THRESHOLD = 1024 * 1024

class ROBlockFile(RawIOBase, ReadMixin):
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

  def datafrom(self, offset):
    ''' Generator yielding natural chunks from the file commencing at offset.
        This supports the ReadMixin.read method.
    '''
    # data from the backing block
    backing_block = self.block
    if len(backing_block) >= AUTO_BLOCKMAP_THRESHOLD:
      X("ROBlockFile.datafrom: get_blockmap...")
      backing_block.get_blockmap()
    return backing_block.datafrom(offset)

class RWBlockFile(MultiOpenMixin, LockableMixin, ReadMixin):
  ''' A read/write file-like object based on cs.fileutils.BackedFile.

      An initial Block is supplied for use as the backing data.
      The .flush and .close methods return a new Block representing the commited data.

      *Note*: a RWBlockFile starts open and must be closed.
  '''

  def __init__(self, backing_block=None):
    ''' Initialise RWBlockFile with optional backing Block `backing_block`.
    '''
    if backing_block is None:
      backing_block = Block(data=b'')
    self.filename = None
    self._syncer = None     # syncing Result, close waits for it
    self._backing_block = None
    self._blockmap = None
    self._file = None
    self.flush_count = 0
    self._lock = RLock()
    MultiOpenMixin.__init__(self, lock=self._lock)
    self.open()
    self._reset(backing_block)

  def __str__(self):
    return "RWBlockFile(backing_block=%s)" % (self._backing_block,)

  def _reset(self, new_backing_block):
    ''' Discard the file contents and replace with the supplied Block, unmodified.
    '''
    old_backing_block = self._backing_block
    if old_backing_block is not new_backing_block:
      if old_backing_block is not None:
        try:
          del old_backing_block.blockmap
        except AttributeError:
          pass
      if self._file is not None:
        self._file.close()
      self._backing_block = new_backing_block
      if new_backing_block is None:
        self._file = None
      else:
        self._file = BackedFile(ROBlockFile(new_backing_block))
        self._file.flush = self.flush

  def startup(self):
    ''' Startup actions.
    '''
    pass

  def shutdown(self):
    ''' Close the RWBlockFile, return the top Block.
    '''
    B = self.sync()
    self._reset(None)
    return B

  def __len__(self):
    return len(self._file)

  @property
  @locked
  def backing_block(self):
    ''' Return the current backing block.

        Note: the backing block may be out of date with respect to any
        pending flushes; call .sync() to obtain an up to date flushed
        block.
    '''
    return self._backing_block

  @locked
  def flush(self, scanner=None, dispatch=None):
    ''' Push the current state to the Store and update the current top block.
        Return a Result which completes later.

        We dispatch the sync in the background within a lock.

        Parameters:
        * `scanner`: optional scanner for new file data to locate
          preferred block boundaries.
    '''
    if dispatch is None:
      dispatch = bg
    flushnum = self.flush_count
    self.flush_count += 1
    syncer = self._syncer
    if syncer is None:
      X("FILE FLUSH: dispatch=%s", dispatch)
      S = defaults.S
      S.open()
      syncer = self._syncer = dispatch(self._sync_file, S)
      X("FILE FLUSH: syncer=%s", syncer)
      def cleanup(R):
        with self._lock:
          if R is self._syncer:
            self._syncer = None
      syncer.notify(cleanup)
    return syncer

  def sync(self):
    ''' Dispatch a flush, return the flushed backing block.
        Wait for any flush to complete before returing the backing block.
    '''
    B = self.flush()()
    X("%s.sync: B=%s", type(self), B)
    return B

  def _sync_file(self, S):
    # worker to sync the front ranges to the Block store
    f = self._file
    while f.front_range:
      with self._lock:
        if f.front_range:
          start, end = f.front_range._spans.pop(0)
          X("FILE SYNC %s: sync span %s:%s", self, start, end)
          with S:
            new_block = file_top_block(f.front_file, start, end)
            old_backing_block = self._backing_block
            if start >= len(old_backing_block):
              # old_block + pad + new_block
              subblocks = [old_backing_block]
              pad_length = start - len(old_backing_block)
              if pad_length > 0:
                subblocks.append(RLEBlock(pad_length, b'\0'))
              subblocks.append(new_block)
              new_backing_block = IndirectBlock(subblocks=subblocks)
            else:
              end = min(end, len(old_backing_block))
              new_backing_block = old_backing_block.splice(start, end, new_block)
          self._backing_block = new_backing_block
          f.back_file = ROBlockFile(new_backing_block)
    S.close()
    return self._backing_block

  @locked
  def truncate(self, length):
    ''' Truncate the RWBlockFile to the specified `length`.
    '''
    if length < 0:
      raise ValueError("length must be >= 0, received %s" % (length,))
    if length == 0:
      self._reset(Block(data=b''))
    else:
      # let any syncers complete
      self.sync()
      cur_len = len(self)
      front_range = self._file.front_range
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
    ''' Write `data` to the RWBlockFile.
    '''
    return self._file.write(data)

  def datafrom(self, offset):
    ''' Generator yielding natural chunks from the file commencing at offset.
        This supports the ReadMixin.read method.
    '''
    ##raise RuntimeError("BANG")
    f = self._file
    front_file = f.front_file
    backing_block = self.backing_block
    if len(backing_block) >= AUTO_BLOCKMAP_THRESHOLD:
      backing_block.get_blockmap()
    for inside, span in f.front_range.slices(offset, len(self)):
      if inside:
        # data from the front file
        yield from filedata(front_file, span.start, span.end)
      else:
        # data from the backing block
        for bs in backing_block.datafrom(start=span.start, end=span.end):
          yield bs

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
    with Pfx("RWBlockFile.high_level_blocks(%s..%s)", start, end):
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
          B = file_top_block(front_file, span.start, span.end, scanner=scanner)
          yield B
          offset += len(B)
        else:
          for B in back_block.top_blocks(span.start, span.end):
            yield B
            offset += len(B)
        if offset < end:
          warning("only got data to offset %d", offset)

def filedata(f, start, end):
  ''' A generator to yield chunks of data from a file.
      These chunks don't need to be preferred-edge aligned;
      blockify() does that.
  '''
  return datafrom(f, start, maxlength=end-start)

def file_top_block(f, start, end, scanner=None):
  ''' Return a top Block for the data from an open file.
  '''
  return top_block_for(blockify(filedata(f, start, end), scanner=scanner))

if __name__ == '__main__':
  from .file_tests import selftest
  selftest(sys.argv)
