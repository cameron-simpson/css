#!/usr/bin/python

from __future__ import print_function
import sys
from threading import RLock
from cs.logutils import D, debug, X
from cs.serialise import get_bs, put_bs
from cs.threads import locked_property
from cs.venti import defaults, totext
from .hash import Hash_SHA1, HASH_SHA1_T, HASH_SIZE_SHA1, decode as hash_decode

F_BLOCK_INDIRECT = 0x01 # indirect block

def decodeBlocks(bs, offset=0):
  ''' Process a bytes from the supplied `offset` (default 0).
      Yield Blocks.
  '''
  while offset < len(bs):
    B, offset = decodeBlock(bs, offset)
    yield B

def decodeBlock(bs, offset=0):
  ''' Decode a Block reference.
      Return the Block and the new offset.
      Format is:
        BS(flags)
          0x01 indirect blockref
          0x02 non-SHA1 hashcode
        BS(span)
        hash
  '''
  bs0 = bs
  offset0 = offset
  flags, offset = get_bs(bs, offset)
  unknown_flags = flags & ~F_BLOCK_INDIRECT
  if unknown_flags:
    raise ValueError("unexpected flags value (0x%02x) with unsupported flags=0x%02x, bs[offset=%d:]=%r"
                     % (flags, unknown_flags, offset0, bs0[offset0:]))
  span, offset = get_bs(bs, offset)
  indirect = bool(flags & F_BLOCK_INDIRECT)
  hashcode, offset = hash_decode(bs, offset)
  if indirect:
    B = IndirectBlock(hashcode=hashcode, span=span)
  else:
    B = Block(hashcode=hashcode, span=span)
  return B, offset

def encodeBlocks(blocks):
  ''' Return data bytes for an IndirectBlock.
  '''
  encs = []
  for B in blocks:
    encs.append(B.encode())
  return b''.join(encs)

def isBlock(o):
  return isinstance(o, _Block)

class _Block(object):

  def __init__(self, data=None, hashcode=None, span=None):
    if data is None and hashcode is None:
      raise ValueError("one of data or hashcode must be not-None")
    if data is not None:
      h = defaults.S.add(data)
      if hashcode is None:
        hashcode = h
      elif h != hashcode:
        raise ValueError("supplied hashcode %r != saved hash for data (%r : %r)" % (hashcode, h, data))
    self.hashcode = hashcode
    self._span = span
    self._lock = RLock()

  def __str__(self):
    return self.textencode()

  @property
  def data(self):
    ''' The direct data of this Block.
        i.e. _not_ the data implied by an indirect Block.
    '''
    return defaults.S[self.hashcode]

  def __getitem__(self, index):
    ''' Return specified direct data.
    '''
    return self.data[index]

  def __len__(self):
    ''' The len(Block) is the length of the encompassed data.
    '''
    return self.span

  def encode(self):
    ''' Encode this Block for storage:
        Format is:
          BS(flags)
            0x01 indirect block
            0x02 has hash type (False ==> Hash_SHA1_T)
          BS(span)
          hashcode.encode()     # may include hashlen prefix for some hash types
    '''
    flags = 0
    if self.indirect:
      flags |= F_BLOCK_INDIRECT
    hashcode = self.hashcode
    enc = put_bs(flags) + put_bs(self.span) + hashcode.encode()
    assert len(enc) >= 22
    return enc

  @property
  def chunks(self):
    ''' Yield the data from the direct blocks.
    '''
    for leaf in self.leaves:
      yield leaf.data

  def slices(self, start=None, end=None):
    ''' Return an iterator yielding (Block, start, len) tuples representing the leaf data covering the supplied span `start`:`end`.
        The iterator may end early if the span exceeds the Block data.
    '''
    X("Block<%s>[len=%d].slices(start=%r, end=%r)...", self, len(self), start, end)
    if start is None:
      start = 0
    elif start < 0:
      raise ValueError("start must be >= 0, received: %r" % (start,))
    if end is None:
      end = len(self)
    elif end < start:
      raise ValueError("end must be >= start(%r), received: %r" % (start,end))
    if self.indirect:
      X("Block.slices: indirect...")
      offset = 0
      for B in self.subblocks:
        sublen = len(B)
        substart = max(0, start - offset)
        subend = min(sublen, end - offset)
        if substart < subend:
          for subslice in B.slices(substart, subend):
            yield subslice
        offset += sublen
        if offset >= end:
          break
    else:
      X("Block.slices: direct")
      # a leaf Block
      if start < len(self):
        X("Block.slices: yield self, %d, %d", start, min(end, len(self)))
        yield self, start, min(end, len(self))
    X("Block.slices: COMPLETE")

  def top_slices(self, start=None, end=None):
    ''' Return an iterator yielding (Block, start, len) tuples representing the uppermost Blocks spanning `start`:`end`.
        This originating use case is to support providing minimal
        Block references required to assemble a new indirect Block
        consisting of data from this Block comingled with updated
        data without naively layering deeper levels of Block
        indirection with every update phase.
        The iterator may end early if the span exceeds the Block data.
    '''
    if start is None:
      start = 0
    elif start < 0:
      raise ValueError("start must be >= 0, received: %r" % (start,))
    if end is None:
      end = len(self)
    elif end < start:
      raise ValueError("end must be >= start(%r), received: %r" % (start,end))
    if self.indirect:
      offset = 0        # the absolute index of the left edge of subblock B
      for B in self.subblocks:
        sublen = len(B)
        substart = max(0, start - offset)
        subend = min(sublen, end - offset)
        if substart < subend:
          if subend - substart == sublen:
            yield B, 0, sublen
          else:
            for subslice in B.top_slices(substart, subend):
              yield subslice
        # advance the offset to account for this subblock
        offset += sublen
        if offset >= end:
          break
    else:
      # a leaf Block
      if start < len(self):
        yield self, start, min(end, len(self))

  def top_blocks(self, start, end):
    ''' Yield existing high level blocks and new partial Blocks covering a portion of this Block, for constructing a new minimal top block.
    '''
    for B, Bstart, Bend in self.top_slices(start, end):
      if Bstart == 0 and Bend == len(B):
        # an extant high level block
        yield B
      else:
        # should be a new partial block
        if B.indirect:
          raise RuntimeError("got slice for partial Block %s start=%r end=%r but Block is indirect! should be a partial leaf" % (B, Bstart, Bend))
        yield Block(data=B[Bstart:Bend])

  def all_data(self):
    ''' The entire data of this Block as a single bytes object.
    '''
    return b''.join(self.chunks)

  def textencode(self):
    return totext(self.encode())

  def open(self, mode="rb"):
    ''' Open the block as a file.
    '''
    if mode == 'rb':
      from .file import BlockFile
      return BlockFile(self)
    if mode == 'w+b':
      from .file import File
      return File(backing_block=self)
    raise ValueError("unsupported open mode, expected 'rb' or 'w+b', got: %s", mode)

class Block(_Block):
  ''' A direct block.
  '''

  def __init__(self, **kw):
    ''' Initialise a direct block, supplying data bytes or hashcode,
        but not both.
    '''
    _Block.__init__(self, **kw)
    self.indirect = False

  def matches_data(self, odata):
    ''' Check supplied bytes `odata` against this Block's hashcode.
        NB: _not_ defined on indirect Blocks to avoid mistakes.
    '''
    return self.hashcode == self.hashcode.from_data(odata)

  @property
  def leaves(self):
    yield self

  @locked_property
  def span(self):
    return len(self.data)

class IndirectBlock(_Block):
  ''' A preexisting indirect block.
      Indirect blocks come in two states, reflecting how how they are
      initialised.
      The other way to initialise an IndirectBlock is with a hashcode and a
      span indicating the length of the data encompassed by the block; this is
      how a block is made from a directory entry or another indirect block.

      An indirect block can be extended with more block hashes, even one
      initialised from a hashcode. It is necessary to call the .store()
      method on a block that has been extended.

      TODO: allow data= initialisation, to decode raw iblock data
  '''

  def __init__(self, subblocks=None, hashcode=None, span=None):
    if subblocks is None:
      _Block.__init__(self, hashcode=hashcode, span=span)
    else:
      _Block.__init__(self, data=encodeBlocks(subblocks))
    self.indirect = True

  @locked_property
  def subblocks(self):
    return tuple(decodeBlocks(self.data))

  @locked_property
  def span(self):
    ''' The span of an IndirectBlock is the sum of the spans of the subblocks.
    '''
    sp = 0
    for B in self.subblocks:
      sp += B.span
    return sp

  @property
  def leaves(self):
    ''' Return the leaf (direct) blocks.
    '''
    for B in self.subblocks:
      if B.indirect:
        for subB in B.leaves:
          yield subB
      else:
        yield B

def chunksOf(B, start, stop=None):
  ''' Generator that yields the chunks from the subblocks that span
      the supplied range.
  '''
  if stop is None:
    stop = sys.maxint
  elif stop <= start:
    return
  rangelen = stop - start

  # skip subblocks preceeding the range
  Bs = iter(self.subblocks())
  while True:
    try:
      B = Bs.next()
    except StopIteration:
      return
    Blen = len(B)
    if Blen <= start:
      # too early - skip this block
      start -= Blen
      continue
    break
  # post: B is a subblock spanning the start of the range
  assert start < Blen

  while rangelen > 0:
    if B.indirect:
      # pull chunks from the indirect block
      for chunk in B.chunks(start, start+rangelen):
        yield chunk
        rangelen -= len(chunk)
    else:
      # grab the relevant chunk of this direct block
      chunk = B[start:start+rangelen]
      yield chunk
      rangelen -= len(chunk)
    if rangelen <= 0:
      break
    try:
      B = Bs.next()
    except StopIteration:
      return
    # we always start from the start of the next block
    start = 0

def dump_block(B, fp=None, indent=''):
  if fp is None:
    fp = sys.stderr
  data = B.data
  if B.indirect:
    subblocks = B.subblocks
    print("%sIB.datalen=%d, span=%d, %d subblocks, hash=%s"
          % (indent, len(data), B.span, len(subblocks), B.hashcode),
          file=fp)
    indent += '  '
    for subB in subblocks:
      dump_block(subB, fp=fp, indent=indent)
  else:
    print("%sB.datalen=%d, span=%d, hash=%s"
          % (indent, len(data), B.span, B.hashcode),
          file=fp)

if __name__ == '__main__':
  import cs.venti.block_tests
  cs.venti.block_tests.selftest(sys.argv)
