#!/usr/bin/python3
#
# Map block to leaves and offsets.
# - Cameron Simpson <cs@cskk.id.au> 07feb2018
#

'''
A flat index of leaf offsets and their hashcodes to speed data
lookup from an `IndirectBlock`. This produces memory mapped indices
to bypass the need to walk the block tree to fetch leaf data.
'''

from bisect import bisect_right
from collections import namedtuple
from mmap import mmap, MAP_PRIVATE, PROT_READ
import os
from os.path import isdir, exists as pathexists, join as joinpath
from struct import Struct
import sys
from tempfile import TemporaryFile, NamedTemporaryFile
from cs.logutils import warning, info as log_info
from cs.pfx import Pfx, pfx_method
from cs.progress import Progress
from cs.py.func import prop
from cs.resources import RunStateMixin
from cs.threads import bg as bg_thread
from cs.upd import upd_proxy, state as upd_state
from cs.x import X
from . import defaults
from .block import HashCodeBlock, IndirectBlock

# The record format uses 4 byte integer offsets
# so this is the maximum (and default) scale for the memory maps.
OFFSET_SCALE = 2**32

OFF_STRUCT = Struct('<L')

_MapEntry = namedtuple('MapEntry', 'index offset span hashcode')

class MapEntry(_MapEntry):
  ''' A blockmap entry (index, offset, span, hashcode) and related properties.
  '''

  @prop
  def leaf(self):
    ''' Return the leaf block for this entry.
    '''
    return HashCodeBlock(hashcode=self.hashcode, span=self.span)

  @prop
  def data(self):
    ''' Return the data from this leaf block.
    '''
    return self.leaf.data

class MappedFD:
  ''' Manage a memory map of the contents of a file
      representing a block's backing leaf content.

      The file contains records (offset, hashcode) in offset order,
      being the starting offset of a leaf block relative to the
      start of the map and the leaf block's hashcode.

      Aside from the first map, the first record need not have
      offset=0 because the leading bytes in this submap probably
      come from a leaf block from a preceeding submap that overflows
      into this map's range.
  '''

  def __init__(self, f, hashclass):
    ''' Initialise a MappedFD from a file.

        Parameters:
        * `f`: the file whose contents will be mapped
          This may be an open file object or the path to a persistent map file.
          The file is expected to be prefilled with complete records.
        * `hashclass`: the type of hashcodes stored in the map, used
          for sizing and for returning this type from the entry bytes

        If `f` is a file path it is opened for read.
        If `f` is an open file, the file's file descriptor is `dup()`ed
        and the dup used to manage the memory map, allowing the
        original file to be closed by the caller.
    '''
    self.hashclass = hashclass
    self.rec_size = OFF_STRUCT.size + hashclass.HASHLEN
    if isinstance(f, str):
      with Pfx("open(%r)", f):
        fd = os.open(f, os.O_RDONLY)
    else:
      # `f` should be an open file
      f.flush()
      with Pfx("dup(%r.fileno())", f):
        fd = os.dup(f.fileno())
    self.fd = fd
    self.mapped = mmap(fd, 0, flags=MAP_PRIVATE, prot=PROT_READ)
    self.record_count = self.mapped.size() // self.rec_size
    assert self.mapped.size() % self.rec_size == 0, \
        "mapped.size()=%s, rec_size=%s, modulus=%d" \
        % (self.mapped.size(), self.rec_size, self.mapped.size() % self.rec_size)

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

  def locate(self, offset):
    ''' Locate and return the `MapEntry` containing the specified `offset`.

        If the offset is not contained within this map then the returned
        `MapEntry` will contain (index=-1, offset=None, span=None, hashcode=None).
    '''
    if offset < 0 or offset >= OFFSET_SCALE:
      raise ValueError("offset(%s) out of range 0:%s" % (offset, OFFSET_SCALE))
    i = bisect_right(self, offset)
    assert 0 < i <= len(self)
    i -= 1
    entry = self.entry(i)
    if offset < entry.offset or offset >= entry.offset + entry.span:
      X("submap.locate(offset=%d): entry=%s OUT OF RANGE", offset, entry)
      entry = MapEntry(-1, None, None, None)
    return entry

  def __getitem__(self, i):
    ''' Return the offset for entry(i), to support bisect.
    '''
    try:
      offset = self.offset(i)
    except ValueError:
      raise IndexError(i)
    return offset

  def entries(self, i):
    ''' Yield `MapEntry` instances starting at index `i`.
    '''
    record_count = self.record_count
    if i < 0 or i >= record_count:
      raise ValueError("index(%s) out of range 0:%d" % (i, record_count))
    while i < self.record_count:
      yield self.entry(i)
      i += 1

  def entry(self, i):
    ''' Fetch the `MapEntry` at index `i`.
    '''
    i0 = i
    if i < 0:
      # convert negative index
      i = self.record_count + i
    if i < 0 or i >= self.record_count:
      raise ValueError(i0)
    mapped = self.mapped
    rec_size = self.rec_size
    rec_offset = i * rec_size
    hash_offset = rec_offset + OFF_STRUCT.size
    next_rec_offset = rec_offset + rec_size
    offset, = OFF_STRUCT.unpack(mapped[rec_offset:hash_offset])
    hashcode = self.hashclass.from_hashbytes(
        mapped[hash_offset:next_rec_offset]
    )
    if i == self.record_count - 1:
      span = len(defaults.S[hashcode])
    else:
      next_hash_offset = next_rec_offset + OFF_STRUCT.size
      next_offset, = OFF_STRUCT.unpack(
          mapped[next_rec_offset:next_hash_offset]
      )
      span = next_offset - offset
    return MapEntry(i, offset, span, hashcode)

  def offset(self, i):
    ''' Fetch the offset of index `i`.
    '''
    i0 = i
    if i < 0:
      # convert negative index
      i = self.record_count + i
    if i < 0 or i >= self.record_count:
      raise ValueError(i0)
    mapped = self.mapped
    rec_size = self.rec_size
    rec_offset = i * rec_size
    hash_offset = rec_offset + OFF_STRUCT.size
    offset, = OFF_STRUCT.unpack(mapped[rec_offset:hash_offset])
    return offset

  @prop
  def start(self):
    ''' The offset of the first leaf in the mapping.
    '''
    return self.offset(0)

class BlockMap(RunStateMixin):
  ''' A fast mapping of offsets to leaf block hashcodes.
  '''

  def __init__(self, block, mapsize=None, blockmapdir=None, runstate=None):
    ''' Initialise the `BlockMap`, dispatch the index generator.

        Parameters:
        * `block`: the source `Block`
        * `mapsize`: the size of each index map, default `OFFSET_SCALE`
        * `blockmapdir`: the pathname for persistent storage of `BlockMaps`
    '''
    super().__init__(runstate=runstate)
    if mapsize is None:
      mapsize = OFFSET_SCALE
    elif mapsize <= 0 or mapsize > OFFSET_SCALE:
      raise ValueError(
          "mapsize(%d) out of range, must be >0 and <=%d" %
          (mapsize, OFFSET_SCALE)
      )
    # DEBUGGING
    if blockmapdir is None:
      blockmapdir = defaults.S.blockmapdir
    if not isinstance(block, IndirectBlock):
      raise TypeError(
          "block needs to be an IndirectBlock, got a %s instead" %
          (type(block),)
      )
    hashcode = block.superblock.hashcode
    hashclass = type(hashcode)
    self.hashclass = hashclass
    self.mapsize = mapsize
    if blockmapdir is None:
      self.mappath = mappath = None
    else:
      self.mappath = mappath = joinpath(
          blockmapdir, "mapsize:%d" % (mapsize,), hashcode.filename
      )
      if not isdir(mappath):
        with Pfx("makedirs(%r)", mappath):
          os.makedirs(mappath)
    self.block = block
    self.S = defaults.S
    nsubmaps = len(block) // mapsize + 1
    submaps = [None] * nsubmaps
    self.maps = submaps
    mapped_to = 0
    self.rec_size = OFF_STRUCT.size + len(hashcode)
    self._loaded = False
    # preattach any existing blockmap files
    if mappath is not None:
      for submap_index in range(nsubmaps):
        submappath = joinpath(mappath, '%d.blockmap' % (submap_index,))
        if not pathexists(submappath):
          break
        # existing map, attach and install, advance and restart loop
        X("Blockmap.__init__: preattach existing map %r", submappath)
        submaps[submap_index] = MappedFD(submappath, hashclass)
        mapped_to += mapsize
    self.mapped_to = mapped_to
    if mapped_to < len(block):
      self.runstate.start()
      self._worker = bg_thread(
          self._load_maps,
          args=(defaults.S,),
          daemon=True,
          name="%s._load_maps" % (self,)
      )
    else:
      self._worker = None

  def __str__(self):
    return "%s(%s,%r)" % (type(self).__name__, self.block, self.mappath)

  def join(self):
    ''' Wait for the worker to complete.
    '''
    worker = self._worker
    if worker is not None:
      self._worker.join()

  def __del__(self):
    ''' Release resources on object deletion.
    '''
    self.close()

  def close(self):
    ''' Release the resources associated with the `BlockMap`.
    '''
    self.cancel()
    self.join()
    maps = self.maps
    for i in range(len(maps) - 1):
      submap = maps[i]
      if submap is not None:
        submap.close()
        maps[i] = None

  @upd_proxy
  @pfx_method(use_str=True)
  def _load_maps(self, S):
    ''' Load leaf offsets and hashcodes into the unfilled portion of the blockmap.
    '''
    proxy = upd_state.proxy
    offset = self.mapped_to
    mapsize = self.mapsize
    submap_index = offset // mapsize - 1
    with S:
      runstate = self.runstate
      block = self.block
      blocklen = len(block)
      hashclass = self.hashclass
      submaps = self.maps
      submap_fp = None
      submap_path = None
      nleaves = 0
      proxy.prefix = "%s(%s) leaves " % (type(self).__name__, block)
      P = Progress(name='scan', position=offset, total=blocklen)
      for leaf, start, length in block.slices(offset, blocklen):
        if runstate.cancelled:
          break
        if start > 0:
          # partial block, skip to next
          offset += length
          continue
        leaf_submap_index = offset // mapsize
        leaf_submap_offset = offset % mapsize
        if submap_index < leaf_submap_index:
          # this leaf belongs in a new submap
          if submap_fp is not None:
            # consume the submap in progress
            submap = MappedFD(submap_fp, hashclass)
            if submap_path is not None:
              log_info("new submap: %r", submap_path)
              os.link(submap_fp.name, submap_path)
            submaps[submap_index] = submap
            last_entry = submap.entry(-1)
            self.mapped_to = last_entry.offset + last_entry.span
            submap_fp.close()
            submap_fp = None
          # advance to the correct submap index
          submap_index = leaf_submap_index
          if self.mappath:
            # if we're doing persistent submaps...
            submap_path = joinpath(
                self.mappath, '%d.blockmap' % (submap_index,)
            )
            if pathexists(submap_path):
              # existing map, attach and install, advance and restart loop
              log_info("attach existing map %r", submap_path)
              submaps[submap_index] = MappedFD(submap_path, hashclass)
              offset = (submap_index + 1) * mapsize
              log_info("skip to offset=0x%x", offset)
              break
            # start a new persistent file to attach later
            submap_fp = NamedTemporaryFile('wb')
          else:
            # not persistent - start a new temp file to attach later
            submap_fp = TemporaryFile('wb')
            submap_path = None
        # post condition: correct submap_index and correct submap_fp state
        assert (
            submap_index == leaf_submap_index and submap_index < len(submaps)
            and (
                submap_fp is None if self.mappath and pathexists(submap_path)
                else submap_fp is not None
            )
        )
        try:
          h = leaf.hashcode
        except AttributeError:
          # make a conventional HashCodeBlock and index that
          data = leaf.data
          if len(data) >= 65536:
            warning(
                "promoting %d bytes from %s to a new HashCodeBlock", len(data),
                leaf
            )
          leaf = HashCodeBlock(data=data)
          h = leaf.hashcode
        submap_fp.write(OFF_STRUCT.pack(leaf_submap_offset))
        submap_fp.write(h)
        offset += leaf.span
        nleaves += 1
        if nleaves % 16 == 0:
          proxy(P.status(P.name, proxy.width))
        if nleaves % 4096 == 0:
          log_info(
              "processed %d leaves in %gs (%d leaves/s)", nleaves,
              runstate.run_time, nleaves // runstate.run_time
          )
      proxy("")
      log_info("leaf scan finished")
      # attach final submap after the loop if one is in progress
      if submap_fp is not None:
        # consume the submap in progress
        submap = MappedFD(submap_fp, hashclass)
        if submap_path is not None:
          log_info("new submap: %r", submap_path)
          os.link(submap_fp.name, submap_path)
        submaps[submap_index] = submap
        last_entry = submap.entry(-1)
        self.mapped_to = last_entry.offset + last_entry.span
        submap_fp.close()
        submap_fp = None
      log_info("LOAD MAPS: COMPLETE")

  def self_check(self):
    ''' Perform some integrity tests.
    '''
    ##assert self._loaded
    for i, submap in enumerate(self.maps):
      if submap is None:
        continue
      assert isinstance(submap, MappedFD), \
          "maps[%d] is not a MappedFD: %r; maps=%r" % (i, type(submap), self.maps)

  def datafrom(self, offset=0, span=None):
    ''' Generator yielding data from [offset:offset+span] from the relevant leaves.

        Parameters:
        * `offset`: starting offset within `self.block`, default `0`
        * `span`: number of bytes to cover; if omitted or `None`, the
          span runs to the end of `self.block`
    '''
    for leaf, start, end in self.slices(offset, span):
      assert start < end
      assert start >= 0
      assert end <= len(leaf)
      yield leaf[start:end]

  # TODO: accept start,end instead of start,span like other slices methods
  def slices(self, offset, span=None):
    ''' Generator yielding `(leaf,start,end)` from [offset:offset+span].

        Parameters:
        * `offset`: starting offset within `self.block`
        * `span`: number of bytes to cover; if omitted or `None`, the
          span runs to the end of `self.block`
    '''
    if span is None:
      span = len(self.block) - offset
    if offset < 0:
      raise ValueError("offset(%d) should be >= 0" % (offset,))
    if span < 0:
      raise ValueError("span(%d) should be >= 0" % (span,))
    if span == 0:
      return
    maps = self.maps
    mapsize = self.mapsize
    while span > 0:
      if self.mapped_to <= offset:
        # outside the mapped range
        # use the normal Block.slices method without any blockmap
        yield from self.block.slices(offset, offset + span, no_blockmap=True)
        return
      # we can get the start of the span from the blockmap
      submap_index = offset // mapsize
      # locate starting entry
      submap = maps[submap_index]
      if submap is None or submap.start > offset:
        # leaf must come from the preceeding submap
        while submap is None or submap.start > offset:
          submap_index -= 1
          submap = maps[submap_index]
          if submap:
            break
        entry = submap.entry(-1)
      else:
        submap_offset = offset % mapsize
        entry = submap.locate(submap_offset)
      submap_base = submap_index * mapsize
      while span > 0 and entry:
        entry_offset = submap_base + entry.offset
        assert entry_offset <= offset < entry_offset + entry.span
        leaf = entry.leaf
        leaf_start = offset - entry_offset
        leaf_subspan = min(entry.span - leaf_start, span)
        leaf_end = leaf_start + leaf_subspan
        yield leaf, leaf_start, leaf_end
        offset += leaf_subspan
        span -= leaf_subspan
        if span == 0:
          break
        if entry.index == submap.record_count - 1:
          break
        # fetch next entry
        entry = submap.entry(entry.index + 1)

  def data(self, offset, span):
    ''' Return the data from `[offset:offset+span]` as a single `bytes` object.
    '''
    return b''.join(self.datafrom(offset, span))

  def __getitem__(self, index):
    ''' Return a single byte or a slice from the `BlockMap`.
    '''
    if isinstance(index, int):
      return next(self.datafrom(index))[0]
    if index.step is not None and index.step != 1:
      raise ValueError("invalid slice: step=%s" % (index.step,))
    start = 0 if index.start is None else index.start
    if index.stop is None:
      span = None
    else:
      span = index.stop - start
      if span < 0:
        raise ValueError(
            "invalid span: stop(%s) < start(%s)" % (index.stop, index.start)
        )
      if span == 0:
        return b''
    return b''.join(self.datafrom(start, span))

if __name__ == '__main__':
  from .blockmap_tests import selftest
  selftest(sys.argv)
