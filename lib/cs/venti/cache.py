#!/usr/bin/python
#
# A cache store, connected to a fast cache and a slower backend.
#       - Cameron Simpson <cs@zip.com.au> 07dec2007
#

from __future__ import with_statement
from threading import BoundedSemaphore
from cs.misc import seq, progress, verbose, debug
from cs.threads import FuncQueue, Q1
from cs.venti import tohex
from cs.venti.store import BasicStore
from heapq import heappush, heappop
import sys

class CacheStore(BasicStore):
  ''' A CacheStore is a Store front end to a pair of other Stores, a backend
      store and a cache store. The backend store is the "main" store, while
      the cache store is normally a faster and possibly lossy store such as a
      MemCacheStore or a local disc store.
      
      A block read is satisfied from the cache is possible, otherwise from
      the backend. A block store is stored to the cache and then
      asynchronously to the backend.
  '''
  def __init__(self,backend,cache):
    BasicStore.__init__(self,"Cache(cache=%s,backend=%s)"%(cache,backend))
    self.backend=backend
    self.cache=cache
    # secondary queue to process background self.backend operations
    self.backQ=FuncQueue(size=256)
    self.__closing=False

  def scan(self):
    if hasattr(self.cache,'scan'):
      for h in self.cache.scan():
        yield h
    if hasattr(self.backend,'scan'):
      for h in self.backend.scan():
        if h not in cache:
          yield h

  def close(self):
    assert not self.__closing, "close() on closed CacheStore, previously closed by %s" % self.__closeCaller
    frame=sys._getframe(1)
    self.__closeCaller="%s()@%s:%d" % (frame.f_code.co_name, frame.f_code.co_filename, frame.f_lineno)
    BasicStore.close(self)
    self.__closing=True
    self.backQ.close()
    self.backend.close()
    self.cache.close()

  def store_bg(self,block,tag,ch):
    h=self.cache.store(block)
    assert h is not None
    ch.put((tag,h))
    ##progress("stored %s in cache" % tohex(h))
    if h not in self.backend:
      self.backQ.call(self.__store_bg2,block)
  def __store_bg2(self,block):
    self.backend.store(block)

  def fetch_bg(self,h,tag,ch):
    inCache=(h in self.cache)
    if inCache:
      ##verbose("fetch %s from cache %s"%(tohex(h), self.cache))
      block=self.cache[h]
    else:
      ##progress("fetch %s from backend %s"%(tohex(h),self.backend))
      block=self.backend[h]
    ch.put((tag,block))
    if not inCache:
      self.backQ.call(self.__fetch_bg2,block)
  def __fetch_bg2(self,block):
    ##progress("fetch: cache %s in %s"%(tohex(h),self.cache))
    self.cache.store(block)

  def prefetch(self,hs):
    ''' Request from the backend those hashes from 'hs'
        which do not occur in the cache.
    '''
    self.backend.prefetch(self.missing(hs))

  def haveyou_bg(self,h,tag,ch):
    if h in self.cache:
      yesno=True
    else:
      yesno=(h in self.backend)
    ch.put((tag,yesno))

  def sync_bg(self,tag,ch):
    backCH=self.backend.sync_a()        # queue the backend
    self.cache.sync()                   # sync the frontend
    backCH.get()                        # wait for the backend
    ch.put((tag,None))                  # report completion

class MemCacheStore(BasicStore):
  ''' A lossy store that keeps an in-memory cache of recent blocks.  It may
      discard older blocks if new ones come in when full and would normally
      be used as the cache part of a CacheStore pair.
  '''
  def __init__(self,max=1024):
    BasicStore.__init__(self,"MemCacheStore")
    assert max > 1
    self.hashlist=[None for i in range(max)]
    self.low=0                    # offset to oldest hash
    self.used=0
    self.hmap={}                  # cached h->(count,block) tuples
    self.memCacheLock=BoundedSemaphore(1)

  def scan(self):
    return self.hmap.keys()

  def _hit(self,h,block):
    assert type(h) is str, "_hit(%s) - not a string" % h
    hmap=self.hmap
    hlist=self.hashlist
    hlen=len(hlist)

    if self.used >= hlen:
      # empty a slot
      oldh=self.hashlist[self.low]
      assert oldh in hmap, "%s not in hmap" % tohex(h)
      hits=hmap[oldh][0]
      if hits <= 1:
        del hmap[oldh]
      else:
        hmap[oldh][0]-=1
      self.low=(self.low+1)%len(hlist)
      self.used-=1

    if h in self.hmap:
      self.hmap[h][0]+=1
    else:
      self.hmap[h]=[1,block]
    self.used+=1
    high=(self.low+self.used)%hlen
    hlist[high]=h

  def store(self,block):
    with self.memCacheLock:
      h=self.hash(block)
      self._hit(h,block)
    return h

  def haveyou(self,h):
    with self.memCacheLock:
      yesno=h in self.hmap
    return yesno

  def fetch(self,h):
    if h not in self:
      return None
    with self.memCacheLock:
      block=self.hmap[h][1]
      self._hit(h,block)
    return block

  def sync(self):
    pass
