#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import os
import os.path
import time
from zlib import compress, decompress
from cs.misc import cmderr, warn, progress, verbose, out, fromBS, toBS, fromBSfp, tb, seq
from cs.threads import getChannel, returnChannel, FuncQueue
from threading import Thread
from Queue import Queue

class BasicStore:
  ''' Core functions provided by all Stores.
      For each of the core operations (store, fetch, haveyou) a subclass must
      define at least one of the op or op_a methods.
  '''
  def __init__(self):
    self.logfp=None
    self.closing=False
    self.Q=FuncQueue()

  def close(self):
    self.closing=True
    self.Q.close()

  def hash(self,block):
    ''' Compute the hash for a block.
    '''
    from cs.venti import hash_sha
    return hash_sha(block)
  def store(self,block):
    ''' Store a block, return its hash.
    '''
    assert not self.closing
    ch=self.store_a(block)
    tag, h = ch.read()
    assert type(h) is str and type(tag) is int, "h=%s, tag=%s"%(h,tag)
    returnChannel(ch)
    return h
  def store_a(self,block,tag=None,ch=None):
    ''' Queue a block for storage, return Channel from which to read the hash.
    '''
    assert type(block) is str and type(tag) is int, "block=%s, tag=%s"%(block,tag)
    assert not self.closing
    if ch is None: ch=getChannel()
    self.Q.put((self.__store_bg,(block,tag,ch)))
    return ch
  def __store_bg(self,block,tag,ch):
    h=self.store(block)
    ch.write((tag,h))
  def fetch(self,h):
    ''' Fetch a block given its hash.
    '''
    assert not self.closing
    ch=self.fetch_a(h,None)
    tag, block = ch.read()
    returnChannel(ch)
    return block
  def fetch_a(self,h,tag=None,ch=None):
    ''' Request a block from its hash.
        Return a Channel from which to read the block.
    '''
    assert not self.closing
    if ch is None: ch=getChannel()
    self.Q.put((self.__fetch_bg,(h,tag,ch)))
    return ch
  def __fetch_bg(self,h,tag,ch):
    block=self.fetch(h)
    ch.write((tag,block))
  def haveyou(self,h):
    ''' Test if a hash is present in the store.
    '''
    assert not self.closing
    ch=self.haveyou_a(h)
    tag, yesno = ch.read()
    returnChannel(ch)
    return yesno
  def haveyou_a(self,h,tag=None,ch=None):
    ''' Query whether a hash is in the store.
        Return a Channel from which to read the answer.
    '''
    assert not self.closing
    if ch is None: ch=getChannel()
    self.Q.put((self.__haveyou_bg,(block,tag,ch)))
    return ch
  def __haveyou_bg(self,h,tag,ch):
    yesno = self.haveyou(h)
    ch.write((tag, yesno))
  def sync(self):
    ''' Return when the store is synced.
    '''
    assert not self.closing
    ch=self.sync_a()
    tag, dummy = ch.read()
    returnChannel(ch)
  def sync_a(self,tag=None,ch=None):
    ''' Request that the store be synced.
        Return a Channel from which to read the answer.
    '''
    assert not self.closing
    if ch is None: ch=getChannel()
    self.Q.put((self.__sync_bg,(tag,ch)))
    return ch
  def __sync_bg(self,tag,ch):
    self.sync()
    ch.write((tag,None))
  def __contains__(self, h):
    return self.haveyou(h)
  def __getitem__(self,h):
    return self.fetch(h)
  def get(self,h,default=None):
    ''' Return block for hash, or None if not present in store.
    '''
    if h not in self:
      return None
    return self[h]

  def log(self,msg):
    now=time.time()
    fp=self.logfp
    if fp is None:
      fp=sys.stderr
    fp.write("%d %s %s\n" % (now, time.strftime("%Y-%m-%d_%H:%M:%S",time.localtime(now)), msg))

class Store(BasicStore):
  ''' A block store connected to a backend BlockStore..
  '''
  def __init__(self,S):
    BasicStore.__init__(self)
    if type(S) is str:
      if S[0] == '/':
        from cs.venti.gdbmstore import GDBMStore
        S=GDBMStore(S)
      elif S[0] == '|':
        toChild, fromChild = os.popen2(S[1:])
        from cs.venti.stream import StreamStore
        S=StreamStore(toChild,fromChild)
      elif S.startswith("tcp:"):
        from cs.venti.tcp import TCPStore
        host, port = S[4:].rsplit(':',1)
        S=TCPStore((host, int(port)))
      else:
        assert False, "unhandled Store name \"%s\"" % S
    self.S=S
  def close(self):
    self.S.close()
    BasicStore.close(self)
  def store(self,block):
    return self.S.store(block)
  def fetch(self,h):
    return self.S.fetch(h)
  def haveyou(self,h):
    return h in self.S
  def sync(self):
    self.S.sync()
    if self.logfp is not None:
      self.logfp.flush()
