#!/usr/bin/python
#
# A cache store, connected to a fast cache and a slower backend.
#       - Cameron Simpson <cs@zip.com.au> 07dec2007
#

from cs.misc import seq
from cs.threads import getChannel, FuncQueue
from cs.venti.store import BasicStore
from heapq import heappush, heappop
import sys

class CacheStore(BasicStore):
  def __init__(self,backend,cache):
    BasicStore.__init__(self)
    self.backend=backend
    self.cache=cache

  def close(self):
    self.backend.close()
    self.cache.close()
    BasicStore.close(self)

  def store_a(self,block,tag=None,ch=None):
    assert block is not None
    if tag is None: tag=seq()
    if ch is None: ch=getChannel()
    self.Q.put((self.__store_bg,(tag,block,ch)))
    return ch
  def __store_bg(self,tag,block,ch):
    h=self.cache.store(block)
    ch.write((tag,h))
    self.Q.put((self.__store_bg2,(h,block)))
  def __store_bg2(self,h,block):
    if h not in self.backend:
      self.backend.store(block)

  def fetch_a(self,h,tag=None,ch=None):
    if tag is None: tag=seq()
    if ch is None: ch=getChannel()
    self.Q.put((self.__fetch_bg,(tag,h,ch)))
    return ch
  def __fetch_bg(self,tag,h,ch):
    if h in self.cache:
      block=self.cache[h]
    else:
      block=self.backend[h]
    ch.write((tag,block))

  def haveyou_a(self,h,tag=None,ch=None):
    if tag is None: tag=seq()
    if ch is None: ch=getChannel()
    self.Q.put((self.__haveyou_bg,(tag,h,ch)))
    return ch
  def __haveyou_bg(self,tag,h,ch):
    if h in self.cache:
      yesno=True
    else:
      yesno=self.backend[h]
    ch.write((tag,yesno))

  def sync_a(self,tag=None,ch=None):
    if tag is None: tag=seq()
    if ch is None: ch=getChannel()
    self.Q.put((self.__sync_bg,(tag,ch)))
    return ch
  def __sync_bg(self,tag,ch):
    self.cache.sync()
    self.backend.sync()
    ch.write((tag,None))

class MemCacheStore(BasicStore):
  ''' A lossy store that keeps an in-memory cache of recent blocks.
  '''
  def __init__(self,max=1024):
    BasicStore.__init__(self)
    assert max > 1
    self.max=max
    self.heap=[]
    self.hmap={}
    self.rawmap={}

  def _stash(self,hits,h,block):
    assert hits >= 0
    assert h is not None
    assert block is not None
    heap=self.heap

    # make room
    while len(heap) > max:
      heappop(heap)

    hits+=1
    tup=(hits, h, block)
    self.hmap[h]=tup
    self.rawmap[block]=tup
    if len(heap) == max:
      heapreplace(heap, tup)
    else:
      heappush(heap, tup)
    
  def store(self,block):
    rawmap=self.rawmap
    if block in rawmap:
      hits, h, mblock = self.rawmap[block]
      assert block == mblock
    else:
      hits=0
      h=self.hash(block)
      mblock=block
    self._stash(hits, h, mblock)

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
