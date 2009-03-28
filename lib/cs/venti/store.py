#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@zip.com.au>
#

''' Basic Store classes.

    Throughout these classes the term 'channel' means an object with a .get()
    method and usually a .put() method (unless it is instantiated with a
    pre-queued value for the .get()). It may be a Queue, Q1, Channel, Get1
    or any similar object for delivery of a result "later".
'''

from __future__ import with_statement
import sys
import os
import os.path
import time
import thread
from Queue import Queue
from cs.misc import cmderr, debug, isdebug, warn, progress, verbose, out, tb, seq, Loggable
from cs.serialise import toBS, fromBS, fromBSfp
from cs.threads import FuncQueue, Q1, DictMonitor, NestingOpenClose
from cs.venti import tohex

class BasicStore(Loggable,NestingOpenClose):
  ''' Core functions provided by all Stores.
      For each of the core operations (store, fetch, haveyou, sync)
      a subclass must implement at least one of each of the op or op_bg methods.
      The methods here are written in terms of each other.

      With respect to the op_bg methods, these return a (tag, channel) pair.
      In normal use the caller will care only about the channel or the tag,
      rarely both. If no channel is presupplied then the return channel is
      a single use channel on which only relevant (tag, result) response will
      be seen, so the tag is superfluous. In the case where a channel is
      presupplied it is possible for responses to requests to arrive in
      arbitrary order, so the tag is needed to identify the response with the
      calling request; however the caller already knows the channel.

      The hint noFlush, if specified and True, suggests that streaming
      store connections need not flush the request stream because another
      request will follow very soon after this request. This allows
      for more efficient use of streams. Users who set this hint to True
      must ensure that a "normal" flushing request, or a call of the
      ._flush() method, follows any noFlush requests promptly otherwise
      deadlocks may ensue.
  '''
  def __init__(self,name):
    Loggable.__init__(self,name)
    NestingOpenClose.__init__(self)
    self.name=name
    self.logfp=None
    self.__funcQ=FuncQueue()
    self.__funcQ.open()

  def __str__(self):
    return "Store(%s)" % self.name

  def shutdown(self):
    ''' Called by final NestingOpenClose.close().
    '''
    self.sync()
    self.__funcQ.close()

  def hash(self,block):
    ''' Compute the hash for a block.
    '''
    from cs.venti.hash import hash_sha
    return hash_sha(block)

  def _tagch(self,ch=None):
    ''' Allocate a tag and a (optionally) channel.
    '''
    if ch is None: ch=Q1()
    return seq(), ch

  def _flush(self):
    pass

  def store(self,block):
    ''' Store a block, return its hash.
    '''
    ##self.log("store %d bytes" % len(block))
    tag, ch = self.store_bg(block)
    assert type(tag) is int
    tag2, h = ch.get()
    assert type(h) is str and type(tag2) is int, "h=%s, tag2=%s"%(h,tag2)
    assert tag == tag2
    return h

  def store_bg(self,block,noFlush=False,ch=None):
    ''' Accept a block for storage, return a channel and tag for the completion.
        The optional ch parameter may specify the channel.
        On completion, a (tag, hashcode) tuple is put on the channel.
    '''
    tag, ch = self._tagch(ch)
    self.__funcQ.dispatch(self.__store_bg2,(block,ch,tag))
    return tag, ch
  def __store_bg2(self,block,ch,tag):
    ch.put(tag, self.store(block))

  def fetch(self,h):
    ''' Fetch a block given its hash.
    '''
    ##self.log("fetch %s" % tohex(h))
    tag, ch = self.fetch_bg(h)
    assert type(tag) is int
    tag, block = ch.get()
    return block
  def fetch_bg(self,h,noFlush=False,ch=None):
    ''' Request a block from its hash, return a channel and tag for the
        completion. The optional ch parameter may specify the channel.
        On completion, a (tag, block) tuple is put on the channel.
    '''
    tag, ch = self._tagch(ch)
    self.__funcQ.dispatch(self.__fetch_bg2,(h,ch,tag))
    return tag, ch
  def __fetch_bg2(self,h,ch,tag):
    ch.put((tag,self.fetch(h)))

  def haveyou(self,h):
    ''' Test if a hash is present in the store.
    '''
    ##self.log("haveyou %s" % tohex(h))
    assert not self.closed
    tag, ch = self.haveyou_bg(h)
    tag, yesno = ch.get()
    return yesno
  def haveyou_bg(self,h,noFlush=False,ch=None):
    ''' Query whether a hash is in the store.
        Return a (tag, channel). A .get() on the channel will return
        (tag, yesno).
    '''
    tag, ch = self._tagch(ch)
    self.__funcQ.dispatch(self.__haveyou_bg2,(h,tag,ch))
    return tag, ch
  def __haveyou_bg2(self,h,tag,ch):
    ch.put((tag,self.haveyou(h)))

  def sync(self):
    ''' Return when the store is synced.
    '''
    self.log("sync")
    tag, ch = self.sync_bg()
    tag, dummy = ch.get()

  def sync_bg(self,noFlush=False,ch=None):
    tag, ch = self._tagch(ch)
    self.__funcQ.dispatch(self.__sync_bg2,(tag,ch))
    return tag, ch
  def __sync_bg2(self,tag,ch):
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

  def missing(self,hs):
    ''' Yield hashcodes that are not in the store from an iterable hash
        code list.
    '''
    for h in hs:
      if h not in self.cache:
        yield h

  def prefetch(self,hs):
    ''' Prefetch the blocks associated with hs, an iterable returning hashes.
        This is intended to hint that these blocks will be wanted soon,
        and so implementors might queue the fetches on an "idle" queue so as
        not to penalise other store users.
        This default implementation does nothing, which may be perfectly
        legitimate for some stores.
    '''
    pass

  def multifetch(self,hs):
    ''' Generator returning a bunch of blocks in sequence corresponding to
        the iterable hashes 'hs'.
    '''
    # dispatch a thread to request the blocks
    tagQ=Queue(0)       # the thread echoes tags for eash hash in hs
    FQ=Queue(0)         # and returns (tag,block) on FQ, possibly out of order
    Thread(target=self.__multifetch_rq,args=(hs,tagQ,FQ)).start()

    waiting={}  # map of blocks that arrived out of order
    frontTag=None
    while True:
      if frontTag is not None:
        # we're waiting for a particular tag
        tag, block = FQ.get()
        if tag == frontTag:
          # it is the one desired, return it
          yield block
          frontTag = None
        else:
          # not what we wanted - save it for later
          waiting[tag]=block
      # get the next desired tag whose block has not yet arrived
      while frontTag is None:
        # get the next desired tag
        frontTag = tagQ.get()
        if frontTag is None:
          # end of tag stream
          break
        if frontTag in waiting:
          # has this tag already arrived?
          yield waiting.pop(frontTag)
          frontTag = None
    assert len(waiting.keys()) == 0

  def __multifetch_rq(self,hs,tagQ,FQ):
    h0=None
    for h in hs:
      tag, ch = self._tagch(FQ)
      self.fetch_bg(h,noFlush=True,ch=FQ)
      tagQ.put(tag)
      h0=h
    if h0 is not None:
      # dummy request to flush stream
      self.haveyou_bg(h0,ch=Get1())
    tagQ.put(None)

class Store(BasicStore):
  ''' A store connected to a backend store or a type designated by a string.
  '''
  def __init__(self,S):
    ''' Trivial wrapper for another store.
        If 'S' is a string:
          /path/to/store designates a GDBMStore.
          |shell-command designates a command to connect to a StreamStore.
          tcp:addr:port designates a TCP target serving a StreamStore.
        Otherwise 'S' is presumed already to be a store.
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
    S.open()
    self.S=S

  def scan(self):
    if hasattr(self.S,'scan'):
      for h in self.S.scan():
        yield h
  def shutdown(self):
    self.S.close()
    BasicStore.shutdown(self)

  def join(self):
    BasicStore.join(self)
    self.S.join()

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
  haveyou_ch=Queue(size=256)
  fetch_ch=Queue(size=256)
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
