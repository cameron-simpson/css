#!/usr/bin/python

import unittest
import sys
from cs.logutils import D
from cs.serialise import toBS, fromBS
from cs.venti import defaults, totext
from cs.venti.hash import Hash_SHA1, HASH_SHA1_T
from cs.venti.debug import dumpBlock

F_BLOCK_INDIRECT = 0x01 # indirect block
F_BLOCK_HASHTYPE = 0x02 # hash type explicit

def decodeBlocks(s):
  while len(s) > 0:
    B, s = decodeBlock(s)
    yield B

def decodeBlock(s, justone=False):
  ''' Decode a Block reference.
      Format is:
        BS(flags)
          0x01 indirect blockref
          0x02 non-SHA1 hashcode
        BS(span)
        [BS(hashtype)[BS(hashlen)]]
        hash
      If the optional parameter 'justone' if false, return the Block or
      IndirectBlock, and the tail of 's'.  
      If the optional paramater 'justone' is true, check the tail is
      empty and return just the Block.
  '''
  s0=s
  flags, s = fromBS(s)
  unknown_flags = flags & ~(F_BLOCK_INDIRECT|F_BLOCK_HASHTYPE)
  assert unknown_flags == 0, \
         "unexpected flags value (0x%02x) with unsupported flags=0x%02x, s=%s" \
         % (flags, unknown_flags, totext(s0))
  span, s = fromBS(s)
  indirect = bool(flags & F_BLOCK_INDIRECT)
  if flags & F_BLOCK_HASHTYPE:
    assert False, "unexpected flags & F_BLOCK_HASHTYPE"
    hashenum, s = fromBS(s)
  else:
    hashenum = HASH_SHA1_T
  if hashenum == HASH_SHA1_T:
    hashcode, s = Hash_SHA1.decode(s)
  else:
    assert False, "unsupported hash enum %d" % (hashenum,)
    # will read hlen here for some hash types
  if indirect:
    B = IndirectBlock(hashcode=hashcode, span=span)
  else:
    B = Block(hashcode=hashcode, span=span)
  if justone:
    assert len(s) == 0, "extra stuff after block ref: %s" % (totext(s),)
    return B
  return B, s

def isBlock(o):
  return isinstance(o, _Block)

class _Block(object):
  # TODO: hashcode(), data(), blockdata() should use __getattr__

  def hashcode(self):
    ''' Return the hashcode for this block.
        Compute the hashcode if unknown or if it does not match the default
        store's default hashtype.
        When the block's current hashcode is the wrong type and this is a
        "hash only" block, the recompute has an implied fetch from the store
        using the old/wrong hashcode, so the store must support both.
    '''
    S = defaults.S
    hashclass = S.hashclass
    _hashcode = self._hashcode
    if _hashcode is None or type(_hashcode) is not hashclass:
      D("block %s: _hashcode=%s, hashclass=%s", id(self), _hashcode, hashclass)
      D("  block: %s", `self.__dict__`)
      data = self.blockdata()
      _hashcode = self._hashcode = S.add(data)
    return _hashcode

  def encode(self):
    ''' Encode this Block for storage:
        Format is:
          BS(flags)
            0x01 indirect block
            0x02 has hash type (False ==> Hash_SHA1_T)
          BS(span)
          [BS(hashtype)]
          hashcode.encode()     # may include hashlen prefix for some hash types
    '''
    flags=0
    if self.indirect:
      flags |= F_BLOCK_INDIRECT
    hashcode = self.hashcode()
    if hashcode.hashenum != HASH_SHA1_T:
      flags |= F_BLOCK_HASHTYPE
    enc = "".join([toBS(flags), toBS(len(self)), hashcode.encode()])
    assert len(enc) >= 22
    return enc

  def textEncode(self):
    return totext(self.encode())

class Block(_Block):
  ''' A direct block.
  '''
  def __init__(self, data=None, hashcode=None, span=None):
    ''' Initialise a direct block, supplying data bytes or hashcode,
        but not both.
    '''
    assert (data is None) ^ (hashcode is None)
    self.indirect = False
    if data is None:
      assert hashcode is not None
      assert span is not None   # really? or should I just cope?
      self._data = None
      self._hashcode = hashcode
      self.__span = span
    else:
      assert hashcode is None
      if span is None:
        span = len(data)
      else:
        assert type(span) is int, "excepted int, got %s" % (`span`,)
        assert span == len(data)
      self._data = data
      self._hashcode = None
      self.__span = span

  def data(self):
    ''' Return the data bytes of this block.
    '''
    data = self._data
    if data is None:
      S = defaults.S
      assert not S.writeonly
      data = self._data = S[self.hashcode()]
    return data

  ''' Return the direct content of this block.
      For a direct Block this is the same as data().
      For an IndirectBlock this is the encoded data that refers to the
      subblocks.
  '''
  blockdata = data

  def store(self, discard=False):
    ''' Ensure this block is stored.
        Return the block's hashcode.
        If discard is true, release the block's data.
    '''
    S = defaults.S
    if self._hashcode is None:
      self._hashcode = S.add(self.blockdata())
      assert self._hashcode is not None
    elif self._hashcode not in S:
      self._hashcode = S.add(self.blockdata())
      assert self._hashcode is not None
    if discard:
      self._data = None
    return self._hashcode

  def leaves(self):
    yield self

  def __getitem__(self, index):
    ''' Return specified data.
    '''
    return self.data()[index]

  def __len__(self):
    ''' Return the length of the data encompassed by this block.
    '''
    mylen = self.__span
    if mylen is None:
      mylen = self.__span = len(self.data())
    return mylen

class IndirectBlock(_Block):
  ''' An indirect block.
      Indirect blocks come in two states, reflecting how how they are
      initialised.
      If initialised without parameters the block is an empty array
      of subblocks.
      The other way to initialise an IndirectBlock is with a hashcode and a
      span indicating the length of the data encompassed by the block; this is
      how a block is made from a directory entry or another indirect block.

      An indirect block can be extended with more block hashes, even one
      initialised from a hashcode. It is necessary to call the .store()
      method on a block that has been extended.

      TODO: allow data= initialisation, to decode raw iblock data
  '''
  def __init__(self, hashcode=None, span=None):
    self.indirect = True
    self._hashcode = hashcode
    if hashcode is None:
      assert span is None
      self.__span = 0
      self.__subblocks = []
    else:
      assert span is not None
      self.__span = span
      self.__subblocks = None

  def __len__(self):
    ''' Return the length of the data encompassed by this block.
    '''
    if self.__span is None:
      self.__span = sum( len(B) for B in self.subblocks() )
    return self.__span

  def __load_subblocks(self):
    # fetch the encoded subblocks from the store
    # and unpack into blocks
    _subblocks = []
    S = defaults.S
    data = S[self._hashcode]
    span = 0
    for B in decodeBlocks(data):
      span += len(B)
      _subblocks.append(B)
    # compute span or check against hostile store
    if self.__span is None:
      self.__span = span
    else:
      assert len(self) == span, \
             "sum(len(subblocks))=%d but expected span=%d" \
             % (span, self.__span)
    self.__subblocks = _subblocks

  def subblocks(self):
    if self.__subblocks is None:
      self.__load_subblocks()
    return list(self.__subblocks)

  def append(self, block):
    assert type(block) is not Hash_SHA1
    if self.__subblocks is None:
      self.__load_subblocks()
    self.__subblocks.append(block)
    self.__span = None
    self._hashcode = None

  def extend(self, subblocks):
    if self.__subblocks is None:
      self.__load_subblocks()
    self.__subblocks.extend(subblocks)
    self.__span = None
    self._hashcode = None

  def data(self):
    ''' Return all the data encompassed by this indirect block.
        Probably to be discouraged as this may be very large.
        TODO: return some kind of buffer object that accesses self[index]
              on demand?
    '''
    return ''.join(B.data() for B in self.leaves())

  def blockdata(self):
    ''' Return the direct content of this block.
        For an IndirectBlock this is the encoded data that refers to
        the subblocks.
        For a direct Block this is the same as data().
    '''
    blocks = self.subblocks()
    return "".join( B.encode() for B in blocks )

  def store(self, discard=False):
    data = self.blockdata()
    self._hashcode = defaults.S.add(data)
    return self._hashcode

  def leaves(self):
    ''' Return the leaf (direct) blocks.
    '''
    for B in self.subblocks():
      if B.indirect:
        for subB in B.leaves():
          yield subB
      else:
        yield B

  def chunks(self, start, stop=None):
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
    if stop > mylen:
      stop = mylen
    step = index.step or 1
    assert step != 0, "step == 0"

    if step == 1:
      # join adjacent chunks
      return ''.join(self.chunks(start, stop))

    if step == -1:
      # obtain chunks, reverse, then join
      chunks = list(self.chunks(stop, start))
      chunks.reverse()
      return ''.join(chunks)

    return ''.join( self[i] for i in xrange(start, stop, step) )

class TestAll(unittest.TestCase):
  def setUp(self):
    import random
    random.seed()
  def testSHA1(self):
    import random
    from cs.venti.cache import MemCacheStore
    S = MemCacheStore()
    with S:
      IB = IndirectBlock()
      for i in range(10):
        rs = ''.join( chr(random.randint(0,255)) for x in range(100) )
        B = Block(data = rs)
        assert len(B) == 100
        IB.append(B)
        assert len(IB) == (i+1) * 100
      IB.store()
      assert len(IB) == 1000
      IBH = IB.hashcode()
      IBdata = IB.data()
      D("IBdata = %s:%d:%s", type(IBdata), len(IBdata), `IBdata`,)
      IB2data = IndirectBlock(hashcode=IBH, span=len(IBdata)).data()
      D("IB2data = %s:%d:%s", type(IB2data), len(IB2data), `IB2data`,)
      self.assertEqual(IBdata,IB2data, "IB:  %s\nIB2: %s" % (totext(IBdata), totext(IB2data)))

if __name__ == '__main__':
  unittest.main()
