#!/usr/bin/python

from __future__ import print_function
from abc import ABC
from enum import IntEnum, unique as uniqueEnum
import sys
from threading import RLock
from cs.lex import texthexify, untexthexify
from cs.logutils import warning
from cs.pfx import Pfx
from cs.py.func import prop
from cs.serialise import get_bs, put_bs
from cs.threads import locked, locked_property
from . import defaults, totext
from .blockmap import BlockMap
from .hash import decode as hash_decode
from .transcribe import Transcriber, register as register_transcriber

F_BLOCK_INDIRECT = 0x01     # indirect block
F_BLOCK_TYPED = 0x02        # block type provided, otherwise BT_HASHCODE
F_BLOCK_TYPE_FLAGS = 0x04   # type-specific flags follow type

@uniqueEnum
class BlockType(IntEnum):
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
        B = HashCodeBlock(hashcode=hashcode, span=span, indirect=is_indirect)
      elif block_type == BlockType.BT_RLE:
        octet = bs[offset]
        offset += 1
        B = RLEBlock(span, octet, indirect=is_indirect)
      elif block_type == BlockType.BT_LITERAL:
        offset1 = offset + span
        data = bs[offset:offset1]
        if len(data) != span:
          raise ValueError("expected %d literal bytes, got %d" % (span, len(data)))
        offset = offset1
        B = LiteralBlock(data, indirect=is_indirect)
      elif block_type == BlockType.BT_SUBBLOCK:
        suboffset, offset = get_bs(bs, offset)
        SuperB, offset = decodeBlock(bs, offset, length=length-(offset-offset0a))
        # wrap inner Block in subspan
        B = SubBlock(SuperB, suboffset, span, indirect=is_indirect)
      else:
        raise ValueError("unsupported Block type 0x%02x" % (block_type,))
      # check that we decoded the correct number of bytes
      if offset - offset0a > length:
        raise ValueError("overflow decoding Block: length should be %d, but decoded %d bytes" % (length, offset - offset0))
      if offset - offset0a < length:
        raise ValueError("underflow decoding Block: length should be %d, but decoded %d bytes" % (length, offset - offset0))
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

def isBlock(o):
  return isinstance(o, _Block)

class _Block(Transcriber, ABC):

  def __init__(self, block_type, span, *, indirect=False):
    if not isinstance(span, int) or span < 0:
      raise ValueError("invalid span: %r", span)
    self.type = block_type
    self.span = span
    self.indirect = indirect
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
    return b''.join(encoded_Block_fields(flags, span, block_type, block_type_flags, chunks))

  def __getattr__(self, attr):
    if attr == 'subblocks':
      if not self.indirect:
        raise AttributeError('%r: Block is direct, no .subblocks' % (attr,))
      self.subblocks = Bs = tuple(decodeBlocks(self.data))
      return Bs
    raise AttributeError(attr)

  def __getitem__(self, index):
    ''' Return specified direct data.
    '''
    return self.data[index]

  def __len__(self):
    ''' len(Block) is the length of the encompassed data.
    '''
    return self.span

  def all_data(self):
    '''
    '''
    if self.indirect:
      return b''.join(leaf.data for leaf in self.leaves)
    return self.data

  def matches_data(self, odata):
    ''' Check supplied bytes `odata` against this Block's hashcode.
        NB: _not_ defined on indirect Blocks to avoid mistakes.
    '''
    try:
      h = self.hashcode
    except AttributeError:
      return self.data == odata
    return h == h.from_chunk(odata)

  @property
  def leaves(self):
    ''' Return the leaf (direct) blocks.
    '''
    if self.indirect:
      for B in self.subblocks:
        yield from B.leaves
    elif self.span > 0:
      yield self

  @locked
  def get_blockmap(self, force=False):
    ''' Get the blockmap for this block, creating it if necessary.
        `force`: if True, create a new blockmap anyway; default: False
    '''
    if force:
      blockmap = None
    else:
      try:
        blockmap = self.blockmap
      except AttributeError:
        blockmap = None
    if blockmap is None:
      warning("making blockmap for %s", self)
      self.blockmap = blockmap = BlockMap(self)
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
        try:
          blockmap = self.blockmap
        except AttributeError:
          pass
        else:
          yield from blockmap.slices(start, end - start)
          return
      offset = 0
      for B in self.subblocks:
        sublen = len(B)
        substart = max(0, start - offset)
        subend = min(sublen, end - offset)
        if substart < subend:
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

class HashCodeBlock(_Block):

  def __init__(self, hashcode=None, data=None, added=False, span=None, **kw):
    ''' Initialise a BT_HASHCODE Block or IndirectBlock.
        A HashCodeBlock always stores its hashcode directly.
        If `data` is supplied, store it and compute or check the hashcode.
        NB: The data are not kept in memory; fetched on demand.
        `added`: if true, do not add the data to the current Store
    '''
    if data is None:
      if hashcode is None:
        raise ValueError("one of data or hashcode must be not-None")
      else:
        if span is None:
          raise ValueError("use of a bare hashcode requires a span")
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
    _Block.__init__(self, BlockType.BT_HASHCODE, span=span, **kw)
    self.hashcode = hashcode

  def __getattr__(self, attr):
    if attr == 'data':
      self.data = bs = defaults.S[self.hashcode]
      return bs
    return super().__getattr__(attr)

  def encode(self):
    flags = 0
    if self.indirect:
      flags |= F_BLOCK_INDIRECT
    hashcode = self.hashcode
    return self._encode(flags, self.span, BlockType.BT_HASHCODE, 0,
                        ( hashcode.encode(), ))

  @prop
  def transcribe_prefix(self):
    ''' Transcription prefix: IB for an IndirectBlock and B for a direct Block.
    '''
    return 'IB' if self.indirect else 'B'

  def transcribe_inner(self, T, fp):
    m = {'span': self.span, 'hash': self.hashcode}
    return T.transcribe_mapping(m, fp)

  @classmethod
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    m, offset = T.parse_mapping(s, offset, stopchar)
    span = m.pop('span')
    hashcode = m.pop('hash')
    if prefix == 'B':
      indirect = False
    elif prefix == 'IB':
      indirect = True
    else:
      warning("unexpected prefix %r, setting indirect=False", prefix)
      indirect = False
    if m:
      raise ValueError("unexpected fields: %r" % (m,))
    B = cls(hashcode=hashcode, span=span, indirect=indirect)
    return B, offset

register_transcriber(HashCodeBlock, ('B', 'IB'))

def Block(hashcode=None, data=None, span=None, added=False, indirect=False):
  ''' Factory function for a Block.
  '''
  if data is None:
    B = HashCodeBlock(hashcode=hashcode, span=span, indirect=indirect)
  else:
    if span is None:
      span = len(data)
    elif span != len(data):
      raise ValueError("span(%d) does not match data (%d bytes)"
                       % (span, len(data)))
    if len(data) > 32:
      B = HashCodeBlock(data=data, hashcode=hashcode, span=span, added=added)
    else:
      B = LiteralBlock(data=data, indirect=indirect)
  return B

def IndirectBlock(subblocks=None, hashcode=None, span=None):
  ''' An indirect Block.

      Indirect blocks may be initialised in two ways:

      The first way is specified by supplying the `subblocks`
      parameter, an iterable of Blocks to be referenced by this
      IndirectBlock. The referenced Blocks are encoded and assembled
      into the data for this Block.

      The second way is to supplying the `hashcode` and `span` for an
      existing Stored block, whose content to initialise an IndirectBlock is
      with a hashcode and a span indicating the length of the data
      encompassed by the block speified by the hashcode; the data of that
      Block can be decoded to obtain the reference Blocks for this
      IndirectBlock.

      TODO: allow data= initialisation, to decode raw iblock data.
  '''

  if subblocks is None:
    if hashcode is None:
      raise ValueError("one of subblocks or hashcode must be supplied")
    if span is None:
      raise ValueError("no span supplied with hashcode %s" % (hashcode,))
    B = HashCodeBlock(hashcode=hashcode, span=span, indirect=True)
  else:
    # subblocks specified
    if hashcode is not None:
      raise ValueError("only one of hashocde and subblocks may be supplied")
    subspan = sum(subB.span for subB in subblocks)
    if span is None:
      span = subspan
    elif span != subspan:
      raise ValueError("span(%d) does not match subblocks (totalling %d)"
                       % (span, subspan))
    if not isinstance(subblocks, (tuple, list)):
      subblocks = tuple(subblocks)
    span = sum(subB.span for subB in subblocks)
    B = HashCodeBlock(data=b''.join(encodeBlocks(subblocks)), span=span, indirect=True)
  return B

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

  @property
  def data(self):
    ''' The data of this RLEBlock.
    '''
    return self.octet * self.span

  def encode(self):
    ''' Return the binary transcription of an RLEBlock.
    '''
    return self._encode(0, self.span, BlockType.BT_RLE, 0, ( self.octet, ))

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

  def encode(self):
    ''' Return the binary transcription of a LiteralBlock.
    '''
    return self._encode(0, self.span, BlockType.BT_LITERAL, 0,
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

def SubBlock(B, suboffset, span, **kw):
  ''' Factory for SubBlocks: returns origin Block if suboffset==0 and span==len(B).
  '''
  with Pfx("SubBlock(%s)", B):
    # check offset and span here because we trust them later
    if suboffset < 0 or suboffset > len(B):
      raise ValueError("suboffset(%d) out of range", suboffset)
    if span < 0 or suboffset + span > len(B):
      raise ValueError("span(%d) out of range", span)
    if span == 0:
      warning("span==0, returning empty LiteralBlock")
      return LiteralBlock(b'')
    if suboffset == 0 and span == len(B):
      warning("covers full Block, returning original")
      return B
    if isinstance(B, _SubBlock):
      return _SubBlock(B._superblock, suboffset + B._offset, span)
    return _SubBlock(B, suboffset, span, **kw)

class _SubBlock(_Block):
  ''' A SubBlock is a view into another block.
      A SubBlock may not be empty and may not cover the whole of its superblock.
  '''

  transcribe_prefix = 'SubB'

  def __init__(self, SuperB, suboffset, span, **kw):
    with Pfx("_SubBlock(suboffset=%d, span=%d)[len(SuperB)=%d]",
             suboffset, span, len(SuperB)):
      if suboffset < 0 or suboffset >= len(SuperB):
        raise ValueError('suboffset out of range 0-%d: %d' % (len(SuperB)-1, suboffset))
      if span < 0 or suboffset+span > len(SuperB):
        raise ValueError(
            'span must be nonnegative and less than %d (suboffset=%d, len(superblock)=%d): %d'
            % (len(SuperB)-suboffset, suboffset, len(SuperB), span))
      if suboffset == 0 and span == len(SuperB):
        raise RuntimeError('tried to make a SubBlock spanning all of of SuperB')
      _Block.__init__(self, BlockType.BT_SUBBLOCK, span, **kw)
      self._superblock = SuperB
      self._offset = suboffset

  def __getattr__(self, attr):
    if attr == 'data':
      self.data = bs = self.all_data()
      return bs
    return super().__getattr__(attr)

  def all_data(self):
    ''' The full data for this block.
    '''
    # TODO: _Blocks need a subrange method that is efficient for indirect blocks
    return self._superblock.all_data()[self._offset:self._offset + self.span]

  def encode(self):
    ''' Return the binary transcription of a SubBlock.
    '''
    return self._encode(0, self.span, BlockType.BT_SUBBLOCK, 0,
                        ( put_bs(self._offset),
                          self._superblock.encode(),
                        ))

  def __getitem__(self, index):
    if isinstance(index, slice):
      return self.data[index]
    if index < 0 or index >= self.span:
      raise IndexError("index %d outside span %d" % (index, self.span))
    return self._superblock[self._offset+index]

  def transcribe_inner(self, T, fp):
    return T.transcribe_mapping({
        'block': self._superblock,
        'offset': self._offset,
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
    pass
  else:
    if hashcode not in S:
      yield B, "hashcode not in %s" % (S,)
    else:
      data = B.data
      # hash the data using the matching hash function
      data_hashcode = hashcode.hashfunc(data)
      if hashcode != data_hashcode:
        yield B, "hashcode(%s) does not match hashfunc of data(%s)" \
                 % (hashcode, data_hashcode)
  if B.indirect:
    if recurse:
      for subB in B.subblocks:
        verify_block(subB, recurse=True, S=S)
  else:
    # direct block: verify data length
    data = B.data
    if B.span != len(data):
      yield B, "span(%d) != len(data:%d)" % (B.span, len(data))

if __name__ == '__main__':
  from .block_tests import selftest
  selftest(sys.argv)
