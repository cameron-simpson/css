#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import os.path
import time
from zlib import compress, decompress
from cs.misc import cmderr, warn, progress, verbose, out, fromBS, toBS, fromBSfp, tb
from cs.threads import bgReturn

class BasicStore:
  ''' Core functions provided by all Stores.
      For each of the core operations (store, fetch, haveyou) a subclass must
      define at least one of the op or op_a methods.
  '''
  def sync(self):
    assert False, "sync implementation missing"
  def hash(self,block):
    ''' Compute the hash for a block.
    '''
    from cs.venti import hash_sha
    return hash_sha(block)
  def store(self,block):
    ''' Store a block, return its hash.
    '''
    ch=self.store_a(block)
    h=ch.read()
    returnChannel(ch)
    return h
  def store_a(self,block):
    ''' Queue a block for storage, return Channel from which to read the hash.
    '''
    return bgReturn(self.store(block))
  def fetch(self,block):
    ''' Fetch a block given its hash.
    '''
    ch=self.fetch_a(h)
    block=ch.read()
    returnChannel(ch)
    return block
  def fetch_a(self,h):
    ''' Request a block from its hash.
        Return a Channel from which to read the block.
    '''
    return bgReturn(self.fetch(h))
  def haveyou(self,h):
    ''' Test if a hash is present in the store.
    '''
    ch=self.haveyou_a(h)
    yesno=ch.read()
    returnChannel(ch)
    return yesno
  def haveyou_a(self,h):
    ''' Query whether a hash is in the store.
        Return a Channel from which to read the answer.
    '''
    return bgReturn(self.haveyou(h))
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
