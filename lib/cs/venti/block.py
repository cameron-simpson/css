#!/usr/bin/python

from cs.venti import defaults

class Block(object):
  ''' A direct block.
  '''
  def __init__(self, data=None, hashcode=None, length=None):
    ''' Initialise a direct block, supplying data bytes or hashcode, but not both.
    '''
    assert (data is None) ^ (hashcode is None)
    self.indirect = False
    self.data = data
    self.hashcode = hashcode
    self.__length = length
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
  def __init__(self):
    self.indirect = True
    self.__len = None
    self.subblocks = []
  def __len__(self):
    if self.__len is None:
      self.__len = sum( len(B) for B in self.subblocks )
    return self.__len
  def data(self):
    ''' Return all the data below this indirect block.
        Probably to be discouraged if this may be very large.
    '''
    return ''.join(B.data() for B in self.leaves())
  def leaves(self):
    ''' Return the leaf (direct) blocks.
    '''
    for B in self.subblocks:
      if B.indirect:
        for subB in B.leaves():
          yield subB
      else:
        yield B
  def append(self, subblock):
    self.subblocks.append(subblock)
    self.__len = None
  def extend(self, subblocks):
    self.subblocks.extend(subblocks)
    self.__len = None
  def __rangeChunks(self, start, stop):
    ''' Generator that yields the chunks from the subblocks that span the supplied range.
    '''
    if stop <= start:
      return
    rangelen = stop - start
    if rangelen <= 0:
      return
    subindex = 0
    for B in self.subblocks:
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
      for B in self.subblocks:
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
