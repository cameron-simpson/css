#!/usr/bin/python -tt
#
# Stream protocol for vt stores.
#       - Cameron Simpson <cs@zip.com.au> 06dec2007
#

from __future__ import with_statement
from threading import Thread, BoundedSemaphore
from cs.misc import seq, toBS, fromBSfp
from cs.threads import getChannel, returnChannel, JobQueue
from cs.venti.store import BasicStore

T_STORE=0       # block->hash
T_FETCH=1       # hash->block
T_HAVEYOU=2     # hash->boolean
# encode tokens once for performance
enc_STORE=toBS(T_STORE)
enc_FETCH=toBS(T_FETCH)
enc_HAVEYOU=toBS(T_HAVEYOU)

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
    self.resultsCH=getChannel()
    self.upstreamCH=getChannel()
    self.readerThread=_StreamDaemonReader(self)
    self.readerThread.start()
    self.resultsThread=_StreamDaemonResults(self)
    self.resultsThread.start()

class _StreamDaemonReader(Thread):
  ''' Read requests from the request stream,
      dispatch asynchronously to the backend Store.
  '''
  def __init__(self,daemon):
    Thread.__init__(self)
    self.daemon=daemon
  def run(self):
    jobs=self.daemon.jobs
    jobsLock=self.daemon.jobsLock
    resultsCH=self.daemon.resultsCH
    upstreamCH=self.daemon.upstreamCH
    S=self.daemon.S
    for n, rqType, arg in decodeStream(self.daemon.requestFP):
      with jobsLock:
        assert n not in jobs, "token %d already in jobs" % n
        jobs[n]=rqType
      if rqType == T_STORE:
        S.store_a(n,resultsCH,arg)
      elif rqType == T_FETCH:
        S.fetch_a(n,resultsCH,arg)
      elif rqType == T_HAVEYOU:
        S.haveyou(n,resultsCH,arg)
      else:
        assert False, "unhandled rqType(%d) for request #%d" % (rqType, n)

class _StreamDaemonResults(Thread):
  ''' Collect results from asynchronous Store calls, report upstream.
  '''
  def __init__(self,daemon):
    Thread.__init__(self)
    self.daemon=daemon
  def run(self):
    jobs=self.daemon.jobs
    jobsLock=self.daemon.jobsLock
    replyFP=self.daemon.replyFP
    for n, result in self.daemon.resultsCH:
      with jobsLock:
        rqType=jobs[n]
        del jobs[n]
      debug("report result upstream: rqType=%d" % rqType)
      replyFP.write(toBS(n))
      replyFP.write(toBS(rqType))
      if rqType == T_STORE:
        pass
      elif rqType == T_FETCH:
        replyFP.write(toBS(len(result)))
        replyFP.write(result)
      elif rqType == T_HAVEYOU:
        replyFP.write(toBS(1 if result else 0))

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
      yield rqType, block
    elif rqType == T_FETCH or rqType == T_HAVEYOU:
      hlen=fromBSfp(fp)
      assert hlen > 0, \
             "nonpositive hash length(%d) for rqType=%d" % (hlen, rqType)
      hash=fp.read(hlen)
      assert len(hash) == hlen, \
             "short read(%d) for rqType=%d, expected %d bytes" % (len(hash), rqType, hlen)
      yield rqTag, rqType, hash
    else:
      assert False, "unsupported request type (%d)" % rqType
    rqTag=fromBSfp(fp)

def encodeStore(fp,n,block):
  global enc_STORE
  fp.write(toBS(n))
  fp.write(enc_STORE)
  fp.write(toBS(len(block)))
  fp.write(block)
  return n

def encodeFetch(fp,n,hash):
  global enc_FETCH
  fp.write(toBS(n))
  fp.write(enc_FETCH)
  fp.write(toBS(len(hash)))
  fp.write(hash)
  return n

def encodeHaveYou(fp,n,hash):
  global enc_HAVEYOU
  fp.write(toBS(n))
  fp.write(env_HAVEYOU)
  fp.write(toBS(len(hash)))
  fp.write(hash)
  return n

class StreamStore(BasicStore):
  ''' A Store connected to a StreamDaemon backend.
  '''
  def __init__(self,requestFP,replyFP):
    self.requestFP=requestFP
    self.replyFP=replyFP
    self.pending=JobQueue()
    self.sendLock=BoundedSemaphore(1)
    self.recvLock=BoundedSemaphore(1)
    _StreamClientReader(self).start()
  def store_a(self,block):
    n=seq()
    ch=self.pending.enqueue(n)
    with self.sendLock:
      encodeStore(self.requestFP,n,block)
    return ch
  def fetch_a(self,h):
    n=seq()
    ch=self.pending.enqueue(n)
    with self.sendLock:
      encodeFetch(self.requestFP,n,h)
    return ch
  def haveyou_a(self,h):
    n=seq()
    ch=self.pending.enqueue(n)
    with self.sendLock:
      encodeHaveYou(self.requestFP,n,h)
    return ch

class _StreamClientReader:
  def __init__(self,client):
    self.client=client
  def run(self):
    replyFP=self.client.replyFP
    pending=self.client.pending
    n=fromBSfp(replyFP)
    while n is not None:
      rqType=fromBSfp(replyFP)
      if rqType == T_STORE:
        pending.dequeue(n,None) # send has instead?
      elif rqType == T_FETCH:
        blen=fromBSfp(replyFP)
        assert blen >= 0
        if blen == 0:
          block=''
        else:
          block=replyFP.read(blen)
          assert len(block) == blen
        pending.dequeue(n,block)
      elif rqType == T_HAVEYOU:
        yesno=bool(fromBSfp(replyFP))
        pending.dequeue(yesno)
      else:
        assert False, "unhandled reply type %d" % rqType

      # fetch next result
      n=fromBSfp(replyFP)
