#!/usr/bin/python
#
# Data structures to deal with multiple blocks:
#  BlockRef:    A reference to a "file", either directly to a single data block
#               or indirectly by reference to an indirect block.
#               An indirect block is a list of encoded BlockRefs.
#

from cs.misc import toBS, fromBS, fromBSfp, warn, cmderr
from cs.venti import MAX_SUBBLOCKS, tohex
import cs.venti.store

def encodeBlockRef(flags,span,h):
    ''' Encode a blockref for storage:
        Format is:
          BS(flags)
          BS(span)
          BS(hashlen)
          hash
    '''
    assert type(h) is str, "h=%s"%h
    return toBS(flags)+toBS(span)+toBS(len(h))+h

def str2BlockRef(s):
  (bref,etc)=decodeBlockRef(s)
  assert len(etc) == 0, "left over data from \"%s\": \"%s\"" % (s,etc)
  return bref

def decodeBlockRef(iblock):
  ''' Decode a block ref from an indirect block.
      It consist of:
        flags: BSencoded ordinal
        span:  BSencoded ordinal
        hashlen: BSencoded ordinal
        hash:  raw hash
      Return (BlockRef,unparsed) where unparsed is remaining data.
  '''
  (flags,iblock)=fromBS(iblock)
  indirect=bool(flags&0x01)
  (span,iblock)=fromBS(iblock)
  (hlen,iblock)=fromBS(iblock)
  h=iblock[:hlen]
  iblock=iblock[hlen:]
  return (BlockRef(h,indirect,span), iblock)

def decodeBlockRefFP(fp):
  ''' Decode a block ref from a file.
      It consist of:
        flags: BSencoded ordinal
        span:  BSencoded ordinal
        hashlen: BSencoded ordinal
        hash:  raw hash
      Return (BlockRef,unparsed) where unparsed is remaining data.
      Return None at EOF.
  '''
  flags=fromBSfp(fp)
  if flags is None:
    return None
  indirect=bool(flags&0x01)
  assert (flags&~0x01) == 0, "unexpected flags: 0x%x" % flags
  span=fromBSfp(fp)
  assert span is not None, "unexpected EOF"
  hlen=fromBSfp(fp)
  assert hlen is not None, "unexpected EOF"
  h=fp.read(hlen)
  assert len(h) == hlen, "(indir=%s, span=%d): expected hlen=%d bytes, read %d bytes: %s"%(indirect,span,hlen,len(h),tohex(h))
  return BlockRef(h,indirect,span)

class BlockRef:
  ''' A reference to a file consisting of a hash and an indirect flag.
      Tiny files are not indirect, and the hash is the block holding
      their content. For larger files indirect is True and the hash
      refers to their top indirect block.
  '''
  def __init__(self,h,indirect,span):
    assert h is not None
    self.h=h
    self.indirect=indirect
    self.span=span
  def flags(self):
    return 0x01 & int(self.indirect)
  def __len__(self):
    return self.span
  def __str__(self):
    return tohex(self.encode())
  def blocklist(self,S):
    assert self.indirect
    return BlockList(S,self.h)
  def encode(self):
    ''' Return an encoded BlockRef for storage, inverse of decodeBlockRef().
    '''
    h=self.h
    return encodeBlockRef(self.flags(),self.span,self.h)
  def leaves(self,S):
    if self.indirect:
      for b in self.blocklist(S).leaves():
        yield b
    else:
      yield S[self.h]

class BlockList(list):
  ''' An in-memory BlockList, used to store or retrieve sequences of
      data blocks.
  '''
  def __init__(self,S,h=None):
    ''' Create a new blocklist.
        This will be empty initially unless the hash of a top level
        indirect block is supplied, in which case it is loaded and
        decoded into BlockRefs.
    '''
    self.__store=S
    if h is not None:
      iblock=S[h]
      while len(iblock) > 0:
        (bref, iblock)=decodeBlockRef(iblock)
        self.append(bref)
    self.__pos=None

  def pack(self):
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

  def seekToBlock(self,offset):
    ''' Seek to a leaf block.
        Return the block hash and the offset within the block of the target.
        If the seek is beyond the end of the blocklist, return None and the
        remaining offset.
    '''
    # seek into most recent block?
    if self.__pos is not None:
      b_h, b_offset, b_size = self.__pos
      if b_offset <= offset and b_offset+b_size > offset:
        return b_h, offset-b_offset

    b_h, roffset = self.__seekToBlock(offset)
    if b_h is not None:
      try:
        blk = self.__store[b_h]
      except KeyError, e:
        cmderr("%s: not in store %s" % (tohex(b_h), self.__store))
        return None, offset-roffset
      self.__pos=(b_h, offset-roffset, len(self.__store[b_h]))
    return b_h, roffset

  def __seekToBlock(self,offset):
    for bref in self:
      if offset < bref.span:
        if bref.indirect:
          return bref.blocklist(self.__store).seekToBlock(offset)
        return (bref.h,offset)
      offset-=bref.span
    return (None,offset)

  def leaves(self):
    ''' Iterate over the leaf blocks.
    '''
    S=self.__store
    S.prefetch(bref.h for bref in self)
    for bref in self:
      for b in bref.leaves(S):
        yield b
