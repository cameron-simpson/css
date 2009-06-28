#!/usr/bin/python

from cs.venti import defaults
from cs.venti.hash import Hash_SHA1, HASH_SHA1_T

F_BLOCK_INDIRECT = 0x01 # indirect block
F_BLOCK_HASHTYPE = 0x02 # hash type explicit

def encodeBlock(B):
  ''' Encode a Block for storage:
      Format is:
        BS(flags)
          0x01 indirect block
          0x02 hashtype != HASH_SHA1_T
        BS(span)
        [BS(hashtype)[BS(hashlen)]]
        hash
  '''
  flags=0
  if self.indirect:
    flags |= F_BLOCK_INDIRECT
  hashcode = B.hashcode()
  hashnum = hashcode.hashenum
  if hashenum == HASH_SHA1_T:
    hashtype_enc = bytes()
  else:
    hashtype_enc = toBS(hashenum)
  hashcode_enc = hashcode.encode()
  return toBS(flags)+toBS(len(B))+hashtype_enc+hashcode_enc

def decodeBlock(s, justone=False):
  ''' Decode a Block reference and return it.
      Format is:
        BS(flags)
          0x01 indirect blockref
          0x02 non-SHA1 hashcode
        BS(span)
        [BS(hashtype)[BS(hashlen)]]
        hash
      Returns a Block (or IndirectBlock) and the tail of 's'.
      If the optional paramater 'justone' is true, check that 's'
      is a complete Block ref encoding with nothing left over
      and return just the Block.
  '''
  s0=s
  flags, s = fromBS(s)
  unknown_flags = flags & ~(F_BLOCK_INDIRECT|F_BLOCK_HASHTYPE)
  assert unknown_flags == 0, \
         "unexpected flags value (0x%02x) with unsupported flags=0x%02x, s=%s" \
         % (flags, unknown_flags, tohex(s0))
  span, s = fromBS(s)
  indirect = bool(flags & F_BLOCK_INDIRECT)
  if flags & F_BLOCK_HASHTYPE:
    hashenum, s = fromBS(s)
  else:
    hashenum = HASH_SHA1_T
  if hashenum == HASH_SHA1_T:
    hashcode, s = Hash_SHA1.decode(s)
  else:
    assert False, "unsupported hash enum %d" % (hashenum,)
    # will read hlen here for some hash types
  assert len(s) >= hlen, \
         "expected %d bytes of hash, only %d bytes in string: %s" \
         % (hlen, len(s), tohex(s))
  if indirect:
    B = IndirectBlock(hashcode=hashcode, length=span)
  else:
    B = Block(hashcode=hashcode, length=span)
  if justone:
    assert len(s) == 0, "extra stuff after block ref: %s" % (tohex(s),)
    return B
  return B, s

class Block(object):
  ''' A direct block.
  '''
  def __init__(self, data=None, hashcode=None, length=None):
    ''' Initialise a direct block, supplying data bytes or hashcode, but not both.
    '''
    assert (data is None) ^ (hashcode is None)
    self.indirect = False
    self.data = data
    self._hashcode = hashcode
    self.__length = length

  def hashcode(self):
    ''' Return the hashcode for this block.
        Compute the hashcode if unknown or if it does not match the default
        store's default hashtype.
        When the block's current hashcode is the wrong type and this is a
        "hash only" block, the recompute has an implied fetch from the store
        using the old/wrong hashcode, so the store must support both.
    '''
    hashtype = defaults.S.hashtype
    _hashcode = self._hashcode
    if type(_hashcode) is not hashtype:
      _hashcode = self._hashcode = hashtype(self.data())
    return _hashcode

  def data(self):
    ''' Retrn the data bytes of this block.
    '''
    data = self.data
    if data is None:
      data = self.data = defaults.S[self.hashcode]
    return data
  def __getitem__(self, index):
    ''' Return specified data.
    '''
    return self.data()[index]
  def __len__(self):
    ''' Return the length of the data.
    '''
    mylen = self.__length
    if mylen is None:
      mylen = self.__length = len(self.data())
    return mylen

class IndirectBlock(object):
  ''' An indirect block.
  '''
  def __init__(self, hashcode=None, length=None):
    self.indirect = True
    self._hashcode = hashcode
    self.__length = length
    if hashcode is None:
      self.__subblocks = []
    else:
      self.__subblocks = None

  def __len__(self):
    if self.__length is None:
      self.__length = sum( len(B) for B in self.subblocks() )
    return self.__length

  def subblocks(self):
    ''' Return the subblocks of this indirect block.
    '''
    _subblocks = self.__subblocks
    if _subblocks is None:
      # fetch the encoded subblocks from the store
      _subblocks = []
      data = defaults.S[self._hashcode]
      while len(data) > 0:
        B, data = decodeBlock(data)
        _subblocks.append(B)
      # compute length or check against hostile store
      length = sum(len(B) for B in _subblocks)
      if self.__length is None:
        self.__length = length
      else:
        assert False, "sum(len(subblocks))=%d but expected length=%d" % (length, self.__length)
      self.__subblocks = _subblocks
    return _subblocks

  def data(self):
    ''' Return all the data below this indirect block.
        Probably to be discouraged if this may be very large.
    '''
    return ''.join(B.data() for B in self.leaves())
  def leaves(self):
    ''' Return the leaf (direct) blocks.
    '''
    for B in self.subblocks():
      if B.indirect:
        for subB in B.leaves():
          yield subB
      else:
        yield B
  def append(self, subblock):
    self.subblocks().append(subblock)
    self.__length = None
  def extend(self, subblocks):
    self.subblocks().extend(subblocks)
    self.__length = None
  def __rangeChunks(self, start, stop):
    ''' Generator that yields the chunks from the subblocks that span the supplied range.
    '''
    if stop <= start:
      return
    rangelen = stop - start
    if rangelen <= 0:
      return
    subindex = 0
    for B in self.subblocks():
      if start >= len(B):
        start -= len(B)
        continue
      chunkend = start + rangelen
      if chunkend > len(B):
        chunkend = len(B)
      chunk = B[start:chunkend]
      yield chunk
      rangelen -= len(chunk)
      if rangelen <= 0:
        break
      start = 0
  def __getitem__(self, index):
    itype = type(index)
    mylen = len(self)
    if itype is int:
      # a simple index
      oindex = index
      if index < 0:
        index += mylen
      if index < 0 or index >= mylen:
        raise IndexError("__getitem__[%d] out of range" % (oindex,))
      for B in self.subblocks():
        Blen = len(B)
        if index >= Blen:
          index -= Blen
        else:
          return B[index]
      assert False, "__getitem__[%d] did not find the index in the subblocks" % (oindex,)
    # a slice
    start = index.start or 0
    stop = index.stop or sys.maxint
    step = index.step or mylen
    assert step != 0, "step == 0"
    if step == 1:
      # join adjacent chunks
      return ''.join(self.__rangeChunks(start, stop))
    if step == -1:
      # obtain chunks, reverse, then join
      chunks = list(self.__rangeChunks(stop, start))
      chunks.reverse()
      return ''.join(chunks)
    return ''.join( self[i] for i in xrange(start, stop, step) )
