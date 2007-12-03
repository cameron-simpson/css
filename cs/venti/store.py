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

class Store:
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

  def __contains__(self,h):
    return h in self.S

  def __getitem__(self,h):
    return self.S[h]

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

  def hash(self,block):
    ''' Compute the hash for a block.
    '''
    from cs.venti import hash_sha
    return hash_sha(block)

  def sync(self):
    if self.logfp is not None:
      self.logfp.flush()

  # compatibility wrapper for __setitem__
  # does a store() and then ensures the hashes match
  def __setitem__(self,h,block):
    stored=self.store(block)
    assert h == stored
    return block
