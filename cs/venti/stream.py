#!/usr/bin/python -tt
#
# Stream protocol for vt stores.
#       - Cameron Simpson <cs@zip.com.au> 06dec2007
#
# TODO: T_SYNC, to wait for pending requests before returning
#

from __future__ import with_statement
from threading import Thread, BoundedSemaphore
from cs.misc import seq, toBS, fromBSfp, debug, ifdebug, tb, warn
from cs.lex import unctrl
from cs.threads import JobQueue, getChannel
from cs.venti import tohex
from cs.venti.store import BasicStore

class RqType(int):
  ''' Debugging wrapper for int, reporting symbolic names of op codes.
  '''
  def __str__(self):
    if self == T_STORE:     s="T_STORE"
    elif self == T_FETCH:   s="T_FETCH"
    elif self == T_HAVEYOU: s="T_HAVEYOU"
    elif self == T_SYNC:    s="T_SYNC"
    elif self == T_QUIT:    s="T_QUIT"
    else:                   s="UNKNOWN"
    return "%s(%d)" % (s, self)

T_STORE=RqType(0)       # block->hash
T_FETCH=RqType(1)       # hash->block
T_HAVEYOU=RqType(2)     # hash->boolean
T_SYNC=RqType(3)        # no args
T_QUIT=RqType(4)        # put to put queues into drain-and-quit mode
# encode tokens once for performance
enc_STORE=toBS(T_STORE)
enc_FETCH=toBS(T_FETCH)
enc_HAVEYOU=toBS(T_HAVEYOU)
enc_SYNC=toBS(T_SYNC)
enc_QUIT=toBS(T_QUIT)

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
  def __init__(self,S,recvRequestFP,sendReplyFP):
    ''' Read Store requests from 'recvRequestFP', apply to the Store 'S',
        report results upstream via 'sendReplyFP'.
    '''
    self.S=S
    self.recvRequestFP=recvRequestFP
    self.sendReplyFP=sendReplyFP
    self.jobs={}
    self.jobsLock=BoundedSemaphore(1)
    self.njobs=0
    self.jobsClosing=False
    self.resultsCH=getChannel()
    self.readerThread=_StreamDaemonRequestReader(self)
    self.resultsThread=_StreamDaemonResultsSender(self)

  def start(self):
    ''' Start the control threads that read from recvRequestFP and write
        to sendReplyFP.
    '''
    self.readerThread.start()
    self.resultsThread.start()

  def join(self):
    ''' Wait for the control threads to terminate.
    '''
    self.readerThread.join()    # wait for requests to cease
    self.resultsThread.join()   # wait for results to drain

class _StreamDaemonRequestReader(Thread):
  ''' Read requests from the request stream,
      dispatch asynchronously to the backend Store.
  '''
  def __init__(self,daemon):
    Thread.__init__(self)
    self.setName("_StreamDaemonRequestReader")
    self.daemon=daemon
  def run(self):
    debug("RUN _StreamDaemonRequestReader")
    jobs=self.daemon.jobs
    jobsLock=self.daemon.jobsLock
    resultsCH=self.daemon.resultsCH
    S=self.daemon.S
    for n, rqType, arg in decodeStream(self.daemon.recvRequestFP):
      if ifdebug():
        if arg is None:
          varg=None
        elif rqType == T_HAVEYOU or rqType == T_FETCH:
          varg=tohex(arg)
        else:
          varg=unctrl(arg)
        warn("StreamDaemon: rq=(%d, %s, %s)" % (n, rqType, varg))
      with jobsLock:
        assert n not in jobs, "token %d already in jobs" % n
        jobs[n]=rqType
        self.daemon.njobs+=1
      if rqType == T_QUIT:
        debug("_StreamDaemonRequestReader: T_QUIT")
        resultsCH.put((n,None))
        break
      if rqType == T_STORE:
        debug("_StreamDaemonRequestReader: T_STORE")
        S.store_a(arg,n,resultsCH)
      elif rqType == T_FETCH:
        debug("_StreamDaemonRequestReader: T_FETCH")
        S.fetch_a(arg,n,resultsCH)
      elif rqType == T_HAVEYOU:
        debug("_StreamDaemonRequestReader: T_HAVEYOU")
        S.haveyou_a(arg,n,resultsCH)
      elif rqType == T_SYNC:
        debug("_StreamDaemonRequestReader: T_SYNC")
        S.sync_a(n,resultsCH)
      else:
        assert False, "unhandled rqType(%s) for request #%d" % (rqType, n)
    debug("END _StreamDaemonRequestReader")

def decodeStream(fp):
  ''' Generator that yields (rqTag, rqType, arg) from the request stream.
  '''
  debug("decodeStream: reading first tag...")
  rqTag=fromBSfp(fp)
  while rqTag is not None:
    debug("decodeStream: reading rqType...")
    rqType=RqType(fromBSfp(fp))
    debug("decodeStream: read request: rqTag=%d, rqType=%s" % (rqTag, rqType))
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
             "nonpositive hash length(%d) for rqType=%s" % (hlen, rqType)
      hash=fp.read(hlen)
      assert len(hash) == hlen, \
             "short read(%d) for rqType=%s, expected %d bytes" % (len(hash), rqType, hlen)
      yield rqTag, rqType, hash
    elif rqType == T_SYNC or rqType == T_QUIT:
      debug("decodeStream: rqType=%s" % rqType)
      yield rqTag, rqType, None
    else:
      assert False, "unsupported request type (%s)" % rqType
    debug("decodeStream: reading next tag...")
    rqTag=fromBSfp(fp)
  debug("decodeStream: tag was None, exiting generator")

class _StreamDaemonResultsSender(Thread):
  ''' Collect results from asynchronous Store calls, report upstream.
  '''
  def __init__(self,daemon):
    Thread.__init__(self)
    self.setName("_StreamDaemonResultsSender")
    self.daemon=daemon
  def run(self):
    debug("RUN _StreamDaemonResultsSender")
    jobs=self.daemon.jobs
    jobsLock=self.daemon.jobsLock
    sendReplyFP=self.daemon.sendReplyFP
    draining=False
    for rqTag, result in self.daemon.resultsCH:
      debug("StreamDaemon: return result (%s, %s)" % (rqTag, result))
      with jobsLock:
        rqType=jobs[rqTag]
      if ifdebug():
        if result is None:
          vresult=None
        elif rqType == T_STORE:
          vresult=tohex(result)
        elif type(result) is str:
          vresult=unctrl(result)
        else:
          vresult=result
        warn("report result upstream: rqTag=%s rqType=%s results=%s" % (rqTag,rqType,vresult))
      sendReplyFP.write(toBS(rqTag))
      sendReplyFP.write(toBS(rqType))
      if rqType == T_QUIT:
        debug("T_QUIT received, draining...")
        assert not draining
        quitTag=rqTag
        draining=True
      elif rqType == T_STORE or rqType == T_FETCH:
        sendReplyFP.write(toBS(len(result)))
        sendReplyFP.write(result)
      elif rqType == T_HAVEYOU:
        sendReplyFP.write(toBS(1 if result else 0))
      elif rqType == T_SYNC:
        pass
      else:
        assert False, "unhandled rqType(%s)" % rqType
      sendReplyFP.flush()
      debug("_StreamDaemonResultsSender: dequeue job %d" % rqTag)
      with jobsLock:
        del jobs[rqTag]
        self.daemon.njobs-=1
      if draining:
        debug("draining: %d jobs still in play" % self.daemon.njobs)
        with jobsLock:
          if self.daemon.njobs == 0:
            break
          jobTags=jobs.keys()
          jobTags.sort()
          for tag in jobTags:
            debug("  jobs[%d]=%s" % (tag, jobs[tag]))
    debug("END _StreamDaemonResultsSender")

def encodeStore(fp,rqTag,block):
  ''' Write store(block) to stream.
  '''
  debug(fp,"encodeStore(rqTag=%d,%d bytes)" % (rqTag,len(block)))
  global enc_STORE
  fp.write(toBS(rqTag))
  fp.write(enc_STORE)
  fp.write(toBS(len(block)))
  fp.write(block)
  fp.flush()

def encodeFetch(fp,rqTag,h):
  ''' Write fetch(h) to stream.
  '''
  debug(fp,"encodeFetch(rqTag=%d,h=%s" % (rqTag,tohex(h)))
  global enc_FETCH
  fp.write(toBS(rqTag))
  fp.write(enc_FETCH)
  fp.write(toBS(len(h)))
  fp.write(h)
  fp.flush()

def encodeHaveYou(fp,rqTag,h):
  ''' Write haveyou(h) to stream.
  '''
  debug(fp,"encodeHaveYou(rqTag=%d,h=%s" % (rqTag,tohex(h)))
  global enc_HAVEYOU
  fp.write(toBS(rqTag))
  fp.write(enc_HAVEYOU)
  fp.write(toBS(len(h)))
  fp.write(h)
  fp.flush()

def encodeSync(fp,rqTag):
  ''' Write sync() to stream.
  '''
  debug(fp,"encodeSync(rqTag=%d)" % rqTag)
  global enc_SYNC
  fp.write(toBS(rqTag))
  fp.write(enc_SYNC)
  fp.flush()

def encodeQuit(fp,rqTag):
  ''' Write T_QUIT to stream.
  '''
  debug(fp,"encodeQuit(rqTag=%d" % rqTag)
  global enc_QUIT
  fp.write(toBS(rqTag))
  fp.write(enc_QUIT)
  fp.flush()

class StreamStore(BasicStore):
  ''' A Store connected to a StreamDaemon backend.
  '''
  def __init__(self,name,sendRequestFP,recvReplyFP):
    ''' Connect to the StreamDaemon via sendRequestFP and recvReplyFP.
    '''
    BasicStore.__init__(self,"StreamStore:%s"%name)
    self.sendRequestFP=sendRequestFP
    self.recvReplyFP=recvReplyFP
    self.pending=JobQueue()
    self.sendLock=BoundedSemaphore(1)
    self.client=_StreamClientReader(self)
    self.client.start()

  def close(self):
    ''' Close the StreamStore.
    '''
    debug("StreamStore.close: close()...")
    if self.closing:
      debug("StreamStore.close: already closed, doing nothing")
    else:
      self.closing=True
      rqTag=seq()
      ch=self.pending.enqueue(rqTag)
      debug("StreamStore.close: queued rqTag=%s, got channel=%s" % (rqTag, ch))
      with self.sendLock:
        debug("StreamStore.close: sending T_QUIT...")
        encodeQuit(self.sendRequestFP,rqTag)
        debug("StreamStore.close: sent T_QUIT")
      x=ch.get()
      debug("StreamStore.close: got result from channel: %s" % (x,))

  def store_a(self,block,rqTag=None,ch=None):
    debug("StreamStore: store_a(%d bytes)..." % len(block))
    if rqTag is None: rqTag=seq()
    ch=self.pending.enqueue(rqTag,ch)
    with self.sendLock:
      encodeStore(self.sendRequestFP,rqTag,block)
    return ch
  def fetch_a(self,h,rqTag=None,ch=None):
    debug("StreamStore: fetch_a(%s)..." % tohex(h))
    ##if ifdebug(): tb()
    if rqTag is None: rqTag=seq()
    ch=self.pending.enqueue(rqTag,ch)
    block=self.lastFetch(h)
    if block is not None:
      ch.put((None,block))
    else:
      with self.sendLock:
        encodeFetch(self.sendRequestFP,rqTag,h)
    return ch
  def haveyou_a(self,h,rqTag=None,ch=None):
    debug("%s: haveyou_a(%s)..." % (self,tohex(h)))
    ##tb()
    if rqTag is None: rqTag=seq()
    ch=self.pending.enqueue(rqTag,ch)
    with self.sendLock:
      encodeHaveYou(self.sendRequestFP,rqTag,h)
    return ch
  def sync_a(self,rqTag=None,ch=None):
    debug("StreamStore: sync_a()...")
    if rqTag is None: rqTag=seq()
    ch=self.pending.enqueue(rqTag,ch)
    with self.sendLock:
      encodeSync(self.sendRequestFP,rqTag)
    return ch

class _StreamClientReader(Thread):
  ''' Class to read from the StreamDaemon's responseFP and report to
      asynchronous callers.
  '''
  def __init__(self,daemon):
    Thread.__init__(self)
    self.setName("_StreamClientReader")
    self.daemon=daemon

  def run(self):
    debug("RUN _StreamClientReader")
    recvReplyFP=self.daemon.recvReplyFP
    pending=self.daemon.pending
    debug("_StreamClientReader: recvReplyFP=%s" % recvReplyFP)
    rqTag=fromBSfp(recvReplyFP)
    while rqTag is not None:
      rqType=fromBSfp(recvReplyFP)
      debug("_StreamClientReader: result header: tag=%d, type=%s" % (rqTag, rqType))
      if rqType == T_QUIT:
        debug("received T_QUIT from daemon")
        pending.dequeue(rqTag,None)
        debug("dequeued T_QUIT from daemon")
        break
      if rqType == T_STORE:
        hlen=fromBSfp(recvReplyFP)
        assert hlen > 0
        h=recvReplyFP.read(hlen)
        assert len(h) == hlen
        pending.dequeue(rqTag,h) # send has instead?
      elif rqType == T_FETCH:
        blen=fromBSfp(recvReplyFP)
        assert blen >= 0
        if blen == 0:
          block=''
        else:
          block=recvReplyFP.read(blen)
          assert len(block) == blen
        pending.dequeue(rqTag,block)
      elif rqType == T_HAVEYOU:
        yesno=bool(fromBSfp(recvReplyFP))
        pending.dequeue(rqTag,yesno)
      elif rqType == T_SYNC:
        pending.dequeue(rqTag,None)
      else:
        assert False, "unhandled reply type %s" % rqType

      # fetch next result
      rqTag=fromBSfp(recvReplyFP)

    debug("END _StreamClientReader")
