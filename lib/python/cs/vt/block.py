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
from cs.binary import (
    PacketField, BSUInt, BSData,
    flatten as flatten_transcription
)
from cs.buffer import CornuCopyBuffer
from cs.lex import texthexify, untexthexify, get_decimal_value
from cs.logutils import warning
from cs.pfx import Pfx
from cs.py.func import prop
from cs.threads import locked
from cs.x import X
from . import defaults, totext
from .hash import HashCode
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

class BlockRecord(PacketField):
  ''' PacketField support binary parsing and transcription of blockrefs.
  '''

  TEST_CASES = (
      # zero length hashcode block
      # note that the hashcode doesn't match that of b''
      b'\x17\0\0' + b'\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0',
  )

  @staticmethod
  def value_from_buffer(bfr):
    ''' Decode a Block reference from a buffer.

        Format is:

            BS(length)
            BS(flags)
              0x01 indirect blockref
              0x02 typed: type follows, otherwise BT_HASHCODE
              0x04 type flags: per type flags follow type
            BS(span)
            [BS(type)]
            [BS(type_flags)]
            union {
              type BT_HASHCODE: hash
              type BT_RLE: octet-value (repeat span times to get data)
              type BT_LITERAL: raw-data (span bytes)
              type BT_SUBBLOCK: suboffset, super block
            }

        Even though this is all decodable without the leading length
        we use a leading length so that future encodings do not
        prevent parsing any following data.
    '''
    raw_encoding = BSData.value_from_buffer(bfr)
    blockref_bfr = CornuCopyBuffer.from_bytes(raw_encoding)
    flags = BSUInt.value_from_buffer(blockref_bfr)
    is_indirect = bool(flags & F_BLOCK_INDIRECT)
    is_typed = bool(flags & F_BLOCK_TYPED)
    has_type_flags = bool(flags & F_BLOCK_TYPE_FLAGS)
    unknown_flags = flags & ~(F_BLOCK_INDIRECT|F_BLOCK_TYPED|F_BLOCK_TYPE_FLAGS)
    if unknown_flags:
      raise ValueError(
          "unexpected flags value (0x%02x) with unsupported flags=0x%02x"
          % (flags, unknown_flags))
    span = BSUInt.value_from_buffer(blockref_bfr)
    if is_indirect:
      # With indirect blocks, the span is of the implied data, not
      # the referenced block's data. Therefore we build the referenced
      # block with a span of None and store the span in the indirect
      # block.
      ispan = span
      span = None
    # block type, default BT_HASHCODE
    if is_typed:
      block_type = BlockType(BSUInt.value_from_buffer(blockref_bfr))
    else:
      block_type = BlockType.BT_HASHCODE
    if has_type_flags:
      type_flags = BSUInt.value_from_buffer(blockref_bfr)
      if type_flags:
        warning("nonzero type_flags: 0x%02x", type_flags)
    else:
      type_flags = 0x00
    # instantiate type specific block ref
    if block_type == BlockType.BT_HASHCODE:
      hashcode = HashCode.from_buffer(blockref_bfr)
      B = HashCodeBlock(hashcode=hashcode, span=span)
    elif block_type == BlockType.BT_RLE:
      octet = blockref_bfr.take(1)
      B = RLEBlock(span, octet)
    elif block_type == BlockType.BT_LITERAL:
      data = blockref_bfr.take(span)
      B = LiteralBlock(data)
    elif block_type == BlockType.BT_SUBBLOCK:
      suboffset = BSUInt.value_from_buffer(blockref_bfr)
      superB = BlockRecord.value_from_buffer(blockref_bfr)
      # wrap inner Block in subspan
      B = SubBlock(superB, suboffset, span)
    else:
      raise ValueError("unsupported Block type 0x%02x" % (block_type,))
    if is_indirect:
      B = _IndirectBlock(B, span=ispan)
    if not blockref_bfr.at_eof():
      warning(
          "unparsed data (%d bytes) follow Block %s",
          len(raw_encoding) - blockref_bfr.offset, B)
    return B

  @staticmethod
  def transcribe_value(B):
    ''' Transcribe this Block, the inverse of value_from_buffer.
    '''
    transcription = []
    is_indirect = B.indirect
    span = B.span
    if is_indirect:
      # aside from the span, everything else comes from the superblock
      ##X("INDIRECT: B(%s) => B.superblock(%s)", B, B.superblock)
      B = B.superblock
    block_type = B.type
    assert block_type >= 0, "block_type(%s) => %d" % (B, B.type)
    block_typed = block_type != BlockType.BT_HASHCODE
    flags = (
        ( F_BLOCK_INDIRECT if is_indirect else 0 )
        | ( F_BLOCK_TYPED if block_typed else 0 )
        | 0     # no F_BLOCK_TYPE_FLAGS
    )
    transcription.append(BSUInt.transcribe_value(flags))
    transcription.append(BSUInt.transcribe_value(span))
    if block_typed:
      transcription.append(BSUInt.transcribe_value(block_type))
    # no block_type_flags
    if block_type == BlockType.BT_HASHCODE:
      transcription.append(B.hashcode.transcribe_b())
    elif block_type == BlockType.BT_RLE:
      transcription.append(B.octet)
    elif block_type == BlockType.BT_LITERAL:
      transcription.append(B.data)
    elif block_type == BlockType.BT_SUBBLOCK:
      ##X("TRANSCRIBE: B=%r", B)
      transcription.append(BSUInt.transcribe_value(B.offset))
      transcription.append(BlockRecord.transcribe_value(B.superblock))
    else:
      raise ValueError("unsupported Block type 0x%02x: %s" % (block_type, B))
    return BSData(b''.join(flatten_transcription(transcription))).transcribe()

Block_from_bytes = BlockRecord.value_from_bytes

def Blocks_from_bytes(bs, offset=0):
  ''' Process the bytes `bs` from the supplied `offset` (default 0).
      Yield Blocks.
  '''
  while offset < len(bs):
    B, offset = Block_from_bytes(bs, offset)
    yield B

class _Block(Transcriber, ABC):

  def __init__(self, block_type, span):
    self.type = block_type
    if span is not None:
      if not isinstance(span, int) or span < 0:
        raise ValueError("invalid span: %r" % (span,))
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
    # see if we can compare hashes
    h1 = getattr(self, 'hashcode', None)
    h2 = getattr(oblock, 'hashcode', None)
    if h1 is not None and h2 is not None and h1 == h2:
      if (
          self.indirect and oblock.indirect
          or not self.indirect and not oblock.indirect
      ):
        # same hashes and indirectness: same data content
        return True
    # see if both blocks are direct blocks
    if not self.indirect and not oblock.indirect:
      # directly compare data otherwise
      # TODO: this may be expensive for some Block types?
      return self.data == oblock.data
    # one of the blocks is indirect: walk both blocks comparing leaves
    # we could do this by stuffing one into a buffer but we still
    # want to do direct leaf comparisons when the leaves are aligned,
    # as that may skip a data fetch
    leaves1 = self.leaves
    leaves2 = oblock.leaves
    offset = 0      # amount already compared
    offset1 = 0     # offset of start of leaf1
    offset2 = 0     # offset of start of leaf2
    leaf2 = None
    for leaf1 in leaves1:
      # skip empty leaves
      leaf1len = len(leaf1)
      if leaf1len == 0:
        continue
      end1 = offset1 + leaf1len
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
              "offset advanced beyond end of leaf1 or leaf2:"
              " offset=%d, end(leaf1)=%d, end(leaf2)= %d"
              % ( offset, end1, end2))
        if offset >= end2:
          # leaf2 consumed, discard
          leaf2 = None
    # check that there are no more nonempty leaves in leaves2
    while leaf2 is None:
      try:
        leaf2 = next(leaves2)
      except StopIteration:
        break
      else:
        if len(leaf2) == 0:
          leaf2 = None
    # data are identical if we have consumed all of leaves1 an all of leaves2
    return leaf2 is None

  def encode(self):
    ''' Binary transcription of this Block via BlockRecord.
    '''
    return b''.join(BlockRecord.transcribe_value_flat(self))

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
    ''' Return an iterator yielding (Block, start, len) tuples
        representing the leaf data covering the supplied span `start`:`end`.

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
    assert end <= len(self)
    if self.indirect:
      if not no_blockmap:
        # use the blockmap to access the data if present
        blockmap = self.blockmap
        if blockmap:
          yield from blockmap.slices(start, end - start)
          return
      offset = 0
      for B in self.subblocks:
        sublen = len(B)
        if start <= offset + sublen:
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
    ''' Return an iterator yielding (Block, start, len) tuples
        representing the uppermost Blocks spanning `start`:`end`.

        The originating use case is to support providing minimal
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
    ''' Yield existing high level blocks and new partial Blocks
        covering a portion of this Block,
        for constructing a new minimal top block.
    '''
    for B, Bstart, Bend in self.top_slices(start, end):
      if Bstart == 0 and Bend == len(B):
        # an extant high level block
        yield B
      else:
        # should be a new partial block
        if B.indirect:
          raise RuntimeError(
              "got slice for partial Block %s start=%r end=%r"
              " but Block is indirect! should be a partial leaf"
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
      from .file import ROBlockFile
      return ROBlockFile(self)
    if mode == 'w+b':
      from .file import RWBlockFile
      return RWBlockFile(backing_block=self)
    raise ValueError(
        "unsupported open mode, expected 'rb' or 'w+b', got: %s" % (mode,))

  def pushto(self, S2, Q=None, runstate=None):
    ''' Push this Block and any implied subblocks to the Store `S2`.

        Parameters:
        * `S2`: the secondary Store to receive Blocks
        * `Q`: optional preexisting Queue, which itself should have
          come from a .pushto targetting the Store `S2`.
        * `runstate`: optional RunState used to cancel operation

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
        if runstate and runstate.cancelled:
          warning("pushto(%s) cancelled", self)
          break
        subB.pushto(S2, Q, runstate=runstate)
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
        `added`: if true, do not add the data to the current Store.
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
    ''' Set the span of the data encompassed by this HashCodeBlock.
    '''
    if newspan < 0:
      raise ValueError("%s: set .span: invalid newspan=%s" % (self, newspan))
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

      As an optimisation, unless `force` is true: if `subblocks`
      is empty a direct Block for b'' is returned; if `subblocks`
      has just one element then that element is returned.

      TODO: allow data= initialisation, to decode raw iblock data.

  '''
  ## TODO: A direct or single byte Block should be an RLEBlock;
  ##   this breaks our implementation of .hashcode - need to see if we
  ##   can not require it - check use cases.
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
      if not subblocks:
        return Block(data=b'')
      if len(subblocks) == 1:
        return subblocks[0]
    superBdata = b''.join(subB.encode() for subB in subblocks)
    B = HashCodeBlock(data=superBdata)
  return _IndirectBlock(B, span=span)

class _IndirectBlock(_Block):

  transcribe_prefix = 'I'

  def __init__(self, superB, span=None):
    if superB.indirect:
      raise ValueError("superB may not be indirect: superB=%s" % (superB,))
    super().__init__(BlockType.BT_INDIRECT, 0)
    self.indirect = True
    self.superblock = superB
    if span is None:
      span = sum(subB.span for subB in self.subblocks)
    self.span = span
    self.hashcode = superB.hashcode

  def __getattr__(self, attr):
    if attr == 'subblocks':
      with self._lock:
        if 'subblocks' not in self.__dict__:
          idata = self.superblock.data
          self.subblocks = tuple(Blocks_from_bytes(idata))
      return self.subblocks
    return super().__getattr__(attr)

  def _data(self):
    ''' Return the concatenation of all the leaf data.
    '''
    return b''.join(leaf.data for leaf in self.leaves)

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
  ''' An RLEBlock is a Run Length Encoded block of `span` bytes
      all of a specific value, typically NUL.
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
      raise ValueError("span(%d) out of range" % (span,))
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
      if B.indirect:
        hashdata = B.superblock.data
      else:
        hashdata = B.data
      # hash the data using the matching hash function
      data_hashcode = hashcode.hashfunc(hashdata)
      if hashcode != data_hashcode:
        yield str(B), "hashcode(%s) does not match hashfunc of data(%s)" \
                 % (hashcode, data_hashcode)
      Sdata = S[hashcode]
      if Sdata != hashdata:
        yield str(B), "Block hashdata != S[%s]" % (hashcode,)
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
