#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import os.path
import time
from zlib import compress, decompress
from cs.misc import cmderr, warn, progress, verbose, out, fromBS, toBS, fromBSfp, tb, seq
from cs.threads import bgCall, returnChannel

class BasicStore:
  ''' Core functions provided by all Stores.
      For each of the core operations (store, fetch, haveyou) a subclass must
      define at least one of the op or op_a methods.
  '''
  def hash(self,block):
    ''' Compute the hash for a block.
    '''
    from cs.venti import hash_sha
    return hash_sha(block)
  def store(self,block):
    ''' Store a block, return its hash.
    '''
    ch=self.store_a(block)
    tag, h = ch.read()
    assert type(h) is str and type(tag) is int, "h=%s, tag=%s"%(h,tag)
    returnChannel(ch)
    return h
  def store_a(self,block,tag=None,ch=None):
    ''' Queue a block for storage, return Channel from which to read the hash.
        
    '''
    assert type(block) is str and type(tag) is int, "block=%s, tag=%s"%(block,tag)
    return bgCall(self.__store_bg,(block,tag),ch=ch)
  def __store_bg(self,block,tag):
    h=self.store(block)
    return tag, h
  def fetch(self,h):
    ''' Fetch a block given its hash.
    '''
    ch=self.fetch_a(h,None)
    tag, block = ch.read()
    returnChannel(ch)
    return block
  def fetch_a(self,h,tag=None,ch=None):
    ''' Request a block from its hash.
        Return a Channel from which to read the block.
    '''
    return bgCall(self.__fetch_bg,(h,tag),ch=ch)
  def __fetch_bg(self,h,tag):
    block=self.fetch(h)
    return tag, block
  def haveyou(self,h):
    ''' Test if a hash is present in the store.
    '''
    ch=self.haveyou_a(h)
    tag, yesno = ch.read()
    returnChannel(ch)
    return yesno
  def haveyou_a(self,h,tag,ch=None):
    ''' Query whether a hash is in the store.
        Return a Channel from which to read the answer.
    '''
    return bgCall(self.__haveyou_bg,(h,tag),ch=ch)
  def __haveyou_bg(self,h,tag):
    yesno = self.haveyou(h)
    return tag, yesno
  def sync(self):
    ''' Return when the store is synced.
    '''
    ch=self.sync_a()
    tag, dummy = ch.read()
    returnChannel(ch)
  def sync_a(self,tag,ch=None):
    ''' Request that the store be synced.
        Return a Channel from which to read the answer.
    '''
    return bgCall(self.__sync_bg,(tag,),ch=ch)
  def __sync_bg(self,tag):
    self.sync()
    return tag, None
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

class Store(BasicStore):
  ''' A block store connected to a backend BlockStore..
  '''
  def __init__(self,S):
    if type(S) is str:
      if S[0] == '/':
        from cs.venti.gdbmstore import GDBMStore
        S=GDBMStore(S)
      elif S.startswith("tcp:"):
        from cs.venti.tcp import TCPStore
        host, port = S[4:].rsplit(':',1)
        S=TCPStore((host, int(port)))
      else:
        assert False, "unhandled Store name \"%s\"" % S
    self.S=S
    self.logfp=None

  def sync(self):
    self.S.sync()
    if self.logfp is not None:
      self.logfp.flush()
  def store(self,block):
    return self.S.store(block)
  def haveyou(self,h):
    return h in self.S
  def fetch(self,h):
    return self.S.fetch(h)

  def log(self,msg):
    now=time.time()
    fp=self.logfp
    if fp is None:
      fp=sys.stderr
    fp.write("%d %s %s\n" % (now, time.strftime("%Y-%m-%d_%H:%M:%S",time.localtime(now)), msg))
