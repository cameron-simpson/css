#!/usr/bin/python
#
# File interfaces.      - Cameron Simpson <cs@zip.com.au>
#

from cs.venti import tohex
from cs.venti.dir import FileDirent
from cs.venti.meta import Meta
from cs.venti.blockify import blockFromFile
from threading import Thread
import sys
from cs.threads import IterableQueue
from cs.misc import debug, TODO, FIXME, tb
import __main__

def storeFile(ifp, rsize=None):
  ''' Store the data from ifp, return Dirent.
      TODO: set M.mtime from ifp.fstat().
  '''
  M=Meta()
  B=blockFromFile(ifp)
  B.store()
  return FileDirent(None,M,B)

class ReadFile:
  ''' A read-only file interface supporting seek(), read(), readline(),
      readlines() and tell() methods.
  '''
  def __init__(self,bref):
    self.isdir=False
    if bref.indirect:
      self.__blist=bref.blocklist()
    else:
      self.__blist=BlockList()
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
    b=S[h]
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

  def __iter__(self):
    while True:
      line=self.readline()
      if len(line) == 0:
        break
      yield line

  def readlines(self,sizehint=None):
    lines=[]
    byteCount=0
    for line in self:
      if len(line) == 0:
        break
      lines.append(line)
      byteCount+=len(line)
      if sizehint is not None and byteCount >= sizehint:
        break
    return lines

class WriteNewFile:
  ''' A File-like class that supplies only write, close, flush.
      flush() forces any unstored data to the store.
      close() flushes all data and returns a BlockRef for the whole file.
  '''
  def __init__(self):
    self.__sink=IterableQueue(1)
    self.__topRef=Q1()
    self.__closed=False
    self.__drain=Thread(target=self.__storeBlocks,kwargs={'S':S})
    self.__drain.start()
    atexit.register(self.__cleanup)

  def __cleanup(self):
    if not self.__closed:
      self.close()

  def write(self,data):
    self.__sink.put(data)

  def flush(self):
    TODO("flush unimplemented, should get an intermediate topblockref")
    return None

  def close(self):
    assert not self.__closed
    self.__closed=True
    self.__sink.close()
    return self.__topRef.get()

  def __storeBlocks(self):
    self.__topRef.put(topIndirectBlock(blocksOf(self.__sink)))

class WriteOverFile:
  ''' A File-like class that overwrites an existing 
  '''
  def __init__(self):
    FIXME("WriteOverFile() UNIMPLEMENTED")
    tb()
