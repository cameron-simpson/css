#!/usr/bin/python -tt
#
# Stream protocol for vt stores.
#       - Cameron Simpson <cs@zip.com.au> 06dec2007
#
# TODO: T_SYNC, to wait for pending requests before returning
#

from __future__ import with_statement
from threading import Thread, BoundedSemaphore
from cs.misc import seq, toBS, fromBSfp, debug, ifdebug, tb
from cs.threads import getChannel, returnChannel, JobQueue
from cs.venti import tohex
from cs.venti.store import BasicStore

T_STORE=0       # block->hash
T_FETCH=1       # hash->block
T_HAVEYOU=2     # hash->boolean
T_SYNC=3        # no args
# encode tokens once for performance
enc_STORE=toBS(T_STORE)
enc_FETCH=toBS(T_FETCH)
enc_HAVEYOU=toBS(T_HAVEYOU)
enc_SYNC=toBS(T_SYNC)

def dbgfp(fp,context=None):
  fd=fp.fileno()
  msg="fp=%s[%d]" % (fp, fp.fileno())
  if context is not None:
    msg="%s: %s" % (context, msg)
  debug(msg)
  import os
  os.system("set -x; lsof -p %d | awk '$4 ~ /^%d/ { print }' >&2" % (os.getpid(), fd))

class StreamDaemon:
  ''' A daemon to handle requests from a stream and apply them to a backend
      store.
  '''
  def __init__(self,S,requestFP,replyFP):
    self.S=S
    self.requestFP=requestFP
    self.replyFP=replyFP
    self.jobs={}
    self.jobsLock=BoundedSemaphore(1)
    self.njobs=0
    self.jobsClosing=False
    self.resultsCH=getChannel()
    self.upstreamCH=getChannel()
    self.readerThread=_StreamDaemonReader(self)
    self.readerThread.start()
    self.resultsThread=_StreamDaemonResults(self)
    self.resultsThread.start()

  def close(self):
    with self.jobsLock:
      self.jobsCLosing=True
  def closing(self):
    with self.jobsLock:
      cl=self.jobsCloosing
    return cl

class _StreamDaemonReader(Thread):
  ''' Read requests from the request stream,
      dispatch asynchronously to the backend Store.
  '''
  def __init__(self,daemon):
    Thread.__init__(self)
    self.setName("_StreamDaemonReader")
    self.daemon=daemon
  def run(self):
    debug("RUN _StreamDaemonReader")
    jobs=self.daemon.jobs
    jobsLock=self.daemon.jobsLock
    resultsCH=self.daemon.resultsCH
    upstreamCH=self.daemon.upstreamCH
    S=self.daemon.S
    for n, rqType, arg in decodeStream(self.daemon.requestFP):
      debug("StreamDaemon: rq=(%d, %d, %s)" % (n, rqType, arg))
      with jobsLock:
        assert n not in jobs, "token %d already in jobs" % n
        jobs[n]=rqType
        self.daemon.njobs+=1
      if rqType == T_STORE:
        S.store_a(arg,n,resultsCH)
      elif rqType == T_FETCH:
        S.fetch_a(arg,n,resultsCH)
      elif rqType == T_HAVEYOU:
        S.haveyou_a(arg,n,resultsCH)
      elif rqType == T_SYNC:
        S.sync_a(n,resultsCH)
      else:
        assert False, "unhandled rqType(%d) for request #%d" % (rqType, n)
    self.daemon.close()
    debug("END _StreamDaemonReader")

def decodeStream(fp):
  ''' Generator that yields (rqTag, rqType, arg) from the request stream.
  '''
  rqTag=fromBSfp(fp)
  while rqTag is not None:
    rqType=fromBSfp(fp)
    if rqType == T_STORE:
      size=fromBSfp(fp)
      assert size >= 0, "negative size(%d) for T_STORE" % size
      if size == 0:
        block=''
      else:
        block=fp.read(size)
        assert len(block) == size
      yield rqTag, rqType, block
    elif rqType == T_FETCH or rqType == T_HAVEYOU:
      hlen=fromBSfp(fp)
      assert hlen > 0, \
             "nonpositive hash length(%d) for rqType=%d" % (hlen, rqType)
      hash=fp.read(hlen)
      assert len(hash) == hlen, \
             "short read(%d) for rqType=%d, expected %d bytes" % (len(hash), rqType, hlen)
      yield rqTag, rqType, hash
    elif rqType == T_SYNC:
      yield rqTag, rqType, None
    else:
      assert False, "unsupported request type (%d)" % rqType
    rqTag=fromBSfp(fp)

class _StreamDaemonResults(Thread):
  ''' Collect results from asynchronous Store calls, report upstream.
  '''
  def __init__(self,daemon):
    Thread.__init__(self)
    self.setName("_StreamDaemonResults")
    self.daemon=daemon
  def run(self):
    debug("RUN _StreamDaemonResults")
    jobs=self.daemon.jobs
    jobsLock=self.daemon.jobsLock
    replyFP=self.daemon.replyFP
    for rqTag, result in self.daemon.resultsCH:
      assert (result is None or type(result) is str) and type(rqTag) is int, "result=%s, rqTag=%s"%(result,rqTag)
      debug("StreamDaemon: return result (%s, %s)" % (rqTag, result))
      with jobsLock:
        rqType=jobs[rqTag]
        del jobs[rqTag]
        self.daemon.njobs-=1
      debug("report result upstream: tqTag=%s rqType=%d results=%s" % (rqTag,rqType,result))
      replyFP.write(toBS(rqTag))
      replyFP.write(toBS(rqType))
      if rqType == T_STORE or rqType == T_FETCH:
        replyFP.write(toBS(len(result)))
        replyFP.write(result)
      elif rqType == T_HAVEYOU:
        replyFP.write(toBS(1 if result else 0))
      elif rqType == T_SYNC:
        pass
      else:
        assert False, "unhandled rqType(%d)" % rqType
      replyFP.flush()
      if self.daemon.closing():
        with jobsLock:
          jobsDone=(self.daemon.njobs == 0)
        if jobsDone:
          break
    debug("END _StreamDaemonResults")

def encodeStore(fp,rqTag,block):
  ##if ifdebug(): dbgfp(fp,"encodeStore(rqTag=%d,%d bytes)" % (rqTag,len(block)))
  global enc_STORE
  fp.write(toBS(rqTag))
  fp.write(enc_STORE)
  fp.write(toBS(len(block)))
  fp.write(block)
  fp.flush()

def encodeFetch(fp,rqTag,h):
  ##if ifdebug(): dbgfp(fp,"encodeFetch(rqTag=%d,h=%s" % (rqTag,tohex(h)))
  global enc_FETCH
  fp.write(toBS(rqTag))
  fp.write(enc_FETCH)
  fp.write(toBS(len(h)))
  fp.write(h)
  fp.flush()

def encodeHaveYou(fp,rqTag,h):
  ##if ifdebug(): dbgfp(fp,"encodeHaveYou(rqTag=%d,h=%s" % (rqTag,tohex(h)))
  global enc_HAVEYOU
  fp.write(toBS(rqTag))
  fp.write(enc_HAVEYOU)
  fp.write(toBS(len(h)))
  fp.write(h)
  fp.flush()

def encodeSync(fp,rqTag):
  ##if ifdebug(): dbgfp(fp,"encodeSync(rqTag=%d" % rqTag)
  global enc_SYNC
  fp.write(toBS(rqTag))
  fp.write(enc_SYNC)
  fp.flush()

class StreamStore(BasicStore):
  ''' A Store connected to a StreamDaemon backend.
  '''
  def __init__(self,requestFP,replyFP):
    BasicStore.__init__(self)
    self.requestFP=requestFP
    self.replyFP=replyFP
    self.pending=JobQueue()
    self.sendLock=BoundedSemaphore(1)
    self.lastBlock=None
    self.lastBlockLock=BoundedSemaphore(1)
    _StreamClientReader(self).start()

  def store_a(self,block,rqTag=None,ch=None):
    debug("StreamStore: store_a(%d bytes)..." % len(block))
    if rqTag is None: rqTag=seq()
    ch=self.pending.enqueue(rqTag,ch)
    with self.sendLock:
      encodeStore(self.requestFP,rqTag,block)
    return ch
  def lastFetch(self,h):
    with self.lastBlockLock:
      LB=self.lastBlock
    if LB is not None:
      Lh, Lblock = LB
      if Lh == h:
        return Lblock
    return None

  def fetch(self,h):
    block=self.lastFetch(h)
    if block is not None:
      return block
    block=BasicStore.fetch(self,h)
    with self.lastBlockLock:
      self.lastBlock=(h,block)
    return block
    
  def fetch_a(self,h,rqTag=None,ch=None):
    debug("StreamStore: fetch_a(%s)..." % tohex(h))
    block=self.lastFetch(h)
    if block is not None:
      return bgReturn((None,Lblock))

    ##if ifdebug(): tb()
    if rqTag is None: rqTag=seq()
    ch=self.pending.enqueue(rqTag,ch)
    with self.sendLock:
      encodeFetch(self.requestFP,rqTag,h)
    return ch
  def haveyou_a(self,h,rqTag=None,ch=None):
    debug("StreamStore: haveyou_a(%s)..." % tohex(h))
    if rqTag is None: rqTag=seq()
    ch=self.pending.enqueue(rqTag,ch)
    with self.sendLock:
      encodeHaveYou(self.requestFP,rqTag,h)
    return ch
  def sync_a(self,rqTag=None,ch=None):
    debug("StreamStore: sync_a()...")
    if rqTag is None: rqTag=seq()
    ch=self.pending.enqueue(rqTag,ch)
    with self.sendLock:
      encodeSync(self.requestFP,rqTag)
    return ch

class _StreamClientReader(Thread):
  def __init__(self,client):
    Thread.__init__(self)
    self.setName("_StreamClientReader")
    self.client=client
  def run(self):
    debug("RUN _StreamClientReader")
    replyFP=self.client.replyFP
    pending=self.client.pending
    debug("_StreamClientReader: replyFP=%s" % replyFP)
    rqTag=fromBSfp(replyFP)
    while rqTag is not None:
      rqType=fromBSfp(replyFP)
      if rqType == T_STORE:
        hlen=fromBSfp(replyFP)
        assert hlen > 0
        h=replyFP.read(hlen)
        assert len(h) == hlen
        pending.dequeue(rqTag,h) # send has instead?
      elif rqType == T_FETCH:
        blen=fromBSfp(replyFP)
        assert blen >= 0
        if blen == 0:
          block=''
        else:
          block=replyFP.read(blen)
          assert len(block) == blen
        pending.dequeue(rqTag,block)
      elif rqType == T_HAVEYOU:
        yesno=bool(fromBSfp(replyFP))
        pending.dequeue(rqTag,yesno)
      elif rqType == T_SYNC:
        pending.dequeue(rqTag,None)
      else:
        assert False, "unhandled reply type %d" % rqType

      # fetch next result
      rqTag=fromBSfp(replyFP)
    self.client.close()
    debug("END _StreamClientReader")
