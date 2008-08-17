#!/usr/bin/python
#
# A cache store, connected to a fast cache and a slower backend.
#       - Cameron Simpson <cs@zip.com.au> 07dec2007
#

from __future__ import with_statement
import sys
from thread import allocate_lock
from Queue import Queue
from cs.misc import seq, progress, verbose, debug
from cs.threads import FuncQueue, Q1
from cs.venti import tohex
from cs.venti.store import BasicStore

class CacheStore(BasicStore):
  ''' A CacheStore is a Store front end to a pair of other Stores, a backend
      store and a cache store. The backend store is the "main" store, while
      the cache store is normally a faster and possibly lossy store such as a
      MemCacheStore or a local disc store.
      
      A block read is satisfied from the cache if possible, otherwise from
      the backend. A block store is stored to the cache and then
      asynchronously to the backend.
  '''
  def __init__(self,backend,cache):
    BasicStore.__init__(self,"Cache(cache=%s,backend=%s)"%(cache,backend))
    backend.open()
    self.backend=backend
    cache.open()
    self.cache=cache
    # secondary queue to process background self.backend operations
    self.backQ=FuncQueue(size=256)
    self.backQ.open()
    self.__closing=False

  def shutdown(self):
    self.cache.close()
    self.backQ.close()
    self.backend.close()
    BasicStore.shutdown(self)

  def flush(self):
    self.cache._flush()
    self.backend._flush()

  def scan(self):
    if hasattr(self.cache,'scan'):
      for h in self.cache.scan():
        yield h
    if hasattr(self.backend,'scan'):
      for h in self.backend.scan():
        if h not in cache:
          yield h

  def store_bg(self,block,ch=None):
    tag, ch = self._tagch(ch)
    self.backQ.dispatch(self.__store_bg2,(block,ch,tag))
    return tag, ch
  def __store_bg2(self,block,ch,tag):
    h = self.cache.store(block)
    ch.put((tag, h))
    if h not in self.backend:
      self.backend.store(block)

  def fetch_bg(self,h,noFlush=False,ch=None):
    tag, ch = self._tagch(ch)
    self.backQ.dispatch(self.__fetch_bg2,(h,ch,tag))
    return tag, ch
  def __fetch_bg2(self,h,ch,tag):
    inCache=(h in self.cache)
    if inCache:
      ##verbose("fetch %s from cache %s"%(tohex(h), self.cache))
      block=self.cache[h]
    else:
      ##progress("fetch %s from backend %s"%(tohex(h),self.backend))
      block=self.backend[h]
    ch.put((tag,block))
    if not inCache:
      self.cache.store(block)

  def prefetch(self,hs):
    ''' Request from the backend those hashes from 'hs'
        which do not occur in the cache.
    '''
    self.backend.prefetch(self.missing(hs))

  def haveyou(self,h):
    if h in self.cache:
      return True
    return h in self.backend

  def sync(self):
    Q=Queue(2)
    tag, ch = self.cache.sync_bg(ch=Q)
    tag, ch = self.backend.sync_bg(ch=Q)
    Q.get()
    Q.get()

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
    self.__memCacheLock=allocate_lock()

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
    with self.__memCacheLock:
      h=self.hash(block)
      self._hit(h,block)
    return h

  def haveyou(self,h):
    with self.__memCacheLock:
      yesno=h in self.hmap
    return yesno

  def fetch(self,h):
    if h not in self:
      return None
    with self.__memCacheLock:
      block=self.hmap[h][1]
      self._hit(h,block)
    return block

  def sync(self):
    pass
