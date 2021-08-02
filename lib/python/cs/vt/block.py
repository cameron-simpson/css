#!/usr/bin/python
#
# pylint: disable=too-many-lines

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
from abc import ABC, abstractmethod
from enum import IntEnum, unique as uniqueEnum
from functools import lru_cache
import sys
from icontract import require
from cs.binary import (
    BinarySingleValue, BSUInt, BSData, flatten as flatten_transcription
)
from cs.buffer import CornuCopyBuffer
from cs.lex import untexthexify, get_decimal_value
from cs.logutils import warning, error
from cs.pfx import Pfx
from cs.py.func import prop
from cs.threads import locked
from . import defaults, RLock
from .hash import HashCode, io_fail
from .transcribe import (
    Transcriber, register as register_transcriber, hexify, parse
)

F_BLOCK_INDIRECT = 0x01  # indirect block
F_BLOCK_TYPED = 0x02  # block type provided, otherwise BT_HASHCODE
F_BLOCK_TYPE_FLAGS = 0x04  # type-specific flags follow type

@uniqueEnum
class BlockType(IntEnum):
  ''' Block type codes used in binary serialisation.
  '''
  BT_INDIRECT = -1  # never gets transcribed
  BT_HASHCODE = 0  # default type: hashref
  BT_RLE = 1  # run length encoding: span octet
  BT_LITERAL = 2  # span raw-data
  BT_SUBBLOCK = 3  # a SubBlock of another Block

def isBlock(o):
  ''' Test if an object `o` is a subinstance of `_Block`.
  '''
  return isinstance(o, _Block)

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

  def __bytes__(self):
    ''' `bytes(_Block)` returns allo the data together as a single `bytes` instance.

        Try not to do this for indirect blocks, it gets expensive.
    '''
    return b''.join(self)

  def __iter__(self):
    ''' Iterating over a `_Block` yields chunks from `self.datafrom()`.
    '''
    return self.datafrom()

  # pylint: disable=too-many-branches,too-many-locals,too-many-return-statements
  def __eq__(self, oblock):
    ''' Compare this Block with another Block for data equality.
    '''
    if self is oblock:
      return True
    if self.span != oblock.span:
      # different lengths, no match
      return False
    # see if we can compare hashes
    h1 = getattr(self, 'hashcode', None)
    h2 = getattr(oblock, 'hashcode', None)
    if h1 is not None and h2 is not None and h1 == h2:
      if (self.indirect and oblock.indirect
          or not self.indirect and not oblock.indirect):
        # same hashes and indirectness: same data content
        return True
    # see if both blocks are direct blocks
    if not self.indirect and not oblock.indirect:
      # directly compare data otherwise
      # TODO: this may be expensive for some Block types?
      #       some kind of rolling buffer compare required
      return self.get_spanned_data() == oblock.get_spanned_data()
    # one of the blocks is indirect: walk both blocks comparing leaves
    # we could do this by stuffing one into a buffer but we still
    # want to do direct leaf comparisons when the leaves are aligned,
    # as that may skip a data fetch
    leaves1 = self.leaves
    leaves2 = oblock.leaves
    offset = 0  # amount already compared
    offset1 = 0  # offset of start of leaf1
    offset2 = 0  # offset of start of leaf2
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
        if offset == offset1 and offset == offset2 and len(leaf1) == len(leaf2
                                                                         ):
          if leaf1 != leaf2:
            return False
          # identical, advance to end of block
          offset = end1
          leaf2 = None
          continue
        cmplen = min(offset1, offset2) - offset
        if cmplen < 0:
          raise RuntimeError(
              "cmplen(%d) < 0: offset=%d, offset1=%d, offset2=%d" %
              (cmplen, offset, offset1, offset2)
          )
        # we can defer fetching the data until now
        data1 = leaf1.data
        data2 = leaf2.data
        if (data1[offset - offset1:offset - offset1 + cmplen] !=
            data2[offset - offset2:offset - offset2 + cmplen]):
          return False
        end2 = offset2 + len(data2)
        offset += cmplen
        if offset > end1 or offset > end2:
          raise RuntimeError(
              "offset advanced beyond end of leaf1 or leaf2:"
              " offset=%d, end(leaf1)=%d, end(leaf2)= %d" %
              (offset, end1, end2)
          )
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
    return bytes(BlockRecord(self))

  def __getitem__(self, index):
    ''' Return specified direct data.
    '''
    return self.get_spanned_data()[index]

  def __len__(self):
    ''' len(Block) is the length of the encompassed data.
    '''
    return self.span

  @abstractmethod
  def datafrom(self, start=0):
    ''' Yield data chuncks from this file.
    '''
    raise NotImplementedError("datafrom")

  def get_spanned_data(self):
    ''' Collect up all the data of this Block and return a single bytes instance.

        This is painfully named because it may be very expensive.
    '''
    chunks = list(self.datafrom())
    if not chunks:
      return b''
    if len(chunks) == 1:
      return chunks[0]
    return b''.join(chunks)

  @property
  def data(self):
    ''' This provides very easy access to `get_spanned_data()`.
        It is overridden on `IndirectBlock` because of the likely expense.
    '''
    return self.get_spanned_data()

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

        Parameters:
        * `force`: if true, create a new blockmap anyway; default: `False`
        * `blockmapdir`: directory to hold persistent block maps
    '''
    if force:
      blockmap = None
    else:
      blockmap = self.blockmap
    if blockmap is None:
      warning("making blockmap for %s", self)
      from .blockmap import BlockMap  # pylint: disable=import-outside-toplevel
      if blockmapdir is None:
        blockmapdir = defaults.S.blockmapdir
      self.blockmap = blockmap = BlockMap(self, blockmapdir=blockmapdir)
    return blockmap

  def bufferfrom(self, offset=0, **kw):
    ''' Return a CornuCopyBuffer presenting data from the Block.
    '''
    return CornuCopyBuffer(self.datafrom(start=offset, **kw), offset=offset)

  @require(lambda self, start, end: 0 <= start <= end <= len(self))
  def slices(self, start, end, no_blockmap=False):
    ''' Return an iterator yielding (Block, start, len) tuples
        representing the leaf data covering the supplied span `start`:`end`.

        The iterator may end early if the span exceeds the Block data.
    '''
    if self.indirect:
      if not no_blockmap:
        # use the blockmap to access the data if present
        blockmap = self.blockmap
        if blockmap:
          yield from blockmap.slices(start, end - start)
          return
      offset = 0
      subblocks = self.subblocks
      HashCodeBlock.need_direct_data(subblocks)
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

  @require(lambda self, start, end: 0 <= start <= end <= len(self))
  def top_slices(self, start, end):
    ''' Return an iterator yielding (Block, start, len) tuples
        representing the uppermost Blocks spanning `start:end`.

        The originating use case is to support providing minimal
        Block references required to assemble a new indirect Block
        consisting of data from this Block comingled with updated
        data without naively layering deeper levels of Block
        indirection with every update phase.
    '''
    if self.indirect:
      offset = 0  # the absolute index of the left edge of subblock B
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

  @require(lambda self, start, end: 0 <= start <= end <= len(self))
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
              " but Block is indirect! should be a partial leaf" %
              (B, Bstart, Bend)
          )
        yield SubBlock(B, Bstart, Bend - Bstart)

  @require(lambda self, start, end: 0 <= start <= end <= len(self))
  def spliced(self, start, end, new_block):
    ''' Generator yielding Blocks producing the data
        from `self` with the range `start:end`
        replaced by the data from `new_block`.
    '''
    if start == len(self):
      yield self
    else:
      yield from self.top_blocks(0, start)
    yield new_block
    if end < len(self):
      yield from self.top_blocks(end, len(self))

  @require(lambda self, start, end: 0 <= start <= end <= len(self))
  def splice(self, start, end, new_block):
    ''' Return a new Block consisting of `self` with the span
        `start:end` replaced by the data from `new_block`.
    '''
    from .blockify import top_block_for  # pylint: disable=import-outside-toplevel
    return top_block_for(self.spliced(start, end, new_block))

  def open(self, mode="rb"):
    ''' Open the block as a file.
    '''
    # pylint: disable=import-outside-toplevel
    from .file import ROBlockFile, RWBlockFile
    if mode == 'rb':
      return ROBlockFile(self)
    if mode == 'w+b':
      return RWBlockFile(backing_block=self)
    raise ValueError(
        "unsupported open mode, expected 'rb' or 'w+b', got: %r" % (mode,)
    )

  def pushto_queue(self, Q, runstate=None, progress=None):
    ''' Push this Block and any implied subblocks to a queue.

        Parameters:
        * `Q`: optional preexisting Queue, which itself should have
          come from a .pushto targetting the Store `S2`.
        * `runstate`: optional RunState used to cancel operation
        * `progress`: optional Progress to update its total

        TODO: optional `no_wait` parameter to control waiting,
        default `False`, which would support closing the Queue but
        not waiting for the worker completion. This is on the premise
        that the final Store shutdown of `S2` will wait for outstanding
        operations anyway.
    '''
    with defaults.S:
      if progress:
        progress.total += len(self)
      Q.put(self)
      if self.indirect:
        # recurse, reusing the Queue
        for subB in self.subblocks:
          if runstate and runstate.cancelled:
            warning("%s: push cancelled", self)
            break
          subB.pushto_queue(Q, runstate=runstate, progress=progress)

class BlockRecord(BinarySingleValue):
  ''' Support for binary parsing and transcription of blockrefs.
  '''

  TEST_CASES = (
      # zero length hashcode block
      # note that the hashcode doesn't match that of b''
      b'\x17\0\0' + b'\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0',
  )

  FIELD_TYPES = dict(value=_Block)

  def __init__(self, B):
    assert isinstance(B, _Block)
    super().__init__(B)

  @property
  def block(self):
    ''' Alias `.value` as `.block`.
    '''
    return self.value

  @staticmethod
  # pylint: disable=arguments-differ,too-many-branches,too-many-locals
  def parse_value(bfr):
    ''' Decode a Block reference from a buffer.

        Format is a `BSData` holding this encoded data:

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
    raw_encoding = BSData.parse_value(bfr)
    blockref_bfr = CornuCopyBuffer.from_bytes(raw_encoding)
    flags = BSUInt.parse_value(blockref_bfr)
    is_indirect = bool(flags & F_BLOCK_INDIRECT)
    is_typed = bool(flags & F_BLOCK_TYPED)
    has_type_flags = bool(flags & F_BLOCK_TYPE_FLAGS)
    unknown_flags = flags & ~(
        F_BLOCK_INDIRECT | F_BLOCK_TYPED | F_BLOCK_TYPE_FLAGS
    )
    if unknown_flags:
      raise ValueError(
          "unexpected flags value (0x%02x) with unsupported flags=0x%02x" %
          (flags, unknown_flags)
      )
    span = BSUInt.parse_value(blockref_bfr)
    if is_indirect:
      # With indirect blocks, the span is of the implied data, not
      # the referenced block's data. Therefore we build the referenced
      # block with a span of None and store the span in the indirect
      # block.
      ispan = span
      span = None
    # block type, default BT_HASHCODE
    if is_typed:
      block_type = BlockType(BSUInt.parse_value(blockref_bfr))
    else:
      block_type = BlockType.BT_HASHCODE
    if has_type_flags:
      type_flags = BSUInt.parse_value(blockref_bfr)
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
      suboffset = BSUInt.parse_value(blockref_bfr)
      superB = BlockRecord.parse_value(blockref_bfr)
      # wrap inner Block in subspan
      B = SubBlock(superB, suboffset, span)
    else:
      raise ValueError("unsupported Block type 0x%02x" % (block_type,))
    if is_indirect:
      B = IndirectBlock(B, span=ispan)
    if not blockref_bfr.at_eof():
      warning(
          "unparsed data (%d bytes) follow Block %s",
          len(raw_encoding) - blockref_bfr.offset, B
      )
    assert isinstance(B, _Block)
    return B

  @staticmethod
  # pylint: disable=arguments-differ
  def transcribe_value(B):
    ''' Transcribe this `Block`, the inverse of parse_value.
    '''
    transcription = []
    is_indirect = B.indirect
    span = B.span
    if is_indirect:
      # aside from the span, everything else comes from the superblock
      B = B.superblock
    block_type = B.type
    assert block_type >= 0, "block_type(%s) => %d" % (B, B.type)
    block_typed = block_type != BlockType.BT_HASHCODE
    flags = (
        (F_BLOCK_INDIRECT if is_indirect else 0)
        | (F_BLOCK_TYPED if block_typed else 0)
        | 0  # no F_BLOCK_TYPE_FLAGS
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
      transcription.append(BSUInt.transcribe_value(B.offset))
      transcription.append(BlockRecord.transcribe_value(B.superblock))
    else:
      raise ValueError("unsupported Block type 0x%02x: %s" % (block_type, B))
    block_bs = b''.join(flatten_transcription(transcription))
    return BSData(block_bs).transcribe()

@lru_cache(maxsize=1024 * 1024, typed=True)
def get_HashCodeBlock(hashcode):
  ''' Caching constructor for HashCodeBlocks of known code.
  '''
  if hashcode is None:
    raise ValueError("invalid hashcode, may not be None")
  return HashCodeBlock(hashcode=hashcode)

class HashCodeBlock(_Block):
  ''' A Block reference based on a Store hashcode.
  '''

  transcribe_prefix = 'B'

  def __init__(self, hashcode=None, data=None, added=False, span=None, **kw):
    ''' Initialise a `BT_HASHCODE` Block.

        A HashCodeBlock always stores its hashcode directly.
        If `data` is supplied, store it and compute or check the hashcode.
        If `span` is not `None`, store it. Otherwise compute it on
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
              "supplied hashcode %r != saved hash for data (%r : %r)" %
              (hashcode, h, data)
          )
    self._data = data
    self._span = None
    _Block.__init__(self, BlockType.BT_HASHCODE, span=span, **kw)
    self.hashcode = hashcode

  @property
  def data(self):
    ''' The data stored in this Block.
    '''
    return self.get_direct_data()

  def get_direct_data(self):
    ''' Return the direct data of this Block, fetching it if necessary.
    '''
    with self._lock:
      data = self._data
      if data is None:
        data = self._data = defaults.S[self.hashcode]
    return data

  @classmethod
  def need_direct_data(cls, blocks):
    ''' Bulk request the direct data for `blocks`.
    '''
    S = defaults.S
    Rs = []
    for B in blocks:
      try:
        d = B._data
      except AttributeError:
        # not a HashCodeBlock-like Block
        continue
      if d is not None:
        # data obtained already
        continue
      try:
        _ = B.hashcode
      except AttributeError as e:
        if isinstance(B, HashCodeBlock):
          error("need_direct_data: B=%s: %s", B, e)
      else:
        # request the data
        Rs.append(S._defer(B.get_direct_data))
    for R in Rs:
      R.join()

  @prop
  def span(self):  # pylint: disable=method-hidden
    ''' Return the data length, computing it from the data if required.
    '''
    _span = self._span
    if _span is None:
      self._span = _span = len(self._data)
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
            "%s: tried to change .span from %s to %s" %
            (self, self._span, newspan)
        )
      raise RuntimeError("SECOND UNEXPECTED")

  def datafrom(self, start=0, end=None):
    ''' Generator yielding data from `start:end`.
    '''
    if start < 0:
      raise ValueError("invalid start:%d" % (start,))
    if end is not None and end < start:
      raise ValueError("invalid end:%d < start:%d" % (end, start))
    bs = self.get_direct_data()
    if start == 0:
      if end is None:
        yield bs
      else:
        yield bs[:end]
    else:
      yield bs[start:end]

  def transcribe_inner(self, T, fp):
    m = {'hash': self.hashcode}
    if self._span is not None:
      m['span'] = self._span
    return T.transcribe_mapping(m, fp)

  @classmethod
  # pylint: disable=too-many-arguments
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    m, offset = T.parse_mapping(s, offset, stopchar)
    span = m.pop('span', None)
    hashcode = m.pop('hash')
    if m:
      raise ValueError("unexpected fields: %r" % (m,))
    B = cls(hashcode=hashcode, span=span)
    return B, offset

  @io_fail
  def fsck(self, recurse=False):  # pylint: disable=unused-argument
    ''' Check this HashCodeBlock.
    '''
    ok = True
    hashcode = self.hashcode
    with Pfx("%s", hashcode):
      with defaults.S as S:
        try:
          data = S[hashcode]
        except KeyError:
          error("not in Store %s", S)
          ok = False
        else:
          if len(self) != len(data):
            error("len(self)=%d, len(data)=%d", len(self), len(data))
            ok = False
          h = S.hash(data)
          if h != hashcode:
            error("hash(data):%s != self.hashcode:%s", h, hashcode)
            ok = False
    return ok

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
      raise ValueError(
          "span(%d) does not match data (%d bytes)" % (span, len(data))
      )
    if len(data) > 32:
      B = HashCodeBlock(data=data, hashcode=hashcode, span=span, added=added)
    else:
      B = LiteralBlock(data=data)
  return B

class IndirectBlock(_Block):
  ''' An indirect block,
      whose direct data consists of references to subsidiary Blocks.
  '''

  transcribe_prefix = 'I'

  def __init__(self, superblock, span=None):
    if superblock.indirect:
      raise ValueError(
          "superblock may not be indirect: superblock=%s" % (superblock,)
      )
    super().__init__(BlockType.BT_INDIRECT, 0)
    self.indirect = True
    self.superblock = superblock
    self._subblocks = None
    # now we can compute the span from the subblocks
    if span is None:
      span = sum(subB.span for subB in self.subblocks)
    self.span = span
    self.hashcode = superblock.hashcode

  @property
  def data(self):
    ''' Prevent use of `.data` on IndirectBlock` instances.
        Use `get_spanned_data()` if you really need a flat `bytes` instance.
    '''
    raise RuntimeError(
        "no .data on %s, likely to be expensive; it truly required, call get_spanned_data()"
        % (type(self),)
    )

  @classmethod
  def from_hashcode(cls, hashcode, span):
    ''' Construct an `IndirectBlock` from the `hashcode`
        for its direct data and the `span` of bytes
        covers.
    '''
    return cls(get_HashCodeBlock(hashcode), span=span)

  @classmethod
  def from_subblocks(cls, subblocks, force=False):
    ''' Construct an `IndirectBlock` from `subblocks`.

        If `force`, always return an `IndirectBlock`.
        Otherwise (the default), return an empty `LiteralBlock` if the span==0
        or a `HashCodeBlock` if `len(subblocks)==1`.
    '''
    if isinstance(subblocks, _Block):
      subblocks = (subblocks,)
    elif isinstance(subblocks, bytes):
      subblocks = (Block(data=subblocks),)
    else:
      subblocks = tuple(subblocks)
    spans = [subB.span for subB in subblocks]
    span = sum(spans)
    if not force:
      if span == 0:
        return Block(data=b'')
      if len(subblocks) == 1:
        return subblocks[0]
    superBdata = b''.join(subB.encode() for subB in subblocks)
    superblock = HashCodeBlock(data=superBdata)
    return cls(superblock, span=span)

  @prop
  @locked
  def subblocks(self):
    ''' The immediate subblocks of this indirect block.
    '''
    blocks = self._subblocks
    if blocks is None:
      blocks = self._subblocks = tuple(
          map(
              lambda BR: BR.block,
              BlockRecord.scan(self.superblock.bufferfrom())
          )
      )
    return blocks

  def transcribe_inner(self, T, fp):
    ''' Transcribe "span:Block".
    '''
    fp.write(str(self.span))
    fp.write(':')
    T.transcribe(self.superblock, fp=fp)

  @classmethod
  # pylint: disable=too-many-arguments
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    ''' Parse "span:Block"
    '''
    span, offset2 = get_decimal_value(s, offset)
    if s[offset2] != ':':
      raise ValueError(
          "offset %d: missing colon after span(%d)" % (offset2, span)
      )
    offset = offset2 + 1
    superB, offset = parse(s, offset, T)
    return cls(superB, span), offset

  def datafrom(self, start=0, end=None):
    ''' Yield data from a point in the Block.
    '''
    if end is None:
      end = self.span
    if start >= end:
      return
    block_cache = defaults.S.block_cache
    if block_cache:
      try:
        bm = block_cache[self.hashcode]
      except KeyError:
        pass
      else:
        filled = bm.filled
        if filled > start:
          maxlength = end - start
          for bs in bm.datafrom(start, maxlength=maxlength):
            yield bs
            start += len(bs)
            assert start <= end
    if start < end:
      for B, Bstart, Bend in self.slices(start, end):
        assert not B.indirect
        yield B.get_direct_data()[Bstart:Bend]

  @io_fail
  def fsck(self, recurse=False):
    ''' Check this IndirectBlock.
    '''
    ok = True
    span = self.span
    subspan = sum(subB.span for subB in self.subblocks)
    if span != subspan:
      error("span:%d != sum(subblocks.span):%d", span, subspan)
      ok = False
    if recurse:
      runstate = defaults.runstate
      for subB in self.subblocks:
        if runstate.cancelled:
          error("cancelled")
          ok = False
          break
        with Pfx(str(subB)):
          if not subB.fsck(recurse=True):
            ok = False
    return ok

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
          "octet should be an int or a bytes instance but is %s: %r" %
          (type(octet), octet)
      )
    if len(octet) != 1:
      raise ValueError("len(octet):%d != 1" % (len(octet),))
    _Block.__init__(self, BlockType.BT_RLE, span=span, **kw)
    self.octet = octet

  def get_direct_data(self):
    ''' The full RLEBlock.
    '''
    return self.octet * self.span

  def datafrom(self, start=0, end=None):
    ''' Yield the data from `start` to `end`.
    '''
    if end is None:
      end = self.span
    if end > self.span:
      end = self.span
    length = end - start
    if length < 0:
      raise ValueError("end(%s) < start(%s)" % (end, start))
    yield self.octet * length

  def transcribe_inner(self, T, fp):
    return T.transcribe_mapping({'span': self.span, 'octet': self.octet}, fp)

  @classmethod
  # pylint: disable=too-many-arguments
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    m = T.parse_mapping(s, offset, stopchar)
    span = m.pop('span')
    octet = m.pop('octet')
    if m:
      raise ValueError("unexpected fields: %r" % (m,))
    return cls(span, octet), offset

  @io_fail
  def fsck(self, recurse=False):  # pylint: disable=unused-argument
    ''' Check this RLEBlock.
    '''
    ok = True
    if not isinstance(self.octet, bytes):
      error(
          "octet is not a bytes instance: type=%s",
          type(self.octet).__name__
      )
      ok = False
    elif len(self.octet) != 1:
      error("len(self.octet) != 1: %d", len(self.octet))
      ok = False
    if self.span < 1:
      error("span < 1: %d", self.span)
      ok = False
    return ok

register_transcriber(RLEBlock)

class LiteralBlock(_Block):
  ''' A LiteralBlock is for data too short to bother hashing and Storing.
  '''

  transcribe_prefix = 'LB'

  def __init__(self, data, **kw):
    _Block.__init__(self, BlockType.BT_LITERAL, span=len(data), **kw)
    self.data = data

  def transcribe_inner(self, T, fp):
    ''' Transcribe the block data in texthexified form.
    '''
    fp.write(hexify(self.data))

  @classmethod
  # pylint: disable=too-many-arguments
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    ''' Parse the interior of the transcription: texthexified data.
    '''
    endpos = s.find(stopchar, offset)
    if endpos < offset:
      raise ValueError("stopchar %r not found" % (stopchar,))
    data = untexthexify(s[offset:endpos])
    return cls(data), endpos

  def get_direct_data(self):
    ''' Return the direct data of this Block>
    '''
    return self.data

  @require(
      lambda self, start, end: 0 <= start and
      (end is None or start <= end <= len(self))
  )
  def datafrom(self, start=0, end=None):
    ''' Yield data from this block.
    '''
    if end is None:
      end = self.span
    yield self.data[start:end]

  @io_fail
  def fsck(self, recurse=False):  # pylint: disable=unused-argument
    ''' Check this LiteralBlock.
    '''
    ok = True
    data = self.data
    if len(self) != len(data):
      error("len(self)=%d, len(data)=%d", len(self), len(data))
      ok = False
    return ok

register_transcriber(LiteralBlock)

def SubBlock(superB, suboffset, span, **kw):
  ''' Factory for SubBlocks.
      Returns origin Block if suboffset==0 and span==len(superB).
      Returns am empty LiteralBlock if the span==0.
  '''
  with Pfx("SubBlock(suboffset=%d,span=%d,superB=%s)", suboffset, span,
           superB):
    if span == 0:
      return LiteralBlock(b'')
    if suboffset == 0 and span == len(superB):
      return superB
    if isinstance(superB, _SubBlock):
      return _SubBlock(superB.superblock, suboffset + superB.offset, span)
    return _SubBlock(superB, suboffset, span, **kw)

class _SubBlock(_Block):
  ''' A SubBlock is a view into another block.
      A SubBlock may not be empty and may not cover the whole of its superblock.
  '''

  transcribe_prefix = 'SubB'

  @require(lambda superB, suboffset: 0 <= suboffset < len(superB))
  @require(
      lambda superB, suboffset, span:
      (span > 0 and suboffset + span <= len(superB))
  )
  def __init__(self, superB, suboffset, span, **kw):
    with Pfx("_SubBlock(suboffset=%d, span=%d)[len(superB)=%d]", suboffset,
             span, len(superB)):
      if suboffset == 0 and span == len(superB):
        raise RuntimeError(
            'tried to make a SubBlock spanning all of of SuperB'
        )
      _Block.__init__(self, BlockType.BT_SUBBLOCK, span, **kw)
      self.superblock = superB
      self.offset = suboffset

  def get_direct_data(self):
    ''' The direct data are the spanned data.
    '''
    return self.get_spanned_data()

  def datafrom(self, start=0, end=None):
    ''' Yield the data from this Block between `start` and `end`.
    '''
    if start < 0 or (end is not None and end < 0):
      raise ValueError("invalid start(%s) or end(%s)" % (start, end))
    start = self.offset + start
    if end is None:
      end = self.span
    end = self.offset + end
    return self.superblock.datafrom(start, end)

  def __getitem__(self, index):
    if isinstance(index, slice):
      return self.data[index]
    if index < 0 or index >= self.span:
      raise IndexError("index %d outside span %d" % (index, self.span))
    return self.superblock[self.offset + index]

  def transcribe_inner(self, T, fp):
    return T.transcribe_mapping(
        {
            'block': self.superblock,
            'offset': self.offset,
            'span': self.span
        }, fp
    )

  @classmethod
  # pylint: disable=too-many-arguments
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    offset, block, suboffset, subspan = T.parse_mapping(
        s, offset, stopchar, required=('block', 'offset', 'span')
    )
    return cls(block, suboffset, subspan), offset

  @io_fail
  def fsck(self, recurse=False):  # pylint: disable=unused-argument
    ''' Check this SubBlock.
    '''
    ok = True
    superB = self.superblock
    suboffset = self.offset
    span = self.span
    if isinstance(superB, _SubBlock):
      error("superblock is a subblock type: %s", type(superB).__name__)
      ok = False
    if suboffset < 0 or suboffset >= len(superB):
      error("offset:%d out of range 0:%d", suboffset, len(superB))
      ok = False
    if span < 1 or span > len(superB) - suboffset:
      error("span:%d out of range 0:%d", span, len(superB) - suboffset)
      ok = False
    return ok

register_transcriber(_SubBlock)

if __name__ == '__main__':
  from .block_tests import selftest
  selftest(sys.argv)
