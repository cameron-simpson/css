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

  def cat(self,bref,fp=None):
    if type(bref) is str:
      from cs.venti.blocks import str2Blockref
      bref=str2BlockRef(bref)
    if fp is None:
      import sys
      fp=sys.stdout
    for b in bref.leaves(self):
      fp.write(b)

  def open(self,path=None,mode="r"):
    import cs.venti.file
    return cs.venti.file.open(self,path=path,mode="r")

  def storeFile(self,ifp,rsize=None):
    import cs.venti.file
    return cs.venti.file.storeFile(self,ifp,rsize=rsize)

  def storeDir(self,path):
    import cs.venti.dir
    return cs.venti.dir.storeDir(self,path)

  def opendir(self,dirref):
    ''' Open a BlockRef that refers to a directory,
        returns a cs.venti.dir.Dir.
    '''
    import cs.venti.dir
    return cs.venti.dir.Dir(self,None,dirref)

  def namei(self,path,bref=None):
    ''' Given a path and an optional BlockRef,
        return the Dirent for the end of the path, or None.
        hexarg is a tohex(bref.encode()) as used by "vt cat" or "vt ls".
    '''
    from cs.venti import fromhex, tohex
    from cs.venti.dir import Dirent, Dir
    from cs.venti.blocks import str2BlockRef
    if bref is None:
      slash=path.find('/')
      if slash < 0:
        return Dirent(str2BlockRef(fromhex(path)), False)
      bref=str2BlockRef(fromhex(path[:slash]))
      warn("bref="+str(bref))
      path=path[slash+1:]

    E=Dirent(bref, True)
    parent=None
    while len(path) > 0:
      if not E.isdir:
        return None
      D=Dir(self,parent,E.bref)
      slash=path.find('/')
      if slash < 0:
        head=path
        path=''
      else:
        head=path[:slash]
        tail=path[slash+1:]
      E=D[head]
      parent=D

    return E

  def walk(self,dirref):
    return self.opendir(dirref).walk()
