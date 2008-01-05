#!/usr/bin/python
#
# File interfaces.      - Cameron Simpson <cs@zip.com.au>
#

from cs.venti import MAX_BLOCKSIZE, hash_sha, tohex
from cs.venti.blocks import BlockList
from cs.misc import debug

def open(S,mode="r",bref=None):
  ''' Obtain a file object open for read or write.
  '''
  if mode == "r":
    return ReadFile(S,bref)
  if mode == "w":
    assert path is None
    return WriteFile(S)
  assert False, "open(path=%s, mode=%s): unsupported mode" % (path,mode)

def storeFile(S,ifp,rsize=None,findEdge=None):
  ''' Store the data from ifp, return BlockRef.
  '''
  if rsize is None: rsize=8192
  ofp=WriteFile(S,findEdge)
  buf=ifp.read(rsize)
  while len(buf) > 0:
    ofp.write(buf)
    buf=ifp.read(rsize)
  ref=ofp.close()
  S.log("store file %s %s" % (tohex(ref.encode()), ifp.name))
  return ref

# Assorted findEdge functions.
#
def findEdgeDumb(block):
  ''' Find edge for unstructured data.
  '''
  global MAX_BLOCKSIZE
  if len(self.__q) >= MAX_BLOCKSIZE:
    return MAX_BLOCKSIZE
  return 0

def findEdgeCode(block):
  ''' Find edge for UNIX mbox files and code.
  '''
  start=0
  found=block.find('\n')
  while found >= 0:
    if found > MAX_BLOCKSIZE:
      return MAX_BLOCKSIZE

    # UNIX mbox file
    # python/perl top level things
    if block[found+1:found:6] == "From " \
    or block[found+1:found+5] == "def " \
    or block[found+1:found+7] == "class " \
    or block[found+1:found+9] == "package ":
      return found+1

    # C/C++/perl/js etc end of function
    if block[found+1:found+3] == "}\n":
      return found+3

    start=found+1
    found=block.find('\n',start)

  return 0

class ReadFile:
  ''' A read-only file interface supporting seek(), read(), readline(),
      readlines() and tell() methods.
  '''
  def __init__(self,S,bref):
    self.isdir=False
    self.__store=S
    if bref.indirect:
      self.__blist=bref.blocklist(S)
    else:
      self.__blist=BlockList(S)
      self.__blist.append(bref)
    self.__pos=0

  def seek(self,offset,whence=0):
    if whence == 1:
      offset+=self.tell()
    elif whence == 2:
      offset+=self.span()
    self.__pos=offset

  def tell(self):
    return self.__pos

  def readShort(self):
    (h,offset)=self.__blist.seekToBlock(self.tell())
    if h is None:
      # at or past EOF - return empty read
      return ''
    b=self.__store[h]
    assert offset < len(b)
    chunk=b[offset:]
    assert len(chunk) > 0
    self.seek(len(chunk),1)
    return chunk

  def read(self,size=None):
    opos=self.__pos
    buf=''
    while size is None or size > 0:
      chunk=self.readShort()
      if len(chunk) == 0:
        break
      if size is None:
        buf+=chunk
      elif size <= len(chunk):
        buf+=chunk[:size]
        size=0
      else:
        buf+=chunk
        size-=len(chunk)

    self.seek(opos+len(buf))
    return buf

  def readline(self,size=None):
    opos=self.__pos
    line=''
    while size is None or size > 0:
      chunk=self.readShort()
      nlndx=chunk.find('\n')
      if nlndx >= 0:
        # there is a NL
        if size is None or nlndx < size:
          # NL in the available chunk
          line+=chunk[:nlndx+1]
        else:
          # NL not available - ergo size not None and inside chunk
          line+=chunk[:size]
        break

      if size is None or size >= len(chunk):
        # we can suck in the whole chunk
        line+=chunk
        if size is not None:
          size-=len(chunk)
      else:
        # take its prefix and quit
        line+=chunk[:size]
        break

    self.seek(opos+len(line))
    return line

  def readlines(self,sizehint=None):
    lines=[]
    byteCount=0
    while sizehint is None or byteCount < sizehint:
      line=self.readline()
      if len(line) == 0:
        break
      lines.append(line)
      byteCount+=len(line)

    return lines

class WriteFile:
  ''' A File-like class that supplies only write, close, flush.
      flush() forces any unstored data to the store.
      close() flushes all data and returns a BlockRef for the whole file.
  '''
  def __init__(self,S,findEdge=None):
    self.isdir=False
    if findEdge is None:
      findEdge=findEdgeCode

    from cs.venti.blocks import BlockSink
    import cs.venti.store
    assert isinstance(S, cs.venti.store.BasicStore), "S=%s"%S
    self.__sink=BlockSink(S)
    self.__findEdge=findEdge
    self.__q=''

  def close(self):
    ''' Close the file. Return the hash code of the top indirect block.
    '''
    self.flush()
    return self.__sink.close()

  def flush(self):
    ''' Force any remaining data to the store.
    '''
    if len(self.__q) > 0:
      self.__dequeue()
      if len(self.__q) > 0:
        self.__sink.appendBlock(self.__q)
        self.__q=''

  def write(self,data):
    ''' Queue data for writing.
    '''
    self.__q+=data
    self.__dequeue()

  def __dequeue(self):
    ''' Flush complete blocks to the store.
    '''
    edgendx=self.__findEdge(self.__q)
    while edgendx > 0:
      self.__sink.appendBlock(self.__q[:edgendx])
      self.__q=self.__q[edgendx:]
      edgendx=self.__findEdge(self.__q)
