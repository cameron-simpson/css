#!/usr/bin/python
#
# Data structures to deal with multiple blocks:
#  BlockRef:    A reference to a "file", either directly to a single data block
#               or indirectly by reference to an indirect block.
#               An indirect block is a list of encoded BlockRefs.
#

from cs.misc import toBS, fromBS, fromBSfp
from cs.venti import MAX_SUBBLOCKS

def encodeBlockRef(flags,span,h):
    ''' Encode a blockref for storage.
    '''
    return toBS(flags)+toBS(span)+toBS(len(h))+h

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

def str2BlockRef(s):
  (bref,etc)=decodeBlockRef(s)
  assert len(etc) == 0, "left over data from \"%s\": \"%s\"" % (s,etc)
  return bref

def decodeBlockRefFP(fp):
  ''' Decode a block ref from an indirect block.
      It consist of:
        flags: BSencoded ordinal
        span:  BSencoded ordinal
        hashlen: BSencoded ordinal
        hash:  raw hash
      Return (BlockRef,unparsed) where unparsed is remaining data.
  '''
  flags=fromBSfp(fp)
  indirect=bool(flags&0x01)
  span=fromBSfp(fp)
  hlen=fromBSfp(fp)
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
    self.h=h
    self.indirect=indirect
    self.span=span
  def flags(self):
    return 0x01 & int(self.indirect)
  def __len__(self):
    return self.span
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

  def pack(self):
    return "".join([bref.encode() for bref in self])

  def span(self):
    return self.offsetTo(len(self))

  def offsetTo(self,ndx):
    ''' Offset to start of blocks under entry ndx.
    '''
    return sum([bref.span for bref in self])

  def seekToBlock(self,offset):
    ''' Seek to a leaf block.
        Return the block hash and the offset within the block of the target.
        If the seek is beyond the end of the blocklist, return None and the
        remaining offset.
    '''
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
    for bref in self:
      for b in bref.leaves(S):
        yield b

class BlockSink:
  ''' A BlockSink supports storing a sequence of blocks or BlockRefs,
      assembling indirect blocks as necessary. It maintains a list of
      BlockLists containing direct or indirect BlockRefs. As each BlockList
      overflows its storage limit it is encoded as a block and stored and a
      new BlockList made for further data; the encoded block is added
      to BlockList for the next level of indirection.

      On close() the BlockLists are stored.
      As a storage optimisation, a BlockList with only one BlockRef is
      discarded, and the BlockRef passed to the next level up.
      When there are no more higher levels, the current blockref is
      returned as the top level reference to the file. For small files
      this is a direct BlockRef to the sole block of storage for the file.
  '''
  def __init__(self,S):
    self.__store=S
    self.__lists=[BlockList(S)]

  def appendBlock(self,block):
    ''' Append a block to the BlockSink.
    '''
    self.appendBlockRef(BlockRef(self.__store.store(block),False,len(block)))

  def appendBlockRef(self,bref):
    ''' Append a BlockRef to the BlockSink.
    '''
    S=self.__store
    level=0
    while True:
      blist=self.__lists[level]
      if len(blist) < MAX_SUBBLOCKS:
        blist.append(bref)
        return

      # The blocklist is full.
      # Make a BlockRef to refer to the stored version.
      # We will store it at the next level of indirection.
      nbref=BlockRef(S.store(blist.pack()),True,blist.span())

      # Prepare fresh empty block at this level.
      # and add the old bref to it instead.
      blist=self.__lists[level]=BlockList(S)
      blist.append(bref)

      # Advance to the next level of indirection and repeat
      # to store the just produced full indirect block.
      level+=1
      bref=nbref
      if level == len(self.__lists):
        self.__lists.append(BlockList(S))

  def close(self):
    ''' Store all the outstanding indirect blocks (if they've more than one entry).
        Return the top BlockRef.
    '''
    S=self.__store

    # special case - empty file
    if len(self.__lists) == 1 and len(self.__lists[0]) == 0:
      self.appendBlock('')

    # Fold BlockLists into blocks and pass them up the chain until we have
    # a single BlockRef - it becomes the file handle.
    # record the lower level indirect blocks in their parents
    assert len(self.__lists) > 0

    # Reduce the BlocList stack to one level.
    while True:
      blist=self.__lists.pop(0)
      assert len(blist) > 0
      if len(blist) == 1:
        # discard block with just one pointer - keep the pointer
        bref=blist[0]
      else:
        # store and record the indirect block
        bref=BlockRef(S.store(blist.pack()),True,blist.span())

      if len(self.__lists) == 0:
        break

      # put the new blockref into the popped stack
      self.appendBlockRef(bref)

    # POST: bref if the top blockref
    assert len(self.__lists) == 0
    self.__lists=None

    return bref
