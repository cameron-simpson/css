#!/usr/bin/python

''' A data store patterned after the Venti scheme:
      http://library.pantek.com/general/plan9.documents/venti/venti.html
    but supporting variable sized blocks.
    See also the Plan 9 Venti support manual pages:
      http://swtch.com/plan9port/man/man7/venti.html
    and the Wikipedia entry:
      http://en.wikipedia.org/wiki/Venti

    TODO:
      Compress the data chunks in the data file.
      MetaFS interface using real files for metadata and their contents
	as hashes.
'''

import os.path
from zlib import compress, decompress
## NOTE: migrate to hashlib sometime when python 2.5 more common
import sha
from cs.misc import warn, progress, verbose
import cs.hier
from cs.lex import unctrl

HASH_SIZE=20    # size of SHA-1 hash
MAX_BLOCKSIZE=65536
MAX_SUBBLOCKS=MAX_BLOCKSIZE/(HASH_SIZE+2)

def hash_sha(block):
  ''' Returns the SHA-1 checksum for the supplied block.
  '''
  hash=sha.new(block)
  return hash.digest()

def unhex(hexstr):
  ''' Return raw byte array from hexadecimal string.
  '''
  return "".join([chr(int(hexstr[i:i+2],16)) for i in range(0,len(hexstr),2)])

def genHex(data):
  for c in data:
    yield '%02x'%ord(c)

def hex(data):
  return "".join(genHex(data))

def writehex(fp,data):
  ''' Write data in hex to file.
  '''
  for w in genHex(data):
    fp.write(w)

class RawStore(dict):
  def __init__(self,path):
    dict.__init__(self)
    self.__path=path
    progress("indexing", path)
    self.__fp=open(path,"a+b")
    self.loadIndex()

  def __setitem__(self,key,value):
    raise IndexError

  def __getitem__(self,key):
    v=dict.__getitem__(self,key)
    if v[2] is None:
      self.__fp.seek(v[0])
      v[2]=decompress(self.__fp.read(v[1]))
    return v[2]

  def hash(self,block):
    ''' Compute the hash for a block.
    '''
    return hash_sha(block)

  def store(self,block):
    h=self.hash(block)
    if h in self:
      verbose(self.__path,"already contains",hex(h))
    else:
      print "new block", hex(h)
      print "["+block+"]"
      zblock=compress(block)
      self.__fp.seek(0,2)
      self.__fp.write(str(len(zblock)))
      self.__fp.write("\n")
      offset=self.__fp.tell()
      self.__fp.write(zblock)
      dict.__setitem__(self,h,[offset,len(zblock),block])

    return h

  def loadIndex(self):
    fp=self.__fp
    fp.seek(0)
    while True:
      e=self.__loadEntryHead(fp)
      if e is None:
        break
      (offset, zsize)=e
      zblock=fp.read(zsize)
      block=decompress(zblock)
      h=self.hash(block)
      dict.__setitem__(self,h,[offset,zsize,None])

  def __loadEntryHead(self,fp,offset=None):
    if offset is not None:
      fp.seek(offset)
    line=fp.readline()
    if len(line) == 0:
      return None
    assert len(line) > 1 and line[-1] == '\n'
    number=line[:-1]
    assert number.isdigit()
    return (fp.tell(), int(number))

  def blocklist(self,h=None):
    return BlockList(self,h)

  def blocksink(self):
    return BlockSink(self)

  def cat(self,h,fp=None):
    if fp is None:
      import sys
      fp=sys.stdout
    for block in self.blocklist(h):
      fp.write(block)

class Store(RawStore):
  pass

class BlockList:
  def __init__(self,S,h=None):
    self.__store=S
    self.__blocks=[]
    if h is not None:
      iblock=S[h]
      while len(iblock) > 0:
        isIndirect=bool(ord(iblock[0]))
        hlen=ord(iblock[1])
        h=iblock[2:2+hlen]
        self.append(h,isIndirect)
        iblock=iblock[2+hlen:]

  def __len__(self):
    return len(self.__blocks)

  def append(self,h,isIndirect):
    self.__blocks.append((isIndirect,h))

  def __getitem__(self,i):
    return self.__blocks[i]

  def pack(self):
    return "".join( [ chr(int(sb[0]))+chr(len(sb[1]))+sb[1] 
                      for sb in self.__blocks
                    ]
                  )

  def __iter__(self):
    S=self.__store
    for (isIndirect,h) in self.__blocks:
      if isIndirect:
        for subblock in BlockList(S,h):
          yield subblock
      else:
        yield S[h]

class BlockSink:
  def __init__(self,S):
    self.__store=S
    self.__lists=[BlockList(S)]
  
  def append(self,block,isIndirect=False):
    S=self.__store
    level=0
    h0=h=S.store(block)
    while True:
      bl=self.__lists[level]
      ##print "append: bl =", `bl`
      if len(bl) < MAX_SUBBLOCKS:
        bl.append(h,isIndirect)
        return h0

      # pack up full block for storage at next indirection level
      fullblock=bl.pack()
      # prepare fresh empty block at this level
      bl=self.__lists[level]=BlockList(S)
      # store the current block in the fresh indirect block
      bl.append(h,isIndirect)
      # advance to the next level of indirection and repeat
      # to store the full indirect block
      level+=1
      isIndirect=True
      block=fullblock
      h=S.store(block)
      if level == len(self.__lists):
        print "new indir level", level
        self.__lists.append(BlockList(S))

  def close(self):
    ''' Store all the outstanding indirect blocks.
        Return the hash of the top level block.
    '''
    S=self.__store
    # record the lower level indirect blocks in their parents
    while len(self.__lists) > 1:
      bl=self.__lists.pop(0)
      self.append(bl.pack(),True)

    # stash and return the topmost indirect block
    h=S.store(self.__lists[0].pack())
    self.__lists=None
    return h

class ManualDataSink:
  ''' A File-like class that supplies only write, close, flush.
      Returned by the sink() method of Store.
      Write(), close() and flush() return the hash code for flushed data.
      Write() usually returns None unless too much is queued; then it
      flushes some data and returns the hash.
      Flush() may return None if there is no queued data.
  '''
  def __init__(self,S):
    ''' Arguments: fp, the writable File object to encapsulate.
                   S, a cs.venti.Store compatible object.
    '''
    self.__bsink=S.blocksink()
    self._q=''

  def close(self):
    ''' Close the file. Return the hash code of the unreported data.
    '''
    self.flush()
    return self.__bsink.close()

  def write(self,data):
    ''' Queue data for writing.
    '''
    self._q+=data

    # flush complete blocks to the store
    edgendx=self.findEdge()
    while edgendx > 0:
      self.__bsink.append(self._q[:edgendx])
      self._q=self._q[edgendx:]
      edgendx=self.findEdge()

  def findEdge(self):
    ''' Return offset of next block start, if present.
    '''
    global MAX_BLOCKSIZE
    if len(self._q) >= MAX_BLOCKSIZE:
      return MAX_BLOCKSIZE
    return 0

  def flush(self):
    ''' Like flush(), but returns the hash code of the data since the last
        flush. It may return None if there is nothing to flush.
    '''
    if len(self._q) == 0:
      return None

    h=self.__bsink.append(self._q)
    self._q=''
    return h

# make patch friendly blocks
class CodeDataSink(ManualDataSink):
  def __init__(self,S):
    ManualDataSink.__init__(self,S)

  def findEdge(self):
    q=self._q
    start=0
    found=q.find('\n')
    while found >= 0:
      if found > MAX_BLOCKSIZE:
        return MAX_BLOCKSIZE

      # python top level things
      if q[found+1:found+5] == "def " \
      or q[found+1:found+7] == "class ":
        return found+1

      # C/C++ etc end of function
      if q[found+1:found+3] == "}\n":
        return found+3

      start=found+1
      found=q.find('\n',start)

    return 0
