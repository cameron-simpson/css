#!/usr/bin/python

''' Functions and classes relating to Blocks, which are data chunk references.

    All Blocks derive from the base class _Block.

    The following Block types are provided:

    HashCodeBlock: a reference to data by its hashcode.

    LiteralBlock: a Block containing its literal data, used when
      the serialisation of a HashCodeBlock exceeds the length of the
      data.

    RLEBlock: a run length encoded Block for repeated byte values,
      particularly long runs of the NUL byte.

    SubBlock: a Block whose data is a subspan of another Block,
      particularly used when new data are blockified from part of an
      existing run of Blocks where the part does not start or end on
      a Block boundary.

    IndirectBlock: a Block whose data is the concatenation of a
      sequence of subsidiary Bloacks, which themselves may also be
      IndirectBlocks. This is how larger files are composed of finite
      sized Blocks. An IndirectBlock is internally constructed as
      a wrapper for another Block whose data are the serialisation
      of the subblock references.

    All Blocks have a .span attribute, which is the length of the
    data they encompass. For all "leaf" Blocks this value is same
    same as the length of their "direct" data, but for IndirectBlocks
    this is the sum of the .span values of their subblocks.
'''

from __future__ import print_function
from abc import ABC
from enum import IntEnum, unique as uniqueEnum
from functools import lru_cache
import sys
from threading import RLock
from cs.lex import texthexify, untexthexify, get_decimal_value
from cs.logutils import warning, exception
from cs.pfx import Pfx
from cs.py.func import prop
from cs.serialise import get_bs, put_bs
from cs.threads import locked
from cs.x import X
from . import defaults, totext
from .hash import decode as hash_decode
from .transcribe import Transcriber, register as register_transcriber, parse

F_BLOCK_INDIRECT = 0x01     # indirect block
F_BLOCK_TYPED = 0x02        # block type provided, otherwise BT_HASHCODE
F_BLOCK_TYPE_FLAGS = 0x04   # type-specific flags follow type

@uniqueEnum
class BlockType(IntEnum):
  ''' Block type codes used in binary serialisation.
  '''
  BT_INDIRECT = -1          # never gets transcribed
  BT_HASHCODE = 0           # default type: hashref
  BT_RLE = 1                # run length encoding: span octet
  BT_LITERAL = 2            # span raw-data
  BT_SUBBLOCK = 3           # a SubBlock of another Block

def decodeBlocks(bs, offset=0):
  ''' Process the bytes `bs` from the supplied `offset` (default 0).
      Yield Blocks.
  '''
  while offset < len(bs):
    B, offset = decodeBlock(bs, offset)
    yield B

def encoded_Block_fields(flags, span, block_type, type_flags=0, chunks=()):
  ''' Transcribe a Block reference given its fields, yielding bytes objects.
  '''
  if flags & ~(F_BLOCK_INDIRECT|F_BLOCK_TYPED|F_BLOCK_TYPE_FLAGS):
    raise ValueError("unexpected flags: 0x%02x" % (flags,))
  if span < 0:
    raise ValueError("expected span >= 1, got: %s" % (span,))
  if not isinstance(block_type, BlockType):
    block_type = BlockType(block_type)
  if block_type is BlockType.BT_HASHCODE:
    flags &= ~F_BLOCK_TYPED
  else:
    flags |= F_BLOCK_TYPED
  if type_flags == 0:
    flags &= ~F_BLOCK_TYPE_FLAGS
  else:
    flags |= F_BLOCK_TYPE_FLAGS
  yield put_bs(flags)
  yield put_bs(span)
  if flags & F_BLOCK_TYPED:
    yield put_bs(block_type)
  if flags & F_BLOCK_TYPE_FLAGS:
    yield put_bs(type_flags)
  for chunk in chunks:
    yield chunk

def decodeBlock(bs, offset=0, length=None):
  ''' Decode a Block reference from the bytes `bs` at offset `offset`. Return the Block and the new offset.
      If `length` is None, expect a leading BS(reflen) run length indicating
        the length of the block record that follows.
      Format is:
        [BS(reflen)]  # length of following data
        BS(flags)
          0x01 indirect blockref
          0x02 typed: type follows
          0x04 type flags: per type flags follow type
        BS(span)
        [BS(type)]
        [BS(type_flags)]
        union { type 0: hash
                type 1: octet-value (repeat span times to get data)
                type 2: raw-data (span bytes)
                type 3: suboffset, super block
              }
  '''
  with Pfx('decodeBlock(bs=%r,offset=%d,length=%s)',
           bs[offset:offset+16], offset, length):
    if length is None:
      length, offset = get_bs(bs, offset)
      return decodeBlock(bs, offset, length)
    if length > len(bs) - offset:
      raise ValueError("length(%d) > len(bs[%d:]):%d"
                       % (length, offset, len(bs) - offset))
    bs0 = bs
    offset0 = offset
    # Note offset after length field, used to sanity check decoding.
    # This is also used by SubBlocks to compute the length to pass
    # to the decode of the inner Block record.
    offset0a = offset
    # gather flags
    flags, offset = get_bs(bs, offset)
    is_indirect = bool(flags & F_BLOCK_INDIRECT)
    is_typed = bool(flags & F_BLOCK_TYPED)
    has_type_flags = bool(flags & F_BLOCK_TYPE_FLAGS)
    unknown_flags = flags & ~(F_BLOCK_INDIRECT|F_BLOCK_TYPED|F_BLOCK_TYPE_FLAGS)
    if unknown_flags:
      raise ValueError("unexpected flags value (0x%02x) with unsupported flags=0x%02x, bs[offset=%d:]=%r"
                       % (flags, unknown_flags, offset0, bs0[offset0:]))
    # gather span
    span, offset = get_bs(bs, offset)
    if is_indirect:
      # With indirect blocks, the span is of the implied data, not
      # the referenced block's data. Therefore we build the referenced
      # block with a span of None and store the span in the indirect
      # block.
      ispan = span
      span = None
    # block type, default BT_HASHCODE
    if is_typed:
      block_type, offset = get_bs(bs, offset)
      block_type = BlockType(block_type)
    else:
      block_type = BlockType.BT_HASHCODE
    with Pfx("block_type=%s", block_type):
      # gather type flags
      if has_type_flags:
        type_flags, offset = get_bs(bs, offset)
        if type_flags:
          warning("nonzero type_flags: 0x%02x", type_flags)
      else:
        type_flags = 0x00
      # instantiate type specific block ref
      if block_type == BlockType.BT_HASHCODE:
        hashcode, offset = hash_decode(bs, offset)
        B = HashCodeBlock(hashcode=hashcode, span=span)
      elif block_type == BlockType.BT_RLE:
        octet = bs[offset]
        offset += 1
        B = RLEBlock(span, octet)
      elif block_type == BlockType.BT_LITERAL:
        offset1 = offset + span
        data = bs[offset:offset1]
        if len(data) != span:
          raise ValueError("expected %d literal bytes, got %d" % (span, len(data)))
        offset = offset1
        B = LiteralBlock(data)
      elif block_type == BlockType.BT_SUBBLOCK:
        suboffset, offset = get_bs(bs, offset)
        superB, offset = decodeBlock(bs, offset, length=length-(offset-offset0a))
        # wrap inner Block in subspan
        B = SubBlock(superB, suboffset, span)
      else:
        raise ValueError("unsupported Block type 0x%02x" % (block_type,))
      # check that we decoded the correct number of bytes
      if offset - offset0a > length:
        raise ValueError("overflow decoding Block: length should be %d, but decoded %d bytes" % (length, offset - offset0))
      if offset - offset0a < length:
        raise ValueError("underflow decoding Block: length should be %d, but decoded %d bytes" % (length, offset - offset0))
      if is_indirect:
        B = _IndirectBlock(B, span=ispan)
      return B, offset

def encodeBlocks(blocks):
  ''' Generator yielding byte chunks encoding Blocks; inverse of decodeBlocks.
  '''
  for B in blocks:
    enc = B.encode()
    yield put_bs(len(enc))
    yield enc

def encodeBlock(B):
  ''' Return a bytes object containing the run length encoding from a Block.
      put_bs(len(B.encode())) + B.encode()
  '''
  return b''.join(encodeBlocks((B,)))

class _Block(Transcriber, ABC):

  def __init__(self, block_type, span):
    self.type = block_type
    if span is not None:
      if not isinstance(span, int) or span < 0:
        raise ValueError("invalid span: %r", span)
      self.span = span
    self.indirect = False
    self.blockmap = None
    self._lock = RLock()

  def __eq__(self, oblock):
    if self is oblock:
      return True
    if self.span != oblock.span:
      # different lengths, no match
      return False
    # see if both blocks are direct blocks
    if not self.indirect and not oblock.indirect:
      # directly compare hashcodes if available
      try:
        h1 = self.hashcode
      except AttributeError:
        pass
      else:
        try:
          h2 = oblock.hashcode
        except AttributeError:
          pass
        else:
          return h1 == h2
      # directly compare data otherwise
      # TODO: can be memory expensive - consider iterative leaf chunk comparison
      return self.data == oblock.data
    # indirect: walk both blocks comparing leaves
    leaves1 = self.leaves
    leaves2 = oblock.leaves
    offset = 0      # amount already compared
    offset1 = 0     # offset of start of leaf1
    offset2 = 0     # offset of start of leaf2
    leaf2 = None
    for leaf1 in leaves1:
      end1 = offset1 + len(leaf1)
      while offset < end1:
        # still more bytes in leaf1 needing comparison
        # fetch leaf2 if required
        while leaf2 is None:
          try:
            leaf2 = next(leaves2)
          except StopIteration:
            # oblock is short
            return False
          if len(leaf2) == 0:
            leaf2 = None
        # compare leaves if aligned
        # this will sidestep any data fetch if they both have hashcodes
        if offset == offset1 and offset == offset2 and len(leaf1) == len(leaf2):
          if leaf1 != leaf2:
            return False
          # identical, advance to end of block
          offset = end1
          leaf2 = None
          continue
        cmplen = min(offset1, offset2) - offset
        if cmplen < 0:
          raise RuntimeError("cmplen(%d) < 0: offset=%d, offset1=%d, offset2=%d"
                             % (cmplen, offset, offset1, offset2))
        # we can defer fetching the data until now
        data1 = leaf1.data
        data2 = leaf2.data
        if ( data1[offset - offset1:offset - offset1 + cmplen]
             != data2[offset - offset2:offset - offset2 + cmplen]
           ):
          return False
        end2 = offset2 + len(data2)
        offset += cmplen
        if offset > end1 or offset > end2:
          raise RuntimeError(
              "offset advanced beyond end of leaf1 or leaf2: offset=%d, end(leaf1)=%d, end(leaf2)= %d"
              % ( offset, end1, end2))
        if offset >= end2:
          # leaf2 consumed, discard
          leaf2 = None
    # check that there are no more nonempty leaves in leaves2
    while leaf2 is None:
      try:
        leaf2 = next(leaves2)
      except StopIteration:
        pass
      else:
        if len(leaf2) == 0:
          leaf2 = None
    # data are identical if we have consumed all of leaves1 an all of leaves2
    return leaf2 is None

  def _encode(self, flags, span, block_type, block_type_flags, chunks):
    ''' Return the bytes encoding for this Block, sans run length.
    '''
    if span is None:
      span = self.span
    return b''.join(encoded_Block_fields(flags, span, block_type, block_type_flags, chunks))

  def __getattr__(self, attr):
    if attr == 'data':
      with self._lock:
        if 'data' not in self.__dict__:
          self.data = self._data()
      return self.data
    raise AttributeError(attr)

  def __getitem__(self, index):
    ''' Return specified direct data.
    '''
    return self.data[index]

  def __len__(self):
    ''' len(Block) is the length of the encompassed data.
    '''
    return self.span

  def rq_data(self):
    ''' Queue a request to fetch this Block's immediate data.
    '''
    X("rq_data(%s)", self)
    if 'data' not in self.__dict__:
      X("dispatch bg call to _data ...")
      defaults.S.bg(self._data)

  def matches_data(self, odata):
    ''' Check supplied bytes `odata` against this Block's hashcode.
        NB: _not_ defined on indirect Blocks to avoid mistakes.
    '''
    try:
      h = self.hashcode
    except AttributeError:
      return self.data == odata
    return h == h.from_chunk(odata)

  @prop
  def leaves(self):
    ''' Return the leaf (direct) blocks.
    '''
    if self.indirect:
      for B in self.subblocks:
        yield from B.leaves
    elif self.span > 0:
      yield self

  @locked
  def get_blockmap(self, force=False, blockmapdir=None):
    ''' Get the blockmap for this block, creating it if necessary.
        `force`: if true, create a new blockmap anyway; default: False
        `blockmapdir`: directory to hold persistent block maps
    '''
    if force:
      blockmap = None
    else:
      blockmap = self.blockmap
    if blockmap is None:
      warning("making blockmap for %s", self)
      from .blockmap import BlockMap
      if blockmapdir is None:
        blockmapdir = defaults.S.blockmapdir
      self.blockmap = blockmap = BlockMap(self, blockmapdir=blockmapdir)
    return blockmap

  def chunks(self, start=None, end=None, no_blockmap=False):
    ''' Generator yielding data from the direct blocks.
    '''
    for leaf, leaf_start, leaf_end in self.slices(start=start, end=end, no_blockmap=no_blockmap):
      yield leaf[leaf_start:leaf_end]

  def slices(self, start=None, end=None, no_blockmap=False):
    ''' Return an iterator yielding (Block, start, len) tuples representing the leaf data covering the supplied span `start`:`end`.
        The iterator may end early if the span exceeds the Block data.
    '''
    ##X("slices %s ...", self)
    if start is None:
      start = 0
    elif start < 0:
      raise ValueError("start must be >= 0, received: %r" % (start,))
    if end is None:
      end = len(self)
    elif end < start:
      raise ValueError("end must be >= start(%r), received: %r" % (start, end))
    if self.indirect:
      if not no_blockmap:
        # use the blockmap to access the data if present
        blockmap = self.blockmap
        if blockmap:
          X("_Block.slices: yield from blockmap.slices[%d:%d] ...", start, end)
          yield from blockmap.slices(start, end - start)
          return
        X("_Block:%s.slices: no BlockMap (%r), fall through", self, blockmap)
      offset = 0
      X("_Block:%s.slices: iterate over subblocks...", self)
      for B in self.subblocks:
        sublen = len(B)
        if start <= offset:
          substart = max(0, start - offset)
          subend = min(sublen, end - offset)
          yield from B.slices(substart, subend)
        offset += sublen
        if offset >= end:
          break
    else:
      # a leaf Block
      if start < len(self):
        yield self, start, min(end, len(self))

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
      raise ValueError("end must be >= start(%r), received: %r" % (start, end))
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
          raise RuntimeError(
              "got slice for partial Block %s start=%r end=%r but Block is indirect! should be a partial leaf"
              % (B, Bstart, Bend))
        yield SubBlock(B, Bstart, Bend - Bstart)

  def textencode(self):
    ''' Transcribe this Block's binary encoding as text.
        TODO: Obsolete, remove.
    '''
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

  def pushto(self, S2, Q=None):
    ''' Push this Block and any implied subblocks to the Store `S2`.
        `S2`: the secondary Store to receive Blocks
        `Q`: optional preexisting Queue, which itself should have
          come from a .pushto targetting the Store `S2`.
        If `Q` is supplied, this method will return as soon as all
        the relevant Blocks have been pushed i.e. possibly before
        delivery is complete. If `Q` is not supplied, a new Queue
        is allocated; after all Blocks have been pushed the Queue
        is closed and its worker waited for.
        TODO: optional `no_wait` parameter to control waiting,
        default False, which would support closing the Queue but
        not waiting for the worker completion. This is on the premise
        that the final Store shutdown of `S2` will wait for outstanding
        operations anyway.
    '''
    S1 = defaults.S
    if Q is None:
      # create a Queue and a worker Thread
      Q, T = S1.pushto(S2)
    else:
      # use an existing Queue, no Thread to wait for
      T = None
    Q.put(self)
    if self.indirect:
      # recurse, reusing the Queue
      for subB in self.subblocks:
        subB.pushto(S2, Q)
    if T:
      Q.close()
      T.join()

@lru_cache(maxsize=1024*1024, typed=True)
def get_HashCodeBlock(hashcode):
  ''' Caching constructor for HashCodeBlocks of known code.
  '''
  if hashcode is None:
    raise ValueError("invlaid hashcode, may not be None")
  return HashCodeBlock(hashcode=hashcode)

class HashCodeBlock(_Block):
  ''' A Block reference based on a Store hashcode.
  '''

  transcribe_prefix = 'B'

  def __init__(self, hashcode=None, data=None, added=False, span=None, **kw):
    ''' Initialise a BT_HASHCODE Block or IndirectBlock.
        A HashCodeBlock always stores its hashcode directly.
        If `data` is supplied, store it and compute or check the hashcode.
        If `span` is not None, store it. Otherwise compute it on
          demand from the data, fetching that if necessary.
        NB: The data are not kept in memory; fetched on demand.
        `added`: if true, do not add the data to the current Store
    '''
    if data is None:
      if hashcode is None:
        raise ValueError("one of data or hashcode must be not-None")
    else:
      # when constructing an indirect block, span != len(data)
      if span is None:
        span = len(data)
      if added:
        # Block already Stored, just require presupplied hashcode
        if hashcode is None:
          raise ValueError("added=%s but no hashcode supplied" % (added,))
      else:
        h = defaults.S.add(data)
        if hashcode is None:
          hashcode = h
        elif h != hashcode:
          raise ValueError(
              "supplied hashcode %r != saved hash for data (%r : %r)"
              % (hashcode, h, data))
    self._span = None
    _Block.__init__(self, BlockType.BT_HASHCODE, span=span, **kw)
    self.hashcode = hashcode

  @prop
  def span(self):
    ''' Return the data length, computing it from the data if required.
    '''
    _span = self._span
    if _span is None:
      self._span = _span = len(self.data)
    return _span

  @span.setter
  def span(self, newspan):
    if newspan < 0:
      raise ValueError("%s: set .span: invalid newspan=%s", self, newspan)
    if self._span is None:
      self._span = newspan
    else:
      warning("setting .span a second time")
      if newspan != self._span:
        raise RuntimeError(
            "%s: tried to change .span from %s to %s"
            % (self, self._span, newspan))
      else:
        raise RuntimeError("SECOND UNEXPECTED")

  def _data(self):
    S = defaults.S
    bs = S[self.hashcode]
    if self._span is not None and len(bs) != self._span:
      raise RuntimeError(
          "%s: span=%d but len(bs)=%d" % (self, self.span, len(bs)))
    return bs

  def encode(self, flags=0, span=None):
    hashcode = self.hashcode
    return self._encode(flags, span, BlockType.BT_HASHCODE, 0,
                        ( hashcode.encode(), ))

  def transcribe_inner(self, T, fp):
    m = {'hash': self.hashcode}
    if self._span is not None:
      m['span'] = self._span
    return T.transcribe_mapping(m, fp)

  @classmethod
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    m, offset = T.parse_mapping(s, offset, stopchar)
    span = m.pop('span', None)
    hashcode = m.pop('hash')
    if m:
      raise ValueError("unexpected fields: %r" % (m,))
    B = cls(hashcode=hashcode, span=span)
    return B, offset

register_transcriber(HashCodeBlock, ('B', 'IB'))

def Block(*, hashcode=None, data=None, span=None, added=False):
  ''' Factory function for a Block.
  '''
  if data is None:
    if span is None:
      raise ValueError('data and span may not both be None')
    B = get_HashCodeBlock(hashcode)
  else:
    if span is None:
      span = len(data)
    elif span != len(data):
      raise ValueError("span(%d) does not match data (%d bytes)"
                       % (span, len(data)))
    if len(data) > 32:
      B = HashCodeBlock(data=data, hashcode=hashcode, span=span, added=added)
    else:
      B = LiteralBlock(data=data)
  return B

def IndirectBlock(subblocks=None, hashcode=None, span=None, force=False):
  ''' Factory function for an indirect Block.

      Indirect blocks may be initialised in two ways:

      The first way is specified by supplying the `subblocks`
      parameter, an iterable of Blocks to be referenced by this
      IndirectBlock. The referenced Blocks are encoded and assembled
      into the data for this Block.

      The second way is to supplying the `hashcode` and `span` for
      an existing Stored block, whose content is used to initialise
      an IndirectBlock is with a hashcode and a span indicating the
      length of the data encompassed by the block speified by the
      hashcode; the data of that Block can be decoded to obtain the
      reference Blocks for this IndirectBlock.

      As an optimisation, unless `force` is true, if `subblocks`
      is empty a direct Block for b'' is returned or if `subblocks`
      has just one element then that element is returned.

      TODO: allow data= initialisation, to decode raw iblock data.
  '''

  if subblocks is None:
    # hashcode specified
    if hashcode is None:
      raise ValueError("one of subblocks or hashcode must be supplied")
    if span is None:
      raise ValueError("no span supplied with hashcode %s" % (hashcode,))
    B = get_HashCodeBlock(hashcode)
  else:
    # subblocks specified
    if hashcode is not None:
      raise ValueError("only one of hashocde and subblocks may be supplied")
    if isinstance(subblocks, _Block):
      subblocks = (subblocks,)
    elif isinstance(subblocks, bytes):
      subblocks = (Block(subblocks),)
    else:
      subblocks = tuple(subblocks)
    spans = [ subB.span for subB in subblocks ]
    subspan = sum(spans)
    if span is None:
      span = subspan
    elif span != subspan:
      raise ValueError("span(%d) does not match subblocks (totalling %d)"
                       % (span, subspan))
    if not force:
      if len(subblocks) == 0:
        return Block(data=b'')
      if len(subblocks) == 1:
        return subblocks[0]
    superBdata = b''.join(encodeBlocks(subblocks))
    B = HashCodeBlock(data=superBdata)
  return _IndirectBlock(B, span=span)

class _IndirectBlock(_Block):

  transcribe_prefix = 'I'

  def __init__(self, superB, span=None):
    super().__init__(BlockType.BT_INDIRECT, 0)
    self.indirect = True
    self.superblock = superB
    if span is None:
      span = sum(subB.span for subB in self.subblocks)
    self.span = span

  def __getattr__(self, attr):
    if attr == 'subblocks':
      with self._lock:
        if 'subblocks' not in self.__dict__:
          idata = self.superblock.data
          self.subblocks = tuple(decodeBlocks(idata))
      return self.subblocks
    return super().__getattr__(attr)

  def _data(self):
    ''' Return the concatenation of all the leaf data.
    '''
    return b''.join(leaf.data for leaf in self.leaves)

  def encode(self):
    ''' Serialise this Block to bytes.
    '''
    return self.superblock.encode(F_BLOCK_INDIRECT, span=self.span)

  def transcribe_inner(self, T, fp):
    ''' Transcribe "span:Block".
    '''
    fp.write(str(self.span))
    fp.write(':')
    T.transcribe(self.superblock, fp=fp)

  @classmethod
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    ''' Parse "span:Block"
    '''
    span, offset2 = get_decimal_value(s, offset)
    if s[offset2] != ':':
      raise ValueError("offset %d: missing colon after span(%d)" % (offset2, span))
    offset = offset2 + 1
    superB, offset = parse(s, offset, T)
    return cls(superB, span), offset

class RLEBlock(_Block):
  ''' An RLEBlock is a Run Length Encoded block of `span` bytes all of a specific value, typically NUL.
  '''

  transcribe_prefix = 'RLE'

  def __init__(self, span, octet, **kw):
    if isinstance(octet, int):
      octet = bytes((octet,))
    elif not isinstance(octet, bytes):
      raise TypeError(
          "octet should be an int or a bytes instance but is %s: %r"
          % (type(octet), octet))
    if len(octet) != 1:
      raise ValueError("len(octet):%d != 1" % (len(octet),))
    _Block.__init__(self, BlockType.BT_RLE, span=span, **kw)
    self.octet = octet

  def _data(self):
    ''' The data of this RLEBlock.
    '''
    return self.octet * self.span

  def encode(self, flags=0, span=None):
    ''' Return the binary transcription of an RLEBlock.
    '''
    return self._encode(flags, span, BlockType.BT_RLE, 0, ( self.octet, ))

  def transcribe_inner(self, T, fp):
    return T.transcribe_mapping({'span': self.span, 'octet': self.octet}, fp)

  @classmethod
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    m = T.parse_mapping(s, offset, stopchar)
    span = m.pop('span')
    octet = m.pop('octet')
    if m:
      raise ValueError("unexpected fields: %r" % (m,))
    return cls(span, octet), offset

register_transcriber(RLEBlock)

class LiteralBlock(_Block):
  ''' A LiteralBlock is for data too short to bother hashing and Storing.
  '''

  transcribe_prefix = 'LB'

  def __init__(self, data, **kw):
    _Block.__init__(self, BlockType.BT_LITERAL, span=len(data), **kw)
    self.data = data

  def encode(self, flags=0, span=None):
    ''' Return the binary transcription of a LiteralBlock.
    '''
    return self._encode(flags, span, BlockType.BT_LITERAL, 0,
                        ( self.data, ))

  def transcribe_inner(self, T, fp):
    fp.write(texthexify(self.data))

  @classmethod
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    endpos = s.find(stopchar, offset)
    if endpos < offset:
      raise ValueError("stopchar %r not found" % (stopchar,))
    data = untexthexify(s[offset:endpos])
    return cls(data), endpos

register_transcriber(LiteralBlock)

def SubBlock(superB, suboffset, span, **kw):
  ''' Factory for SubBlocks.
      Returns origin Block if suboffset==0 and span==len(superB).
      Returns am empty LiteralBlock if the span==0.
  '''
  with Pfx("SubBlock(suboffset=%d,span=%d,superB=%s)", suboffset, span, superB):
    # check offset and span here because we trust them later
    if suboffset < 0 or suboffset > len(superB):
      raise ValueError("suboffset out of range")
    if span < 0 or suboffset + span > len(superB):
      raise ValueError("span(%d) out of range", span)
    if span == 0:
      ##warning("span==0, returning empty LiteralBlock")
      return LiteralBlock(b'')
    if suboffset == 0 and span == len(superB):
      ##warning("covers full Block, returning original")
      return superB
    if isinstance(superB, _SubBlock):
      return _SubBlock(superB.superblock, suboffset + superB.offset, span)
    return _SubBlock(superB, suboffset, span, **kw)

class _SubBlock(_Block):
  ''' A SubBlock is a view into another block.
      A SubBlock may not be empty and may not cover the whole of its superblock.
  '''

  transcribe_prefix = 'SubB'

  def __init__(self, superB, suboffset, span, **kw):
    with Pfx("_SubBlock(suboffset=%d, span=%d)[len(superB)=%d]",
             suboffset, span, len(superB)):
      if suboffset < 0 or suboffset >= len(superB):
        raise ValueError('suboffset out of range 0-%d: %d' % (len(superB)-1, suboffset))
      if span < 0 or suboffset+span > len(superB):
        raise ValueError(
            'span must be nonnegative and less than %d (suboffset=%d, len(superblock)=%d): %d'
            % (len(superB)-suboffset, suboffset, len(superB), span))
      if suboffset == 0 and span == len(superB):
        raise RuntimeError('tried to make a SubBlock spanning all of of SuperB')
      _Block.__init__(self, BlockType.BT_SUBBLOCK, span, **kw)
      self.superblock = superB
      self.offset = suboffset

  def _data(self):
    ''' The full data for this block.
    '''
    # TODO: _Blocks need a subrange method that is efficient for indirect blocks
    bs = self.superblock.data[self.offset:self.offset + self.span]
    if len(bs) != self.span:
      raise RuntimeError(
          "%s: span=%d but superblock[%d:%d+%d=%d] has length %d"
          % (self, self.span, self.offset, self.offset, self.span,
             self.offset + self.span, len(bs)))
    return bs

  def encode(self, flags=0, span=None):
    ''' Return the binary transcription of a SubBlock.
    '''
    return self._encode(flags, span, BlockType.BT_SUBBLOCK, 0,
                        ( put_bs(self.offset),
                          self.superblock.encode(),
                        ))

  def __getitem__(self, index):
    if isinstance(index, slice):
      return self.data[index]
    if index < 0 or index >= self.span:
      raise IndexError("index %d outside span %d" % (index, self.span))
    return self.superblock[self.offset+index]

  def transcribe_inner(self, T, fp):
    return T.transcribe_mapping({
        'block': self.superblock,
        'offset': self.offset,
        'span': self.span
    }, fp)

  @classmethod
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    offset, block, suboffset, subspan = T.parse_mapping(
        s, offset, stopchar,
        required=('block', 'offset', 'span'))
    return cls(block, suboffset, subspan), offset

register_transcriber(_SubBlock)

def verify_block(B, recurse=False, S=None):
  ''' Perform integrity checks on the Block `B`, yield error messages.
  '''
  if S is None:
    S = defaults.S
  try:
    hashcode = B.hashcode
  except AttributeError:
    hashcode = None
  else:
    if hashcode not in S:
      yield str(B), "hashcode not in %s" % (S,)
    else:
      data = B.data
      # hash the data using the matching hash function
      data_hashcode = hashcode.hashfunc(data)
      if hashcode != data_hashcode:
        yield str(B), "hashcode(%s) does not match hashfunc of data(%s)" \
                 % (hashcode, data_hashcode)
      Sdata = S[hashcode]
      if Sdata != data:
        yield str(B), "B.data != S[%s]" % (hashcode,)
  if B.indirect:
    if recurse:
      for subB in B.subblocks:
        yield from verify_block(subB, recurse=True, S=S)
  data = B.data
  if B.span != len(data):
    X("VERIFY BLOCK %s: B.span=%d, len(data)=%d, data=%r...",
      B, B.span, len(data), data[:16])
    yield str(B), "span(%d) != len(data:%d)" % (B.span, len(data))

if __name__ == '__main__':
  from .block_tests import selftest
  selftest(sys.argv)
