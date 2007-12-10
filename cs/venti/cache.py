#!/usr/bin/python
#
# A cache store, connected to a fast cache and a slower backend.
#       - Cameron Simpson <cs@zip.com.au> 07dec2007
#

from cs.misc import seq
from cs.threads import getChannel, FuncQueue
from cs.venti.store import BasicStore
import sys

class CacheStore(BasicStore):
  def __init__(self,backend,cache):
    BasicStore.__init__(self)
    self.backend=backend
    self.cache=cache
    self.backQ=FuncQueue()

  def close(self):
    self.backend.close()
    self.cache.close()
    self.backQ.close()
    BasicStore.close(self)

  def store_a(self,block,tag=None,ch=None):
    if tag is None: tag=seq()
    if ch is None: ch=getChannel()
    self.Q.put((self.__store_bg,(tag,block,ch)))
    return ch
  def __store_bg(self,tag,block,ch):
    h=self.cache.store(block)
    ch.write((tag,h))
    self.backQ.put((self.__store_bg2,(h,block)))
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
