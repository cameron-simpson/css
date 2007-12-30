#!/usr/bin/python
#
# A cache store, connected to a fast cache and a slower backend.
#       - Cameron Simpson <cs@zip.com.au> 07dec2007
#

from cs.misc import seq, progress, verbose
from cs.threads import FuncQueue, Q1
from cs.venti import tohex
from cs.venti.store import BasicStore
from heapq import heappush, heappop
import sys

class CacheStore(BasicStore):
  def __init__(self,backend,cache):
    BasicStore.__init__(self,"Cache(cache=%s,backend=%s)"%(cache,backend))
    self.backend=backend
    self.cache=cache

  def close(self):
    BasicStore.close(self)
    self.backend.close()
    self.cache.close()

  def store_a(self,block,tag=None,ch=None):
    assert block is not None
    if tag is None: tag=seq()
    if ch is None: ch=Q1()
    self.Q.put((self.__store_bg,(tag,block,ch)))
    return ch
  def __store_bg(self,tag,block,ch):
    h=self.cache.store(block)
    assert h is not None
    ch.put((tag,h))
    ##progress("stored %s in cache" % tohex(h))
    if h not in self.backend:
      self.backend.store(block)

  def fetch_a(self,h,tag=None,ch=None):
    if tag is None: tag=seq()
    if ch is None: ch=Q1()
    self.Q.put((self.__fetch_bg,(tag,h,ch)))
    return ch
  def __fetch_bg(self,tag,h,ch):
    inCache=(h in self.cache)
    if inCache:
      ##verbose("fetch %s from cache %s"%(tohex(h), self.cache))
      block=self.cache[h]
    else:
      ##progress("fetch %s from backend %s"%(tohex(h),self.backend))
      block=self.backend[h]
    ch.put((tag,block))
    if not inCache:
      ##progress("fetch: cache %s in %s"%(tohex(h),self.cache))
      self.cache.store(block)

  def haveyou_a(self,h,tag=None,ch=None):
    if tag is None: tag=seq()
    if ch is None: ch=Q1()
    self.Q.put((self.__haveyou_bg,(tag,h,ch)))
    return ch
  def __haveyou_bg(self,tag,h,ch):
    if h in self.cache:
      yesno=True
    else:
      yesno=(h in self.backend)
    ch.put((tag,yesno))

  def sync_a(self,tag=None,ch=None):
    if tag is None: tag=seq()
    if ch is None: ch=Q1()
    self.Q.put((self.__sync_bg,(tag,ch)))
    return ch
  def __sync_bg(self,tag,ch):
    backCH=self.backend.sync_a()
    self.cache.sync()
    backCH.get()
    ch.put((tag,None))

class MemCacheStore(BasicStore):
  ''' A lossy store that keeps an in-memory cache of recent blocks.
  '''
  def __init__(self,max=1024):
    BasicStore.__init__(self,"MemCacheStore")
    assert max > 1
    self.max=max
    self.heap=[]
    self.hmap={}
    self.hcount={}

  def _stash(self,hits,h,block):
    assert hits >= 0
    assert h is not None
    assert block is not None
    heap=self.heap
    hmap=self.hmap

    # make room
    while len(heap) >= max:
      tup=heappop(heap)
      th=tup[1]
      count=self.hcount[th]
      if count == 1:
        del hmap[th]
      else:
        self.hcount[th]=count-1

    hits+=1
    tup=(hits, h, block)
    self.hmap[h]=tup
    heappush(heap, tup)
    if h in self.hcount:
      self.hcount[h]+=1
    else:
      self.hcount[h]=1
    
  def store(self,block):
    hmap=self.hmap
    h=self.hash(block)
    if h in hmap:
      hits, mh, mblock = self.hmap[h]
      assert h == mh
      assert block == mblock
    else:
      hits=0
      mh=h
      mblock=block
    self._stash(hits, mh, mblock)
    return h

  def haveyou(self,h):
    if h not in self.hmap:
      return False
    hits, h, mblock = self.hmap[h]
    self._stash(hits, h, mblock)
    return True

  def fetch(self,h):
    return self.hmap[h][2]

  def sync(self):
    pass
