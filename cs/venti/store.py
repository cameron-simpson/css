#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import with_statement
import sys
import os
import os.path
import time
from zlib import compress, decompress
from cs.misc import cmderr, debug, warn, progress, verbose, out, fromBS, toBS, fromBSfp, tb, seq
from cs.threads import FuncQueue, Q1
from cs.venti import tohex
from threading import Thread, BoundedSemaphore
from Queue import Queue

class BasicStore:
  ''' Core functions provided by all Stores.
      For each of the core operations (store, fetch, haveyou, sync)
      a subclass must define at least one of the op or op_a methods.
  '''
  def __init__(self,name):
    self.name=name
    self.logfp=None
    self.closing=False
    self.Q=FuncQueue()
    self.lastBlock=None
    self.lastBlockLock=BoundedSemaphore(1)

  def __str__(self):
    return "Store(%s)" % self.name

  def close(self):
    if not self.closing:
      self.sync()
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
    tag, h = ch.get()
    assert type(h) is str and type(tag) is int, "h=%s, tag=%s"%(h,tag)
    return h
  def store_a(self,block,tag=None,ch=None):
    ''' Queue a block for storage, return a cs.threads.Q1 from which to
        read the hash when stored.
    '''
    assert type(block) is str and type(tag) is int, \
           "block=%s, tag=%s"%(block,tag)
    assert not self.closing
    if ch is None: ch=Q1()
    self.Q.put((self.__store_bg,(block,tag,ch)))
    return ch
  def __store_bg(self,block,tag,ch):
    h=self.store(block)
    ch.put((tag,h))
  def fetch(self,h):
    ''' Fetch a block given its hash.
    '''
    assert not self.closing
    block=self.lastFetch(h)
    if block is not None:
      return block
    ch=self.fetch_a(h,None)
    tag, block = ch.get()
    with self.lastBlockLock:
      self.lastBlock=(h,block)
    return block
  def fetch_a(self,h,tag=None,ch=None):
    ''' Request a block from its hash.
        Return a cs.threads.Q1 from which to read the block.
    '''
    assert not self.closing
    if ch is None: ch=Q1(useQueue=True)
    block=self.lastFetch(h)
    if block is not None:
      ch.put((tag,block))
    else:
      self.Q.put((self.__fetch_bg,(h,tag,ch)))
    return ch
  def __fetch_bg(self,h,tag,ch):
    block=self.fetch(h)
    ch.put((tag,block))
  def lastFetch(self,h):
    with self.lastBlockLock:
      LB=self.lastBlock
    if LB is not None:
      Lh, Lblock = LB
      if Lh == h:
        return Lblock
    return None
  def haveyou(self,h):
    ''' Test if a hash is present in the store.
    '''
    assert not self.closing
    ch=self.haveyou_a(h)
    tag, yesno = ch.get()
    return yesno
  def haveyou_a(self,h,tag=None,ch=None):
    ''' Query whether a hash is in the store.
        Return a cs.threads.Q1 from which to read the answer.
    '''
    assert not self.closing
    if ch is None: ch=Q1()
    self.Q.put((self.__haveyou_bg,(h,tag,ch)))
    return ch
  def __haveyou_bg(self,h,tag,ch):
    yesno = self.haveyou(h)
    ch.put((tag, yesno))
  def sync(self):
    ''' Return when the store is synced.
    '''
    ##assert not self.closing
    debug("store.sync: calling sync_a...")
    ch=self.sync_a()
    debug("store.sync: waiting for response on %s" % ch)
    tag, dummy = ch.get()
    debug("store.sync: response: tag=%s, dummy=%s" % (tag, dummy))
  def sync_a(self,tag=None,ch=None):
    ''' Request that the store be synced.
        Return a cs.threads.Q1 from which to read the answer.
    '''
    assert not self.closing
    if ch is None: ch=Q1()
    self.Q.put((self.__sync_bg,(tag,ch)))
    return ch
  def __sync_bg(self,tag,ch):
    self.sync()
    ch.put((tag,None))
  def __contains__(self, h):
    ''' Wrapper for haveyou().
    '''
    return self.haveyou(h)
  def __getitem__(self,h):
    ''' Wrapper for fetch(h).
    '''
    if h not in self:
      raise KeyError, "%s: %s not in store" % (self, tohex(h))
    block=self.fetch(h)
    if block is None:
      raise KeyError, "%s: fetch(%s) returned None" % (self, tohex(h))
    return block
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
  ''' A store connected to a backend store or a type designated by a string.
  '''
  def __init__(self,S):
    ''' Trivial wrapper for another store.
        If 'S' is a string:
          /path/to/store designates a GDBMStore.
          |shell-command designates a command to connect to a StreamStore.
          tcp:addr:port designates a TCP target serving a StreamStore.
    '''
    BasicStore.__init__(self,str(S))
    if type(S) is str:
      if S[0] == '/':
        from cs.venti.gdbmstore import GDBMStore
        S=GDBMStore(S)
      elif S[0] == '|':
        toChild, fromChild = os.popen2(S[1:])
        from cs.venti.stream import StreamStore
        S=StreamStore(S,toChild,fromChild)
      elif S.startswith("tcp:"):
        from cs.venti.tcp import TCPStore
        host, port = S[4:].rsplit(':',1)
        S=TCPStore((host, int(port)))
      else:
        assert False, "unhandled Store name \"%s\"" % S
    self.S=S
  def close(self):
    BasicStore.close(self)
    self.S.close()
  def store(self,block):
    return self.S.store(block)
  def fetch(self,h):
    return self.S.fetch(h)
  def haveyou(self,h):
    return self.S.haveyou(h)
  def sync(self):
    self.S.sync()
    if self.logfp is not None:
      self.logfp.flush()
