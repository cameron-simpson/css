#!/usr/bin/python -tt
#
# Stream protocol for vt stores.
#       - Cameron Simpson <cs@zip.com.au> 06dec2007
#
# TODO: T_SYNC, to wait for pending requests before returning
#

from __future__ import with_statement
from thread import allocate_lock
from threading import Thread
from Queue import Queue
from cs.misc import seq, debug, isdebug, tb, warn, progress
from cs.serialise import toBS, fromBSfp
from cs.lex import unctrl
from cs.threads import Q1
from cs.venti import tohex
from cs.venti.store import BasicStore

class RqType(int):
  ''' Debugging wrapper for int, reporting symbolic names of op codes.
  '''
  def __str__(self):
    if self == T_STORE:      s="T_STORE"
    elif self == T_GET:      s="T_GET"
    elif self == T_CONTAINS: s="T_CONTAINS"
    elif self == T_SYNC:     s="T_SYNC"
    elif self == T_QUIT:     s="T_QUIT"
    else:                    s="UNKNOWN"
    return "%s(%d)" % (s, self)

T_STORE=RqType(0)       # block->hash
T_GET=RqType(1)       # hash->block
T_CONTAINS=RqType(2)     # hash->boolean
T_SYNC=RqType(3)        # no args
T_QUIT=RqType(4)        # put to put queues into drain-and-quit mode
# encode tokens once for performance
enc_STORE=toBS(T_STORE)
env_GET=toBS(T_GET)
enc_CONTAINS=toBS(T_CONTAINS)
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
    self.jobsLock=allocate_lock()
    self.njobs=0
    self.jobsClosing=False
    self.resultsCH=Queue(size=256)
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
      if isdebug:
        if arg is None:
          varg=None
        elif rqType == T_CONTAINS or rqType == T_GET:
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
        S.store_ch(arg,resultsCH,n)
      elif rqType == T_GET:
        debug("_StreamDaemonRequestReader: T_GET")
        S.fetch_ch(arg,resultsCH,n)
      elif rqType == T_CONTAINS:
        debug("_StreamDaemonRequestReader: T_CONTAINS")
        S.haveyou_ch(arg,resultsCH,n)
      elif rqType == T_SYNC:
        debug("_StreamDaemonRequestReader: T_SYNC")
        S.sync_ch(resultsCH,n)
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
    elif rqType == T_GET or rqType == T_CONTAINS:
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
    written=0
    while True:
      if self.daemon.resultsCH.empty():
        progress("flush sendReplyFP after %d requests" % written)
        sendReplyFP.flush()
        written=0
      rqTag, result = self.daemon.resultsCH.get()
      debug("StreamDaemon: return result (%s, %s)" % (rqTag, result))
      with jobsLock:
        rqType=jobs[rqTag]
      if isdebug:
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
      elif rqType == T_STORE or rqType == T_GET:
        sendReplyFP.write(toBS(len(result)))
        sendReplyFP.write(result)
      elif rqType == T_CONTAINS:
        sendReplyFP.write(toBS(1 if result else 0))
      elif rqType == T_SYNC:
        pass
      else:
        assert False, "unhandled rqType(%s)" % rqType
      written+=1
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
    progress("flush sendReplyFP")
    sendReplyFP.flush()
    debug("END _StreamDaemonResultsSender")

def encodeStore(fp,rqTag,block):
  ''' Write store(block) to stream.
      Does not .flush() the stream.
  '''
  debug(fp,"encodeStore(rqTag=%d,%d bytes)" % (rqTag,len(block)))
  global enc_STORE
  fp.write(toBS(rqTag))
  fp.write(enc_STORE)
  fp.write(toBS(len(block)))
  fp.write(block)

def encodeFetch(fp,rqTag,h):
  ''' Write fetch(h) to stream.
      Does not .flush() the stream.
  '''
  debug(fp,"encodeFetch(rqTag=%d,h=%s" % (rqTag,tohex(h)))
  global env_GET
  fp.write(toBS(rqTag))
  fp.write(env_GET)
  fp.write(toBS(len(h)))
  fp.write(h)

def encodeHaveYou(fp,rqTag,h):
  ''' Write haveyou(h) to stream.
      Does not .flush() the stream.
  '''
  debug(fp,"encodeHaveYou(rqTag=%d,h=%s" % (rqTag,tohex(h)))
  global enc_CONTAINS
  fp.write(toBS(rqTag))
  fp.write(enc_CONTAINS)
  fp.write(toBS(len(h)))
  fp.write(h)

def encodeSync(fp,rqTag):
  ''' Write sync() to stream.
      Does not .flush() the stream.
  '''
  debug(fp,"encodeSync(rqTag=%d)" % rqTag)
  global enc_SYNC
  fp.write(toBS(rqTag))
  fp.write(enc_SYNC)

def encodeQuit(fp,rqTag):
  ''' Write T_QUIT to stream.
      Does not .flush() the stream.
  '''
  debug(fp,"encodeQuit(rqTag=%d" % rqTag)
  global enc_QUIT
  fp.write(toBS(rqTag))
  fp.write(enc_QUIT)

class StreamStore(BasicStore):
  ''' A Store connected to a StreamDaemon backend.
  '''
  def __init__(self,name,sendRequestFP,recvReplyFP):
    ''' Connect to a StreamDaemon via sendRequestFP and recvReplyFP.
    '''
    BasicStore.__init__(self, "StreamStore:%s"%name)
    self.sendRequestFP=sendRequestFP
    self.recvReplyFP=recvReplyFP
    self.__sendLock=allocate_lock()
    self.__tagmapLock=allocate_lock()
    self.__tagmap={}
    self.reader = Thread(target=self.readReplies)
    self.reader.start()

  def flush(self):
    with self.__sendLock:
      self.sendRequestFP.flush()

  def shutdown(self):
    ''' Close the StreamStore.
    '''
    tag, ch = self._tagch()
    self.__maptag(tag,ch)
    with self.__sendLock:
      if isdebug:
        self.log("sending T_QUIT...","close")
      encodeQuit(self.sendRequestFP,tag)
      if isdebug:
        self.log("sent T_QUIT","close")
    x=ch.get()
    if isdebug:
      self.log("got result from channel: %s" % (x,), "close")
    BasicStore.shutdown(self)

  def _respond(self, tag, result):
    with self.__tagmapLock:
      retQ = self.__tagmap[tag]
      del self.__tagmap[tag]
    retQ.put(result)

  def _sendPacket(self, packet, tag, retQ, noFlush=False):
    with self.__tagmapLock:
      assert tag not in self.__tagmap
      self.__tagmap[tag] = retQ
    with self.__sendLock:
      sendRequestFP.write(packet)
      if not noFlush:
        sendRequestFP.flush()

  def contains(self, h):
    tag, ch = self.contains_bg(data)
    return ch.get()
  def contains_bg(self, h, noFlush=False):
    if ch is None: ch = Q1()
    tag = seq()
    packet = encodeContains(tag, h)
    self._sendPacket(packet, tag, ch, noFlush)
    return tag, ch
  __contains__ = contains

  def get(self, h):
    tag, ch = self.get_bg(data)
    return ch.get()
  def get_bg(self, h, noFlush=False):
    if ch is None: ch = Q1()
    tag = seq()
    packet = encodeGet(tag, h)
    self._sendPacket(packet, tag, ch, noFlush)
    return tag, ch

  def add(self, data, noFlush=False):
    tag, ch = self.add_bg(data, noFlush=noFlush)
    return ch.get()
  def add_bg(self, data, noFlush=False, ch=None):
    if ch is None: ch = Q1()
    tag = seq()
    packet = encodeAdd(tag, data)
    self._sendPacket(packet, tag, ch, noFlush)
    return tag, ch

  def sync(self):
    tag, ch = self.sync_bg()
    return ch.get()
  def sync_bg(self, noFlush=False):
    if ch is None: ch = Q1()
    tag = seq()
    packet = encodeSync(tag)
    self._sendPacket(packet, tag, ch, noFlush)
    return tag, ch

  def readReplies(self):
    debug("RUN _StreamClientReader")
    recvReplyFP=self.recvReplyFP
    respond=self._respond
    debug("_StreamClientReader: recvReplyFP=%s" % (recvReplyFP,))
    rqTag=fromBSfp(recvReplyFP)
    while rqTag is not None:
      rqType=fromBSfp(recvReplyFP)
      debug("_StreamClientReader: result header: tag=%d, type=%s" % (rqTag, rqType))
      if rqType == T_QUIT:
        debug("received T_QUIT from daemon")
        respond(rqTag,None)
        break
      if rqType == T_STORE:
        hlen=fromBSfp(recvReplyFP)
        assert hlen > 0
        h=recvReplyFP.read(hlen)
        assert len(h) == hlen, "%s: read %d bytes, expected %d" \
                                 % (recvReplyFP, len(h), hlen)
        respond(rqTag,h)
        continue
      if rqType == T_GET:
        blen=fromBSfp(recvReplyFP)
        assert blen >= 0
        if blen == 0:
          block=''
        else:
          block=recvReplyFP.read(blen)
          assert len(block) == blen
        respond(rqTag, block)
        continue
      if rqType == T_CONTAINS:
        yesno=bool(fromBSfp(recvReplyFP))
        respond(rqTag, yesno)
        continue
      if rqType == T_SYNC:
        respond(rqTag, None)
        continue
      assert False, "unhandled reply type %s" % rqType

      # fetch next result
      rqTag=fromBSfp(recvReplyFP)

    debug("END _StreamClientReader")
