#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import with_statement
import sys
import os.path
import time
from zlib import compress
from threading import BoundedSemaphore
from cs.cache import LRU
from cs.misc import cmderr, warn, progress, verbose, ifverbose, out, fromBS, toBS, fromBSfp, tb, the
from cs.venti import tohex, hash
from cs.venti.store import BasicStore
from cs.venti.datafile import scanFile, getBlock, addBlock

class GDBMStore(BasicStore):
  ''' A Store attached to a GDBM indexed bunch of files.
      'path' is the pathname of a directory containing files named
      'n.vtd' when n is a natural number (IN0).
      These files contain byte sequences of the form:
        BS(zlength)
        zblock
      where zblock is the zlib.compress()ed form of the stored block
      and zlength is the byte length of the zblock.
      The cs.misc.toBS() function is used to represent the length
      as a byte sequence.
      See cs.venti.dataFile for access functions.

      There is also a file 'index' in this directory, which is
      a GDBM file mapping block hash codes (20 byte SHA-1 hashes)
      to (n, offset, zlength) tuples, where 'n' indicated the 'n.vtd'
      file, offset is the offset within that file of the zblock
      and zlength is the length of the zblock.
      This is stored in the GDBM file as the concatenation of the BS()
      encodings of n, offset and zlength respectively.
  '''
  def __init__(self,path,doindex=False):
    BasicStore.__init__(self,"gdbm:%s"%path)
    self.__path=path
    self.logfp=open(os.path.join(path,"log"),"a")
    stores=[ int(name[:-4])
             for name in os.listdir(path)
             if len(name) > 4
                and name.endswith('.vtd')
                and name[:-4].isdigit()
           ]
    if len(stores) == 0:
      stores=[0]
    self.__newStore=max(stores)
    self.__open={}
    for n in stores:
      self.__open[n]=None
    self.__opened=[]    # dumb FIFO of open files
    self.__index=GDBMIndex(path)
    if doindex:
      for n in stores:
        self.__loadIndex(n)
      self.sync()
    self.ioLock=BoundedSemaphore(1)
    self.poolLock=BoundedSemaphore(1)

  def sync(self):
    ''' Sync(0 the store.
        Calls the GDBM sync() function and flush()es any open
        .vtd files.
    '''
    self.__index.sync()
    for n in self.__open:
      fp=self.__open[n]
      if fp:
        fp.flush()

  def store(self,block):
    ''' Store a block, return the hash.
        Appends to the end of the store:
        - toBS(len(zblock))
        - zblock (compressed block)
        Notes the offset to the zblock and the zsize in the GDBM index.
    '''
    h=hash(block)
    if h in self:
      if ifverbose():
        warn(self.__path,"already contains",tohex(h))
      return h

    zblock=compress(block)
    fp=self.__storeOpen(self.__newStore)
    with self.ioLock:
      offset, zsize = addBlock(fp,zblock,True)
    self.__index[h]=(self.__newStore,offset,len(zblock))
    return h

  def fetch(self,h):
    ''' Return block for hash, or None if not present.
    '''
    if h not in self.__index:
      return None
    n,offset,zsize = self.__index[h]
    return getBlock(self.__storeOpen(n),offset,zsize)

  def haveyou(self,h):
    ''' Test if the hash 'h' is present in the store.
    '''
    return h in self.__index

  def __storeOpen(self,n):
    name=str(n)+'.vtd'
    with self.poolLock:
      if self.__open[n] is None:
        # flush an older file
        if len(self.__opened) >= 16:
          oldn=self.__opened.pop(0)
          oldfp=self.__open[oldn]
          self.__open[oldn]=None
        self.__open[n]=newfp=open(os.path.join(self.__path,name),"a+b")
        self.__opened.append(newfp)
      fp=self.__open[n]
    return fp

  def __loadIndex(self,n):
    progress("load index from store", str(n))
    bsn=toBS(n)
    for h, offset, zsize in scanFile(self.__storeOpen(n)):
      self.__index[h]=bsn+toBS(offset)+toBS(zsize)

class GDBMIndex:
  ''' A GDBM index for a GDBMStore.
  '''
  def __init__(self,path):
    import gdbm
    self.lock=BoundedSemaphore(1)
    self.__db=gdbm.open(os.path.join(path,"index"),"cf")

  def sync(self):
    self.__db.sync()

  def __setitem__(self,h,noz):
    # encode then store
    assert len(noz) == 3
    entry="".join(toBS(n) for n in noz)
    with self.lock:
      self.__db[h]=entry

  def __getitem__(self,h):
    # fetch and decode
    with self.lock:
      noz=self.__db[h]
    (n,noz)=fromBS(noz)
    (offset,noz)=fromBS(noz)
    (zsize,noz)=fromBS(noz)
    assert len(noz) == 0
    return (n,offset,zsize)

  def get(self,h,default=None):
    try:
      return self[h]
    except KeyError:
      return default

  def __contains__(self,h):
    noz=self.get(h)
    return noz is not None

  def __len__(self):
    return len(self.__db)

  def keys(self):
    k = self.__db.firstkey()
    while k is not None:
      yield k
      k = self.__db.nextkey()

  def __iter__(self):
    for h in self.__db.keys():
      yield h

  def iterkeys(self):
    for h in self.__db.keys():
      yield h
