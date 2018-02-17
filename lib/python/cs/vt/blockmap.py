#!/usr/bin/python3
#
# Map block to leaves and offsets.
# - Cameron Simpson <cs@cskk.id.au> 07feb2018
#

'''
A flat index of leaf offsets and their hashcodes to speed data
lookup from an indirect Block. This produces memory mapped indices
to bypass the need to walk the block tree for fetch leaf data.
'''

from bisect import bisect_left
from mmap import mmap, MAP_PRIVATE, PROT_READ
import os
from struct import Struct
import sys
from tempfile import TemporaryFile
from threading import Thread
from cs.resources import RunStateMixin
from cs.x import X
from . import defaults

# the record format uses 4 byte integer offsets
# to this is the maximum (and default) scale for the mmory maps
OFFSET_SCALE = 2 ** 32

OFF_STRUCT = Struct('<L')

class _MappedFDStub:
  def __init__(self, start_offset):
    self.start = start_offset

class MappedFD:
  ''' Manage a memory map of the contents of a file representing a block's backing leaf content.
  '''

  def __init__(self, fp, mapsize, recsize, start_offset, end_offset, submap_index):
    ''' Initialise the MappedFD from a file.
        `fp`: the file whose contents will be mapped
        `recsize`: the size of each record in the file

        The file's file descriptor is dup()ed and the dup used to manage the
        memory map, allowing the original file to be closed.
    '''
    assert recsize > 4
    self.mapsize = mapsize
    self.recsize = recsize
    self.base = mapsize * submap_index
    self.start = start_offset   # absolute offset of first block
    self.end = end_offset       # absolute offset of the end of the last block
    self.submap_index = submap_index
    fd = os.dup(fp.fileno())
    self.fd = fd
    self.mmap = mmap(fd, 0, flags=MAP_PRIVATE, prot=PROT_READ)
    self.prevmap = None
    self.nextmap = None
    self.record_count = self.mmap.size() // self.recsize
    assert self.mmap.size() % self.recsize == 0, \
        "mmap.size()=%s, recsize=%s, modulus=%d" \
        % (self.mmap.size(), self.recsize, self.mmap.size() % self.recsize)

  def __del__(self):
    ''' Release resouces on object deletion.
    '''
    self.close()

  def close(self):
    ''' Release the map and its file descriptor.
    '''
    self.mmap.close()
    os.close(self.fd)

  def __len__(self):
    return self.record_count

  def __getitem__(self, i):
    ''' Fetch the offset component of the record.
    '''
    assert self.nextmap is not None
    if i < 0 or i >= self.record_count:
      raise IndexError(i)
    rec_offset = i * self.recsize
    buf = self.mmap[rec_offset:rec_offset + 4]
    value, = OFF_STRUCT.unpack(buf)
    return value

  def get_record(self, i):
    ''' Return (offset, span, hashcode) for index `i`.
    '''
    if i < 0 or i >= self.record_count:
      raise ValueError(i)
    mmap = self.mmap
    recsize = self.recsize
    rec_offset = i * recsize
    hash_offset = rec_offset + 4
    next_rec_offset = rec_offset + recsize
    offset, = OFF_STRUCT.unpack(mmap[rec_offset:hash_offset])
    hashcode = mmap[hash_offset:next_rec_offset]
    try:
      next_offset = self[i+1]
    except IndexError:
      map_base = self.base
      assert map_base % self.mapsize == 0
      next_offset = self.end - map_base
    span = next_offset - offset
    return offset, span, hashcode

  def __iter__(self):
    ''' Iterate over the map yielding (offset, span, hashcode).
    '''
    i = 0
    get_record = self.get_record
    while True:
      try:
        yield get_record(i)
      except IndexError:
        return
      i += 1

class BlockMap(RunStateMixin):
  ''' A fast mapping of offsets to leaf block hashcodes.
  '''

  def __init__(self, block, mapsize=None):
    ''' Initialise the BlockMap, dispatch the index generator.
    '''
    if mapsize is None:
      mapsize = OFFSET_SCALE
    elif mapsize <= 0 or mapsize > OFFSET_SCALE:
      raise ValueError(
          "mapsize(%d) out of range, must be >0 and <=%d"
          % (mapsize, OFFSET_SCALE))
    RunStateMixin.__init__(self)
    self.mapsize = mapsize
    self.block = block
    self.S = defaults.S
    self.maps = []
    self.mapped_to = 0
    self.recsize = 4 + self.S.hashclass.HASHLEN
    self._loaded = False
    self._worker = Thread(target=self._load_maps)
    self._worker.start()

  def join(self):
    ''' Wait for the worker to complete.
    '''
    self._worker.join()

  def __del__(self):
    ''' Release resources on object deletion.
    '''
    self.close()

  def close(self):
    ''' Release the resources associated with the BlockMap.
    '''
    self.cancel()
    self.join()
    maps = self.maps
    for i in range(len(maps)-1):
      submap = maps[i]
      if submap is not None:
        submap.close()
        maps[i] = None

  def _load_maps(self):
    ''' Walk the block tree assembling the mapping.
    '''
    with self.S:
      maps = self.maps
      recsize = self.recsize
      mapsize = self.mapsize
      submap_index = -1
      submap_fp = None
      offset = 0
      prevmap = None
      offset0 = offset
      for leaf in self.block.leaves:
        if self.cancelled:
          return
        leaf_submap_index = offset // mapsize
        leaf_submap_offset = offset % mapsize
        while submap_index < leaf_submap_index:
          if submap_index >= 0:
            # save the current map
            if submap_fp is None:
              maps.append(None)
            else:
              submap_fp.flush()
              assert submap_index == len(maps)
              newmap = MappedFD(submap_fp, mapsize, recsize, offset0, offset, len(maps))
              if prevmap is not None:
                assert prevmap.nextmap is None
                prevmap.nextmap = newmap
                newmap.prevmap = prevmap
              prevmap = newmap
              offset0 = offset
              maps.append(newmap)
              self.mapped_to = offset
              submap_fp.close()
              submap_fp = None
          submap_index += 1
        if submap_fp is None:
          submap_fp = TemporaryFile('wb')
          submap_index = leaf_submap_index
        h = leaf.hashcode
        submap_fp.write(OFF_STRUCT.pack(leaf_submap_offset))
        submap_fp.write(h)
        offset += leaf.span
      if submap_fp is not None:
        submap_fp.flush()
        newmap = MappedFD(submap_fp, mapsize, recsize, offset0, offset, len(maps))
        maps.append(newmap)
        if prevmap is not None:
          assert prevmap.nextmap is None
          prevmap.nextmap = newmap
          newmap.prevmap = prevmap
        prevmap = newmap
        submap_fp.close()
      final_submap_index = offset // mapsize
      while submap_index < final_submap_index:
        maps.append(None)
        submap_index += 1
      # dummy map on the end of the list to hold the final offset
      newmap = _MappedFDStub(offset)
      assert prevmap.nextmap is None
      prevmap.nextmap = newmap
      maps.append(newmap)
    self._loaded = True
    self.self_check()

  def self_check(self):
    ''' Perform some integrity tests.
    '''
    assert self._loaded
    maps = self.maps
    for i in range(len(maps)-1):
      submap = maps[i]
      if submap is None:
        continue
      assert isinstance(submap, MappedFD), \
          "maps[%d] is not a MappedFD: %r" % (i, type(submap))
      assert maps[i].nextmap is not None, \
          "len(maps)=%d: maps[%d].nextmap=%s" % (len(maps), i, maps[i].nextmap)
    assert isinstance(maps[-1], _MappedFDStub)

  def chunks(self, offset, span):
    ''' Generator yielding data from [offset:offset+span] from the relevant leaves.
    '''
    if offset < 0:
      raise ValueError("offset(%d) should be >= 0" % (offset,))
    if span < 0:
      raise ValueError("span(%d) should be >= 0" % (span,))
    if span == 0:
      return
    mapped_to = self.mapped_to
    if mapped_to < offset + span:
      X("PARTIALLY MAPPED: mapped_to=%d, offset=%d, span=%d", mapped_to, offset, span)
      # a portion of the span is outside the mapped range
      mapped_span = mapped_to - offset
      if mapped_span > 0:
        # a leading portion is inside the mapped range
        yield from self.chunks(offset, mapped_span)
        span -= mapped_span
        offset += mapped_span
      # fetch the unmapped data by traversing the Block tree
      X("get block.chunks: offst=%d, span=%d, offset+span=%d",
        offset, span, offset+span)
      yield from self.block.chunks(offset, offset + span)
      return
    S = self.S
    hashclass = S.hashclass
    maps = self.maps
    mapsize = self.mapsize
    submap_index = offset // mapsize
    submap_offset = offset % mapsize
    submap = maps[submap_index]
    while submap is None:
      submap_index -= 1
      submap = maps[submap_index]
    submap_base = submap.base
    i = bisect_left(submap, submap_offset)
    assert i >= 0 and i <= len(submap)
    if i == len(submap):
      assert i > 0
      i -= 1
    elif submap[i] > submap_offset:
      if i == 0:
        submap = submap.prevmap
        submap_base = submap.base
        i = len(submap) - 1
      else:
        i -= 1
    assert submap[i] <= offset
    chunks_yielded = []
    with S:
      while span > 0:
        leaf_offset, leaf_span, leaf_hashcode = submap.get_record(i)
        leaf_offset += submap_base
        start = offset - leaf_offset
        end = start + min(span, leaf_span - start)
        leaf = S[hashclass(leaf_hashcode)]
        chunk = leaf[start:end]
        chunks_yielded.append(chunk)
        yield chunk
        offset += len(chunk)
        span -= len(chunk)
        if span > 0:
          i += 1
          if i == len(submap):
            submap = submap.nextmap
            submap_base = submap.base
            i = 0

  def data(self, offset, span):
    ''' Return the data from [offset:offset+span] as a single bytes object.
    '''
    return b''.join(self.chunks(offset, span))

  def __getitem__(self, index):
    ''' Return a single byte from the BlockMap.
    '''
    if isinstance(index, int):
      return self.data(index, 1)
    raise RuntimeError("need to implement slices")

if __name__ == '__main__':
  from .blockmap_tests import selftest
  selftest(sys.argv)
