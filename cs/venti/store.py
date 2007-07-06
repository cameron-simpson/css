#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@zip.com.au>
#

import os.path
from zlib import compress, decompress
from cs.cache import LRU
from cs.misc import cmderr, warn, progress, verbose, out, fromBS, toBS, fromBSfp

class GenericStore:
  ''' Store methods implemented entirely in terms of other public methods,
      or common to all (eg hash()).
  '''
  def __init__(self):
    self.logfp=None

  def log(self,msg):
    import time
    if self.logfp is None:
      import sys
      self.logfp=sys.stderr
    now=time.time()
    self.logfp.write("%d %s %s\n" % (now, time.strftime("%Y-%m-%d_%H:%M:%S",time.localtime(now)), msg))

  def get(self,h,default=None):
    ''' Return block for hash, or None if not present in store.
    '''
    if h not in self:
      return None
    return self[h]

  def hash(self,block):
    ''' Compute the hash for a block.
    '''
    from cs.venti import hash_sha
    return hash_sha(block)

  def sync(self):
    if self.logfp is not None:
      self.logfp.flush()

  # compatibility wrapper for __setitem__
  # does a store() and then ensures the hashes match
  def __setitem__(self,h,block):
    stored=self.store(block)
    assert h == stored
    return block

  def cat(self,bref,fp=None):
    if type(bref) is str:
      from cs.venti.blocks import str2Blockref
      bref=str2BlockRef(bref)
    if fp is None:
      import sys
      fp=sys.stdout
    for b in bref.leaves(self):
      fp.write(b)

  def open(self,path=None,mode="r"):
    import cs.venti.file
    return cs.venti.file.open(self,path=path,mode="r")

  def storeFile(self,ifp,rsize=None):
    import cs.venti.file
    return cs.venti.file.storeFile(self,ifp,rsize=rsize)

  def storeDir(self,path):
    import cs.venti.dir
    return cs.venti.dir.storeDir(self,path)

  def opendir(self,dirref):
    ''' Open a BlockRef that refers to a directory.
    '''
    import cs.venti.dir
    return cs.venti.dir.Dir(self,None,dirref)

  def namei(self,hexarg):
    ''' Given a path of the form
          hexarg/sub1/sub2/...
        return the Dirent for the end of the path, or None.
        hexarg is a tohex(bref.encode()) as used by "vt cat" or "vt ls".
    '''
    from cs.venti import fromhex
    from cs.venti.dir import Dirent
    from cs.venti.blocks import str2BlockRef
    slash=hexarg.find('/')
    if slash < 0:
      # no slash - presume file reference
      return Dirent(str2BlockRef(fromhex(hexarg)),False)

    subpath=[p for p in hexarg[slash+1:].split('/') if len(p)]
    if len(subpath) == 0:
      return Dirent(str2BlockRef(fromhex(hexarg[:slash])), hexarg[-1] == '/')

    hexarg=hexarg[:slash]
    D=self.opendir(fromhex(hexarg))
    while len(subpath) > 1:
      D=D.subdir(subpath.pop(0))
    return D[subpath[0]]

  def walk(self,dirref):
    for i in self.opendir(dirref).walk():
      yield i

class StoreWrapper(GenericStore):
  def __init__(self,S):
    GenericStore.__init__(self)
    self.__S=S

  def hash(self,block):               return self.__S.hash(block)
  def store(self,block):              return self.__S.store(block)
  def sync(self):                     return self.__S.sync()
  def __getitem__(self,h):            return self.__S[h]
  def __contains__(self,h):           return h in self.__S

class RawStoreIndexGDBM:
  ''' A GDBM index for a RawStore.
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

class RawStore(GenericStore):
  def __init__(self,path,doindex=False):
    GenericStore.__init__(self)
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
    self.__index=RawStoreIndexGDBM(path)
    if doindex:
      for n in stores:
        self.__loadIndex(n)
      self.sync()

  def store(self,block):
    ''' Store a block, return the hash.
    '''
    h=self.hash(block)
    if h in self:
      from cs.venti import tohex
      verbose(self.__path,"already contains",tohex(h))
    else:
      zblock=compress(block)
      fp=self.__storeOpen(self.__newStore)
      fp.seek(0,2)
      fp.write(toBS(len(zblock)))
      offset=fp.tell()
      fp.write(zblock)
      self.__index[h]=(self.__newStore,offset,len(zblock))

    return h

  def sync(self):
    GenericStore.sync(self)
    self.__index.sync()
    for n in self.__open:
      fp=self.__open[n]
      if fp:
        fp.flush()

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
      h=self.hash(block)
      self.__index[h]=toBS(n)+toBS(offset)+toBS(zsize)

MAX_LRU=1024
class LRUCacheStore(LRU,StoreWrapper):
  ''' A subclass of RawStore that keeps an LRU cache of referenced blocks.
      TODO: Recode to take an arbitrary Store backend.
            This will let me put Store pools behind a single cache.
  '''
  def __init__(self,backstore,max=None):
    if maxCache is None: maxCache=MAX_LRU
    LRU.__init__(self,backstore,max)
    StoreWrapper.__init__(self,backstore)

class Store(StoreWrapper):
  def __init__(self,S):
    if type(S) is str:
      if S[0] == '/':
        S=RawStore(S)
    StoreWrapper.__init__(self,S)
