#!/usr/bin/python
#
# A cache store, connected to a fast cache and a slower backend.
#       - Cameron Simpson <cs@zip.com.au> 07dec2007
#

from __future__ import with_statement
import sys
from Queue import Queue
from cs.lex import hexify
from cs.venti.store import BasicStore

class CacheStore(BasicStore):
  ''' A CacheStore is a Store front end to a pair of other Stores, a backend
      store and a cache store. The backend store is the "main" store, perhaps
      remote or slow, while the cache store is normally a faster and possibly
      lossy store such as a MemCacheStore or a local disc store.
      
      A block read is satisfied from the cache if possible, otherwise from
      the backend. A block store is stored to the cache and then
      asynchronously to the backend.
  '''
  def __init__(self, backend, cache):
    BasicStore.__init__(self, "Cache(cache=%s, backend=%s)" % (cache, backend))
    backend.open()
    self.backend=backend
    cache.open()
    self.cache=cache
    # secondary queue to process background self.backend operations
    self.__closing=False

  def shutdown(self):
    BasicStore.shutdown(self)
    self.cache.close()
    self.backend.close()

  def flush(self):
    self.cache.flush()
    self.backend.flush()

  def sync(self):
    self.cache.sync()
    self.backend.sync()

  def keys(self):
    for h in self.cache.keys():
      yield h
    for h in self.backend.keys():
      if h not in cache:
        yield h

  def __contains__(self, h):
    if h in self.cache:
      return True
    if h in self.backend:
      return True
    return False

  def __getitem__(self, h):
    if h in self.cache:
      return self.cache[h]
    return self.backend[h]
    
  def add(self, data):
    self.cache.add(data)
    self.backend.add(data)

  def prefetch(self,hs):
    ''' Request from the backend those hashes from 'hs'
        which do not occur in the cache.
    '''
    self.backend.prefetch(self.missing(hs))

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
  def __init__(self, max=1024):
    BasicStore.__init__(self, "MemCacheStore")
    assert max > 1
    self.hashlist=[None for i in range(max)]
    self.low=0                    # offset to oldest hash
    self.used=0
    self.hmap={}                  # cached h->(count,block) tuples

  def flush(self):
    pass
  def sync(self):
    pass

  def keys(self):
    return self.hmap.keys()

  def _hit(self, h, data):
    #assert type(h) is str, "_hit(%s) - not a string" % h
    hmap=self.hmap
    hlist=self.hashlist
    hlen=len(hlist)

    if self.used >= hlen:
      # empty a slot
      oldh=self.hashlist[self.low]
      assert oldh in hmap, "%s not in hmap" % hexify(h)
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
      self.hmap[h]=[1, data]
    self.used+=1
    high=(self.low+self.used)%hlen
    hlist[high]=h

  def __contains__(self, h):
    with self.lock:
      return h in self.hmap

  def __getitem__(self, h):
    with self.lock:
      return self.hmap[h][1]

  def add(self, data):
    with self.lock:
      H = self.hash(data)
      self._hit(H, data)
    return H
