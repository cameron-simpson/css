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
from cs.threads import FuncQueue, Q1, DictMonitor
from cs.venti import tohex
from threading import Thread, BoundedSemaphore
from Queue import Queue

class BasicStore:
  ''' Core functions provided by all Stores.
      For each of the core operations (store, fetch, haveyou, sync)
      a subclass must implement at least one of each of the op or op_bg methods.
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
  def store_a(self,block):
    ''' Queue a block for storage, return a cs.threads.Q1 from which to
        read (tag,hash) when stored.
    '''
    assert type(block) is str, \
           "block=%s"%(block,tag)
    assert not self.closing
    ch=Q1()
    self.store_ch(block,ch)
    return ch
  def store_ch(self,block,ch,tag=None):
    ''' Given a block, a channel/Queue and an optional tag,
        queue the block for storage and return the tag.
        If the tag is None or missing, one is generated.
    '''
    assert not self.closing
    if tag is None: tag=seq()
    self.Q.qfunc(self.store_bg,block,tag,ch)
    return tag
  def store_bg(self,block,tag,ch):
    ''' Accept a block for storage, report the hash code on the supplied channel/queue.
        Then store the block synchronously.
    '''
    h=self.hash(block)
    ch.put((tag,h))
    h2=self.store(block)
    assert h == h2, "hash(block)=%s, %s.store() returns %s" % (h, self, h2)
  def fetch(self,h):
    ''' Fetch a block given its hash.
    '''
    assert not self.closing
    ch=self.fetch_a(h,None)
    tag, block = ch.get()
    return block
  def fetch_a(self,h):
    ''' Request a block from its hash.
        Return a cs.threads.Q1 from which to read (tag,block).
    '''
    assert not self.closing
    ch=Q1()
    self.fetch_ch(h,ch)
    return ch
  def fetch_ch(self,h,ch,tag=None):
    ''' Given a hash, a channel/Queue and an optional tag,
        queue the hash for retrieval and return the tag.
        If the tag is None or missing, one is generated.
    '''
    assert not self.closing
    if tag is None: tag=seq()
    self.Q.qfunc(self.__fetch_bg,h,tag,ch)
    return tag
  def fetch_bg(self,h,tag,ch):
    ''' Accept a hash, report the matching block on the supplied channel/queue.
    '''
    block=self.fetch(h)
    ch.put((tag,block))
  def haveyou(self,h):
    ''' Test if a hash is present in the store.
    '''
    assert not self.closing
    ch=self.haveyou_a(h)
    tag, yesno = ch.get()
    return yesno
  def haveyou_a(self,h):
    ''' Query whether a hash is in the store.
        Return a cs.threads.Q1 from which to read (tag, yesno).
    '''
    assert not self.closing
    ch=Q1()
    self.haveyou_ch(h,ch)
    return ch
  def haveyou_ch(self,h,ch,tag=None):
    assert not self.closing
    if tag is None: tag=seq()
    self.Q.qfunc(self.haveyou_bg,h,tag,ch)
    return tag
  def haveyou_bg(self,h,tag,ch):
    ch.put((tag, self.haveyou(h)))
  def sync(self):
    ''' Return when the store is synced.
    '''
    ##assert not self.closing
    debug("store.sync: calling sync_a...")
    ch=self.sync_a()
    debug("store.sync: waiting for response on %s" % ch)
    tag, dummy = ch.get()
    debug("store.sync: response: tag=%s, dummy=%s" % (tag, dummy))
  def sync_a(self):
    ''' Request that the store be synced.
        Return a cs.threads.Q1 from which to read the answer on completion.
    '''
    assert not self.closing
    ch=Q1()
    self.sync_ch(ch)
    return ch
  def sync_ch(self,ch,tag=None):
    if tag is None: tag=seq()
    self.Q.qfunc(self.sync_bg,tag,ch)
    return tag
  def sync_bg(self,tag,ch):
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
        if len(host) == 0:
          host='127.0.0.1'
        S=TCPStore((host, int(port)))
      else:
        assert False, "unhandled Store name \"%s\"" % S
    self.S=S
  def scan(self):
    if hasattr(self.S,'scan'):
      for h in self.S.scan():
        yield h
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

from cs.upd import out, nl

def pullFromSerial(S1,S2):
  asked=0
  for h in S2.scan():
    asked+=1
    out("%d %s" % (asked,tohex(h)))
    if not S1.haveyou(h):
      S1.store(S2.fetch(h))
def pullFrom(S1,S2):
  haveyou_ch=Queue()
  fetch_ch=Queue()
  pending=DictMonitor()
  watcher=Thread(target=_pullWatcher,args=(S1,S2,haveyou_ch,pending,fetch_ch))
  watcher.start()
  fetcher=Thread(target=_pullFetcher,args=(S1,fetch_ch))
  fetcher.start()
  asked=0
  for h in S2.scan():
    asked+=1
    out("%d %s" % (asked,tohex(h)))
    tag=seq()
    pending[tag]=h
    S1.haveyou_ch(h,haveyou_ch,tag)
  nl('draining haveyou queue...')
  haveyou_ch.put((None,asked))
  watcher.join()
  nl('draining fetch queue...')
  fetcher.join()
  out('')

def _pullWatcher(S1,S2,ch,pending,fetch_ch):
  closing=False
  answered=0
  fetches=0
  while not closing or asked > answered:
    tag, yesno = ch.get()
    if tag is None:
      asked=yesno
      closing=True
      continue
    answered+=1
    if closing:
      left=asked-answered
      if left % 10 == 0:
        out(str(left))
    h=pending[tag]
    if not yesno:
      fetches+=1
      S2.fetch_ch(h,fetch_ch)
      warn("requested %s" % tohex(h))
    del pending[tag]
  fetch_ch.put((None,fetches))

def _pullFetcher(S1,ch):
  closing=False
  fetched=0
  while not closing or fetches > fetched:
    tag, block = ch.get()
    if tag is None:
      fetches=block
      closing=True
      continue
    if closing:
      left=fetches-fetched
      if left % 10 == 0:
        out(str(left))
    fetched+=1
    h=S1.store(block)
    warn("stored %s" % tohex(h))
