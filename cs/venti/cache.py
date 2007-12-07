#!/usr/bin/python
#
# A cache store, connected to a fast cache and a slower backend.
#       - Cameron Simpson <cs@zip.com.au> 07dec2007
#

from cs.venti.store import BasicStore
from cs.threads import getChannel

class CacheStore(BasicStore):
  def __init__(self,backend,cache):
    self.backend=backend
    self.cache=cache

  def haveyou_a(self,h):
    ch=getChannel()
    Thread(target=self.store_bg,kw={'self':self, 'ch':ch, 'block':block}).start()
    return ch
  def haveyou_bg(self,ch,h):
    if h in self.cache:
      ch.write(True)
    else:
      ch.write(h in self.backend)

  def store_a(self,block):
    ch=getChannel()
    Thread(target=self.store_bg,kw={'self':self, 'ch':ch, 'block':block}).start()
    return ch
  def store_bg(self,ch,block):
    h=self.cache.store(block)
    ch.write(h)
    if h not in self.backend:
      self.backend.store(h)

  def fetch_a(self,h):
    ch=getChannel()
    Thread(target=self.fetch_bg,kw={'self':self, 'ch':ch, 'h':h}).start()
    return ch
  def fetch_bg(self,ch,h):
    if h in self.cache:
      ch.write(self.cache[h])
    else:
      block=self.backend[h]
      ch.write(block)
      self.cache.store(block)
