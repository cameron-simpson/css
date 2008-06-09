#!/usr/bin/python
#
# Data structures to deal with multiple blocks:
#  BlockRef:    A reference to a "file", either directly to a single data block
#               or indirectly by reference to an indirect block.
#               An indirect block is a list of encoded BlockRefs.
#

from cs.misc import toBS, fromBS, fromBSfp, warn, cmderr, TODO, FIXME, isdebug
from cs.io import readn
from cs.venti import MAX_SUBBLOCKS, tohex
from cs.venti.hash import HASH_SIZE_DEFAULT

class BlockRef:
  ''' A reference to a "file" (block sequence) consisting of a hash and an
      indirect flag. Tiny files are not indirect, and the hash is the block
      holding their content. For larger files indirect is True and the hash
      refers to their top indirect block.
  '''
  F_INDIRECT=0x01
  F_HAS_HASH_SIZE=0x02
  def __init__(self,h,indirect,span):
    assert h is not None
    self.h=h
    self.indirect=indirect
    self.span=span
  def __len__(self):
    return self.span
  def __str__(self):
    return tohex(self.encode())
  def blocklist(self,S):
    assert self.indirect
    return BlockList(S,self.h)
  @classmethod
  def decode(cls, s, justone=False):
    ''' Decode a blockref and return it.
        Format is:
          BS(flags)
            0x01 indirect blockref
            0x02 len(hash) != HASH_SIZE_DEFAULT
          BS(span)
          [BS(hashlen)]
          hash
    '''
    s0=s
    flags, s = fromBS(s)
    assert flags&~(BlockRef.F_INDIRECT|BlockRef.F_HAS_HASH_SIZE) == 0, \
           "unexpected flags value (%02x), s=%s" % (flags, tohex(s0))
    span, s = fromBS(s)
    indirect=bool(flags & BlockRef.F_INDIRECT)
    if flags & BlockRef.F_HAS_HASH_SIZE:
      hlen, s = fromBS(s)
    else:
      hlen = HASH_SIZE_DEFAULT
    assert len(s) >= hlen, \
           "expected %d bytes of hash, only %d bytes in string: %s" \
           % (hlen, len(s), tohex(s))
    h=s[:hlen]
    s=s[hlen:]
    bref=BlockRef(h,indirect,span)
    if justone:
      assert len(s) == 0, "extra stuff after blockref: %s" % tohex(s)
      return bref
    return bref, s
      
  def encode(self):
    ''' Encode a blockref for storage:
        Format is:
          BS(flags)
            0x01 indirect blockref
            0x02 len(hash) != HASH_SIZE_DEFAULT
          BS(span)
          [BS(hashlen)]
          hash
    '''
    flags=0
    if self.indirect:
      flags|=BlockRef.F_INDIRECT
    h=self.h
    if len(h) != HASH_SIZE_DEFAULT:
      flags|=BlockRef.F_HAS_HASH_SIZE
      hlen=toBS(len(h))
    else:
      hlen=""
    brefEnc=toBS(flags)+toBS(self.span)+hlen+h
    if isdebug:
      import sys; print >>sys.stderr, "brefEnc=[%s]" % tohex(brefEnc)
    return brefEnc

  def leaves(self,S):
    if self.indirect:
      for b in self.blocklist().leaves(S):
        yield b
    else:
      yield S[self.h]

decodeBlockRef=BlockRef.decode

class BlockList(list):
  ''' An in-memory BlockList, used to store or retrieve sequences of
      data blocks.
  '''
  def __init__(self,iblock=None):
    ''' Create a new blocklist, initially empty, which is a subclass
        if "list" containing BlockRefs.
        If the optional argument "iblock" is supplied it is treated as an
        indirect block from which a list of BlockRefs is read to initialise
        the BlockList.
    '''
    list.__init__(self)
    if iblock is not None:
      while len(iblock) > 0:
        (bref, iblock)=decodeBlockRef(iblock)
        self.append(bref)
    self.__pos=None

  def encode(self):
    return "".join(bref.encode() for bref in self)

  def span(self):
    ''' Return the number of bytes in the leaf blocks spanned by this
        BlockList.
    '''
    return self.offsetTo(len(self))

  def offsetTo(self,ndx):
    ''' Offset to start of blocks under entry ndx.
    '''
    return sum(bref.span for bref in self[:ndx])

  def seekToBlock(self,offset,S=None):
    ''' Seek to a leaf block.
        Return the block hash and the offset within the block of the target.
        If the seek is beyond the end of the blocklist, return None and the
        remaining offset.
    '''
    if S is None:
      S=__main__.S
    # seek into most recent block?
    if self.__pos is not None:
      b_h, b_offset, b_size = self.__pos
      if b_offset <= offset and b_offset+b_size > offset:
        return b_h, offset-b_offset

    b_h, roffset = self.__seekToBlock(offset,S)
    if b_h is not None:
      try:
        blk = S[b_h]
      except KeyError, e:
        cmderr("%s: not in store %s" % (tohex(b_h), S))
        return None, offset-roffset
      self.__pos=(b_h, offset-roffset, len(S[b_h]))
    return b_h, roffset

  def __seekToBlock(self,offset,S):
    for bref in self:
      if offset < bref.span:
        if bref.indirect:
          return bref.blocklist().seekToBlock(offset,S=S)
        return (bref.h,offset)
      offset-=bref.span
    return (None,offset)

  def leaves(self,S=None):
    ''' Iterate over the leaf blocks.
    '''
    if S is None:
      S=__main__.S
    S.prefetch(bref.h for bref in self)
    for bref in self:
      for b in bref.leaves(S=S):
        yield b
