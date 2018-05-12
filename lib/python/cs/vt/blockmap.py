#!/usr/bin/python3
#
# Map block to leaves and offsets.
# - Cameron Simpson <cs@cskk.id.au> 07feb2018
#

'''
A flat index of leaf offsets and their hashcodes to speed data
lookup from an indirect Block. This produces memory mapped indices
to bypass the need to walk the block tree to fetch leaf data.
'''

from bisect import bisect_left
from mmap import mmap, MAP_PRIVATE, PROT_READ
import os
from os.path import isdir, exists as pathexists, join as joinpath
from struct import Struct
import sys
from time import time
from tempfile import TemporaryFile, NamedTemporaryFile
from threading import Thread, Lock
from cs.excutils import logexc
from cs.logutils import warning
from cs.pfx import Pfx
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
    ''' Initialise a MappedFD from a file.
        `f`: the file whose contents will be mapped
          This may be an open file object or the path to a persistent map file.
        `mapsize`: the span covered by a submap
        `recsize`: the size of each record in the file
        `start_offset`: the offset of the first leaf
        `end_offset`: the offset of the end of the last leaf
        `submap_index`: the index of this map
        If `f` is a file path it is opened for read.
        If `f` is an open file, the file's file descriptor is dup()ed
        and the dup used to manage the memory map, allowing the
        original file to be closed by the caller.
    '''
    assert recsize > OFF_STRUCT.size
    self.mapsize = mapsize
    self.recsize = recsize
    self.base = mapsize * submap_index
    self.start = start_offset   # absolute offset of first block
    self.end = end_offset       # absolute offset of the end of the last block
    self.submap_index = submap_index
    self.prevmap = None
    self.nextmap = None
    if isinstance(f, str):
      with Pfx("open(%r)", f):
        fd = os.open(f, os.O_RDONLY)
    else:
      # `f` should be an open file
      with Pfx("dup(%r.fileno())", f):
        fd = os.dup(f.fileno())
    self.fd = fd
    self.mapped = mmap(fd, 0, flags=MAP_PRIVATE, prot=PROT_READ)
    self.record_count = self.mapped.size() // self.recsize
    assert self.mapped.size() % self.recsize == 0, \
        "mapped.size()=%s, recsize=%s, modulus=%d" \
        % (self.mapped.size(), self.recsize, self.mapped.size() % self.recsize)

  def __del__(self):
    ''' Release resouces on object deletion.
    '''
    self.close()

  def close(self):
    ''' Release the map and its file descriptor.
    '''
    self.mapped.close()

  def __len__(self):
    return self.record_count

  def __getitem__(self, i):
    ''' Fetch the offset component of the record.
    '''
    assert self.nextmap is not None
    if i < 0 or i >= self.record_count:
      raise IndexError(i)
    rec_offset = i * self.recsize
    hash_offset = rec_offset + OFF_STRUCT.size
    buf = self.mapped[rec_offset:hash_offset]
    value, = OFF_STRUCT.unpack(buf)
    return value

  def get_record(self, i):
    ''' Return (offset, span, hashcode) for index `i`.
    '''
    if i < 0 or i >= self.record_count:
      raise ValueError(i)
    mapped = self.mapped
    recsize = self.recsize
    rec_offset = i * recsize
    hash_offset = rec_offset + OFF_STRUCT.size
    next_rec_offset = rec_offset + recsize
    offset, = OFF_STRUCT.unpack(mapped[rec_offset:hash_offset])
    hashcode = mapped[hash_offset:next_rec_offset]
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

  def __init__(self, block, mapsize=None, base_mappath=None):
    ''' Initialise the BlockMap, dispatch the index generator.
        `block`: the source Block
        `mapsize`: the size of each index map, default `OFFSET_SCALE`
        `base_mappath`: the pathname for persistent storage of BlockMaps
    '''
    from .block import _IndirectBlock
    if not isinstance(block, _IndirectBlock):
      raise TypeError("block needs to be a _IndirectBlock, got a %s instead" % (type(block),))
    hashcode = block.superblock.hashcode
    if mapsize is None:
      mapsize = OFFSET_SCALE
    elif mapsize <= 0 or mapsize > OFFSET_SCALE:
      raise ValueError(
          "mapsize(%d) out of range, must be >0 and <=%d"
          % (mapsize, OFFSET_SCALE))
    # DEBUGGING
    if base_mappath is None:
      base_mappath = '/Users/cameron/hg/css-venti/test_blockmaps'
      X("BlockMap: set base_mappath to %r (was None)", base_mappath)
    RunStateMixin.__init__(self)
    self.mapsize = mapsize
    self.mappath = mappath = joinpath(base_mappath, "mapsize:%d" % (mapsize,), hashcode.filename)
    if mappath:
      if not isdir(mappath):
        with Pfx("makedirs(%r)", mappath):
          X("MKDIR %r", mappath)
          os.makedirs(mappath)
    self.block = block
    self.S = defaults.S
    self.maps = [_MappedFDStub(0)]
    self.mapped_to = 0
    self.recsize = OFF_STRUCT.size + len(hashcode)
    self._loaded = False
    self._load_lock = Lock()
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
    X("BlockMap.close...")
    self.cancel()
    self.join()
    maps = self.maps
    for i in range(len(maps)-1):
      submap = maps[i]
      if submap is not None:
        submap.close()
        maps[i] = None

  @logexc
  def _load_maps(self):
    ''' Walk the block tree assembling the mapping.
    '''
    X("_load_maps for %s (self.mappath=%r) ...", self.block, self.mappath)
    start_time = time()
    nleaves = 0
    with self.S:
      maps = self.maps
      recsize = self.recsize
      mapsize = self.mapsize
      # current submap index, current open submap file
      submap_index = -1
      submap_fp = None
      prevmap = None
      offset = 0
      offset0 = offset
      def flush_submap_fp():
        ''' Turn the current submap_fp into a MappedFD and store it.
            Then pad the maps with None until we're ready for the next map.
        '''
        nonlocal submap_fp, maps, mapsize, recsize
        nonlocal prevmap, offset0, offset
        nonlocal submap_index, leaf_submap_index, submappath
        X("flush_submap_fp (submap_fp=%s)...", submap_fp)
        X("flush_submap_fp: len(maps)=%d (includes stub), submap_index=%d", len(maps), submap_index)
        # discard the end stub
        maps.pop()
        if submap_fp is not None:
          # construct a submap for the current map file
          submap_fp.flush()
          if self.mappath:
            with Pfx("link submap %d from %r to %r", submap_index, submap_fp.name, submappath):
              os.link(submap_fp.name, submappath)
          assert submap_index == len(maps), \
              "submap_index(%d) != len(maps)(%d), maps=%r" \
              % (submap_index, len(maps), maps)
          # append a MappedFD for the current file and close it
          newmap = MappedFD(submap_fp, mapsize, recsize, offset0, offset, submap_index)
          maps.append(newmap)
          submap_fp.close()
          submap_fp = None
          if prevmap is not None:
            prevmap.nextmap = newmap
          newmap.prevmap = prevmap
          prevmap = newmap
        # pad the map with None until submap_index == leaf_submap_index
        while len(maps) < leaf_submap_index:
          maps.append(None)
        submap_index = len(maps)
        # add a new end stub
        last_submap = _MappedFDStub(offset)
        maps.append(last_submap)
        if prevmap:
          prevmap.nextmap = last_submap
        else:
          assert submap_index == 0, "submap_index(%d) != 0 but prevmap is None - should only happen on the first submap" % (submap_index,)
        self.mapped_to = offset
        # reset offset0 for the new submap
        offset0 = offset
      with self._load_lock:
        for leaf in self.block.leaves:
          if nleaves % 4096 == 0 and nleaves > 0:
            X("... mapped %d leaves in %gs", nleaves, time() - start_time)
          nleaves += 1
          self.self_check()
          if self.cancelled:
            X("CANCELLED: abort submap construction")
            return
          leaf_submap_index = offset // mapsize
          leaf_submap_offset = offset % mapsize
          if submap_index < leaf_submap_index:
            # this leaf belongs in a new submap
            flush_submap_fp()
            assert submap_index == leaf_submap_index, "submap_index(%d) != leaf_submap_index(%d)" % (submap_index, leaf_submap_index)
            self._load_lock.release()
            self._load_lock.acquire()
            # prep for new submap file
            if submap_fp is None:
              # commence new submap, or skip over existing one
              need_submap_fp = True
              if self.mappath:
                submappath = joinpath(self.mappath, '%d.blockmap' % (submap_index,))
                if pathexists(submappath):
                  # we will just skip over the submap
                  # TODO: fast skip to leaves from the offset of the next blockmap
                  # TODO: hook up the MappedFD object for this file
                  X("skip submap creation, path exists: %r", submappath)
                  need_submap_fp = False
              if need_submap_fp:
                if self.mappath:
                  submap_fp = NamedTemporaryFile('wb')
                else:
                  submap_fp = TemporaryFile('wb')
              else:
                submap_fp = None
          if submap_fp is not None:
            try:
              h = leaf.hashcode
            except AttributeError:
              # make a conventional HashCodeBlock and index that
              from .block import HashCodeBlock
              data = leaf.data
              if len(data) >= 65536:
                warning("promoting %d bytes from %s to a new HashCodeBlock", len(data), leaf)
              leaf = HashCodeBlock(data=data)
              h = leaf.hashcode
            submap_fp.write(OFF_STRUCT.pack(leaf_submap_offset))
            submap_fp.write(h)
          offset += leaf.span
      flush_submap_fp()
    self._loaded = True
    end_time = time()
    X("_load_maps FINAL: mapped %d leaves in %gs", nleaves, end_time - start_time)
    self.self_check()

  def self_check(self):
    ''' Perform some integrity tests.
    '''
    ##assert self._loaded
    maps = self.maps
    for i in range(len(maps)-1):
      submap = maps[i]
      if submap is None:
        continue
      assert isinstance(submap, MappedFD), \
          "maps[%d] is not a MappedFD: %r; maps=%r" % (i, type(submap), maps)
      assert maps[i].nextmap is not None, \
          "len(maps)=%d: maps[%d].nextmap=%s; maps=%r" % (len(maps), i, maps[i].nextmap, maps)
    assert isinstance(maps[-1], _MappedFDStub)

  def chunks(self, offset, span):
    ''' Generator yielding data from [offset:offset+span] from the relevant leaves.
    '''
    for leaf, start, end in self.slices(offset, span):
      assert start < end
      yield leaf[start:end]

  def slices(self, offset, span):
    ''' Generator yielding (leaf, start, end) from [offset:offset+span].
    '''
    from .block import get_HashCodeBlock
    ##from cs.py.stack import caller
    ##X("BlockMap.slices(offset=%d, span=%d) from %s ...", offset, span, caller())
    ##raise RuntimeError("BANG")
    if offset < 0:
      raise ValueError("offset(%d) should be >= 0" % (offset,))
    if span < 0:
      raise ValueError("span(%d) should be >= 0" % (span,))
    if span == 0:
      return
    while span > 0:
      if self.mapped_to <= offset:
        # outside the mapped range
        yield from self.block.slices(offset, offset + span, no_blockmap=True)
        return
      # we can get the start of the span from the blockmap
      S = self.S
      hashclass = S.hashclass
      maps = self.maps
      mapsize = self.mapsize
      submap_index = offset // mapsize
      submap_offset = offset % mapsize
      with self._load_lock:
        submap = maps[submap_index]
        while submap is None:
          submap_index -= 1
          submap = maps[submap_index]
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
        ##X("maps=%d:%r, submap_index=%d", len(maps), maps, submap_index)
        submap_base = submap.base
      while span > 0:
        with self._load_lock:
          # pull slices from the mapped range
          leaf_offset, leaf_span, leaf_hashcode = submap.get_record(i)
          leaf_offset += submap_base
          assert offset >= leaf_offset and offset < leaf_offset + leaf_span, \
              "NOT offset(%d) >= leaf_offset(%d) and offset < leaf_offset + leaf_span(%d) => %d" % (offset, leaf_offset, leaf_span, leaf_offset + leaf_span)
          leaf_hashcode = hashclass.from_hashbytes(leaf_hashcode)
          start = offset - leaf_offset
          end = start + min(span, leaf_span - start)
          leaf = get_HashCodeBlock(leaf_hashcode)
          leaf.rq_data()
          ## then try requesting leaf2
        yield leaf, start, end
        with self._load_lock:
          yielded_length = end - start
          offset += yielded_length
          span -= yielded_length
          if offset >= self.mapped_to:
            # reached the end of the mapped range, resume main loop
            break
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
