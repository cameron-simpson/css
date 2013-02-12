#!/usr/bin/python

import sys
from threading import RLock
from cs.logutils import D
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
  ##D("flags=0x%02x", flags)
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

  def __init__(self, data=None, hashcode=None, span=None, doStore=False, doFlush=False):
    if data is None and hashcode is None:
      raise ValueError("one of data or hashcode must be not-None")
    self._lock = RLock()
    self._data = data
    self._hashcode = hashcode
    self._span = span
    if doStore:
      self.store(doFlush)

  def __str__(self):
    return self.textEncode()

  @locked_property
  def data(self):
    ''' The direct data of this Block.
        i.e. _not_ the data implied by an indirect Block.
    '''
    return defaults.S[self.hashcode]

  @locked_property
  def hashcode(self):
    ''' Return the hashcode for this block.
        Compute the hashcode if unknown or if it does not match the default
        store's default hashtype.
        When the block's current hashcode is the wrong type and this is a
        "hash only" block, the recompute has an implied fetch from the store
        using the old/wrong hashcode, so the store must support both.
    '''
    data = self._data
    if data is None:
      raise RuntimeError("tried to compute hashcode but _data is None")
    return defaults.S.hash(data)

  @locked_property
  def span(self):
    sp = 0
    for chunk in self.chunks:
      sp += len(chunk)
    return sp

  def _flush(self):
    if self._hashcode is None:
      self.store()
    self._data = None

  def __getitem__(self, index):
    ''' Return specified direct data.
    '''
    return self.data[index]

  def __len__(self):
    if not self.indirect:
      # avoid data fetch for direct blocks
      return self.span
    return len(self.data)

  def store(self, flush=False):
    ''' Ensure this block is stored.
        If `discard` is true, release the block's data.
    '''
    hashcode = defaults.S.add(self.data)
    if self._hashcode is None:
      self._hashcode = hashcode
    elif hashcode != self._hashcode:
      raise RuntimeError(
              "store()d hashcode does not match cache: self._hashcode=%r, stored=%r"
              % (self._hashcode, hashcode))
    if flush:
      self._flush()

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

  def all_data(self):
    return b''.join(self.chunks)

  def textEncode(self):
    return totext(self.encode())

  def open(self, mode="rb"):
    ''' Open the block as a file.
    '''
    if mode != 'rb':
      raise ValueError("unsupported open mode, require 'rb', got: %s", mode)
    from cs.ventifile import ReadFile
    return ReadFile(self)

class Block(_Block):
  ''' A direct block.
  '''
  def __init__(self, **kw):
    ''' Initialise a direct block, supplying data bytes or hashcode,
        but not both.
    '''
    _Block.__init__(self, **kw)
    self.indirect = False

  @property
  def leaves(self):
    yield self

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
      _Block.__init__(hashcode=hashcode, span=span)
    else:
      _Block.__init__(self, data=encodeBlocks(subblocks))
    self.indirect = True

  @locked_property
  def subblocks(self):
    return tuple(decodeBlocks(self.data))

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

if __name__ == '__main__':
  import cs.venti.block_tests
  cs.venti.block_tests.selftest(sys.argv)
