#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import with_statement
import sys
import os.path
import time
from zlib import compress, decompress
from threading import BoundedSemaphore
from cs.cache import LRU
from cs.misc import cmderr, warn, progress, verbose, ifverbose, out, fromBS, toBS, fromBSfp, tb, the
from cs.venti import tohex, hash
from cs.venti.store import BasicStore

class GDBMStore(BasicStore):
  def __init__(self,path,doindex=False):
    BasicStore.__init__(self)
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
      if ifverbose(): warn(self.__path,"already contains",tohex(h))
      return h

    zblock=compress(block)
    fp=self.__storeOpen(self.__newStore)
    with self.ioLock:
      fp.seek(0,2)
      fp.write(toBS(len(zblock)))
      offset=fp.tell()
      fp.write(zblock)
    self.__index[h]=(self.__newStore,offset,len(zblock))
    return h

  def fetch(self,h):
    ''' Return block for hash, or raise IndexError if not in store.
    '''
    n,offset,zsize = self.__index[h]
    fp=self.__storeOpen(n)
    fp.seek(offset)
    return decompress(fp.read(zsize))

  def haveyou(self,h):
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
    fp=self.__storeOpen(n)
    while True:
      zsize=fromBSfp(fp)
      if zsize is None:
        break
      offset=fp.tell()
      zblock=fp.read(zsize)
      block=decompress(zblock)
      h=hash(block)
      self.__index[h]=toBS(n)+toBS(offset)+toBS(zsize)

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
