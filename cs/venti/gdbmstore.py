#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import os.path
import time
from zlib import compress, decompress
from cs.cache import LRU
from cs.misc import cmderr, warn, progress, verbose, ifverbose, out, fromBS, toBS, fromBSfp, tb
from cs.venti import tohex, hash

class GDBMIndex:
  ''' A GDBM index for a GDBMStore.
  '''

  def __init__(self,path):
    import gdbm
    self.__db=gdbm.open(os.path.join(path,"index"),"cf")

  def sync(self):
    self.__db.sync()

  def __setitem__(self,h,noz):
    # encode then store
    assert len(noz) == 3
    self.__db[h]=toBS(noz[0])+toBS(noz[1])+toBS(noz[2])

  def __getitem__(self,h):
    # fetch and decode
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

class GDBMStore:
  def __init__(self,path,doindex=False):
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

  def sync(self):
    self.__index.sync()
    for n in self.__open:
      fp=self.__open[n]
      if fp:
        fp.flush()

  def store(self,block):
    ''' Store a block, return the hash.
    '''
    h=hash(block)
    if h in self:
      if ifverbose(): warn(self.__path,"already contains",tohex(h))
      return h

    zblock=compress(block)
    fp=self.__storeOpen(self.__newStore)
    fp.seek(0,2)
    fp.write(toBS(len(zblock)))
    offset=fp.tell()
    fp.write(zblock)
    self.__index[h]=(self.__newStore,offset,len(zblock))
    return h

  def __storeOpen(self,n):
    name=str(n)+'.vtd'
    if self.__open[n] is None:
      # flush an older file
      if len(self.__opened) >= 16:
        oldn=self.__opened.pop(0)
        oldfp=self.__open[oldn]
        oldfp.close()
        self.__open[oldn]=None
      self.__open[n]=newfp=open(os.path.join(self.__path,name),"a+b")
      self.__opened.append(newfp)
    return self.__open[n]

  def __getitem__(self,h):
    ''' Return block for hash, or raise IndexError if not in store.
    '''
    n,offset,zsize = self.__index[h]
    fp=self.__storeOpen(n)
    fp.seek(offset)
    return decompress(fp.read(zsize))

  def __contains__(self,h):
    return h in self.__index

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
