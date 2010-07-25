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
from cs.misc import seq
from cs.logutils import Pfx, info, debug, warn
from cs.serialise import toBS, fromBSfp
from cs.lex import unctrl
from cs.threads import Q1
from cs.lex import hexify
from cs.venti.store import BasicStore

class RqType(int):
  ''' Debugging wrapper for int, reporting symbolic names of op codes.
  '''
  def __str__(self):
    if self == T_STORE:
      s = "T_STORE"
    elif self == T_GET:
      s = "T_GET"
    elif self == T_CONTAINS:
      s = "T_CONTAINS"
    elif self == T_SYNC:
      s = "T_SYNC"
    elif self == T_QUIT:
      s = "T_QUIT"
    else:
      s = "UNKNOWN"
    return "%s(%d)" % (s, self)

T_STORE = RqType(0)       # block->hash
T_GET = RqType(1)       # hash->block
T_CONTAINS = RqType(2)     # hash->boolean
T_SYNC = RqType(3)        # no args
T_QUIT = RqType(4)        # put to put queues into drain-and-quit mode
# encode tokens once for performance
enc_STORE = toBS(T_STORE)
env_GET = toBS(T_GET)
enc_CONTAINS = toBS(T_CONTAINS)
enc_SYNC = toBS(T_SYNC)
enc_QUIT = toBS(T_QUIT)

def dbgfp(fp, context=None):
  fd = fp.fileno()
  msg = "fp=%s[%d]" % (fp, fp.fileno())
  if context is not None:
    msg = "%s: %s" % (context, msg)
  debug(msg)
  import os
  os.system("set -x; lsof -p %d | awk '$4 ~ /^%d/ { print }' >&2" % (os.getpid(), fd))

def decodeStream(fp):
  ''' Generator that yields (rqTag, rqType, arg) from the request stream.
  '''
  with Pfx("decodeStream(%s)" % (fp,)):
    debug("reading first tag...")
    rqTag = fromBSfp(fp)
    while rqTag is not None:
      debug("reading rqType...")
      rqType = RqType(fromBSfp(fp))
      debug("read request: rqTag=%d, rqType=%s" % (rqTag, rqType))
      if rqType == T_STORE:
        size = fromBSfp(fp)
        assert size >= 0, "negative size(%d) for T_STORE" % size
        if size == 0:
          block = ''
        else:
          block = fp.read(size)
          assert len(block) == size
        yield rqTag, rqType, block
      elif rqType == T_GET or rqType == T_CONTAINS:
        hlen = fromBSfp(fp)
        assert hlen > 0, \
               "nonpositive hash length(%d) for rqType=%s" % (hlen, rqType)
        h = fp.read(hlen)
        assert len(h) == hlen, \
               "short read(%d) for rqType=%s, expected %d bytes" % (len(h), rqType, hlen)
        yield rqTag, rqType, h
      elif rqType == T_SYNC or rqType == T_QUIT:
        debug("rqType=%s" % rqType)
        yield rqTag, rqType, None
      else:
        assert False, "unsupported request type (%s)" % rqType
      debug("reading next tag...")
      rqTag = fromBSfp(fp)
    debug("tag was None, exiting generator")

class StreamDaemon:
  ''' A daemon to handle requests from a stream and apply them to a backend
      store.
  '''
  def __init__(self, S, recvRequestFP, sendReplyFP):
    ''' Read Store requests from 'recvRequestFP', apply to the Store 'S',
        report results upstream via 'sendReplyFP'.
    '''
    self.S = S
    self.recvRequestFP = recvRequestFP
    self.sendReplyFP = sendReplyFP
    self.jobs = {}
    self.jobsLock = allocate_lock()
    self.njobs = 0
    self.jobsClosing = False
    self.resultsCH = Queue(size=256)
    self.readerThread = Thread(target=self._request_reader, name="requestReader")
    self.resultsThread = Thread(target=self._result_sender, name="resultSender")

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

  def _request_reader(self):
    debug("RUN _request_reader")
    jobs = self.jobs
    jobsLock = self.jobsLock
    resultsCH = self.resultsCH
    S = self.S
    for n, rqType, arg in decodeStream(self.recvRequestFP):
      if isdebug:
        if arg is None:
          varg = None
        elif rqType == T_CONTAINS or rqType == T_GET:
          varg = hexify(arg)
        else:
          varg = unctrl(arg)
        warn("StreamDaemon: rq=(%d, %s, %s)" % (n, rqType, varg))
      with jobsLock:
        assert n not in jobs, "token %d already in jobs" % n
        jobs[n] = rqType
        self.njobs += 1
      if rqType == T_QUIT:
        debug("_StreamDaemonRequestReader: T_QUIT")
        resultsCH.put((n, None))
        break
      if rqType == T_STORE:
        debug("_StreamDaemonRequestReader: T_STORE")
        S.store_ch(arg, resultsCH, n)
      elif rqType == T_GET:
        debug("_StreamDaemonRequestReader: T_GET")
        S.get_bg(arg, resultsCH, n)
      elif rqType == T_CONTAINS:
        debug("_StreamDaemonRequestReader: T_CONTAINS")
        S.contains_bg(arg, resultsCH, n)
      elif rqType == T_SYNC:
        debug("_StreamDaemonRequestReader: T_SYNC")
        S.sync_ch(resultsCH, n)
      else:
        assert False, "unhandled rqType(%s) for request #%d" % (rqType, n)
    debug("END _StreamDaemonRequestReader")

  def _result_sender(self):
    ''' Collect results from asynchronous Store calls, report upstream.
    '''
    debug("RUN _StreamDaemonResultsSender")
    jobs = self.jobs
    jobsLock = self.jobsLock
    sendReplyFP = self.sendReplyFP
    draining = False
    written = 0
    while True:
      if self.resultsCH.empty():
        info("flush sendReplyFP after %d requests" % written)
        sendReplyFP.flush()
        written = 0
      rqTag, result = self.resultsCH.get()
      debug("StreamDaemon: return result (%s, %s)" % (rqTag, result))
      with jobsLock:
        rqType = jobs[rqTag]
      if isdebug:
        if result is None:
          vresult = None
        elif rqType == T_STORE:
          vresult = hexify(result)
        elif type(result) is str:
          vresult = unctrl(result)
        else:
          vresult = result
        warn("report result upstream: rqTag=%s rqType=%s results=%s" % (rqTag, rqType, vresult))
      sendReplyFP.write(toBS(rqTag))
      sendReplyFP.write(toBS(rqType))
      if rqType == T_QUIT:
        debug("T_QUIT received, draining...")
        assert not draining
        draining = True
      elif rqType == T_STORE or rqType == T_GET:
        sendReplyFP.write(toBS(len(result)))
        sendReplyFP.write(result)
      elif rqType == T_CONTAINS:
        sendReplyFP.write(toBS(1 if result else 0))
      elif rqType == T_SYNC:
        pass
      else:
        assert False, "unhandled rqType(%s)" % rqType
      written += 1
      debug("_StreamDaemonResultsSender: dequeue job %d" % rqTag)
      with jobsLock:
        del jobs[rqTag]
        self.njobs -= 1
      if draining:
        debug("draining: %d jobs still in play" % self.njobs)
        with jobsLock:
          if self.njobs == 0:
            break
          jobTags = jobs.keys()
          jobTags.sort()
          for tag in jobTags:
            debug("  jobs[%d]=%s" % (tag, jobs[tag]))
    info("flush sendReplyFP")
    sendReplyFP.flush()
    debug("END _StreamDaemonResultsSender")

def encodeAdd(rqTag, block):
  ''' Write add(block) to stream.
      Does not .flush() the stream.
  '''
  debug("encodeStore(rqTag=%d, %d bytes)" % (rqTag, len(block)))
  return toBS(rqTag) + enc_STORE + toBS(len(block)) + block

def encodeGet(rqTag, h):
  ''' Write get(h) to stream.
      Does not .flush() the stream.
  '''
  debug("encodeGet(rqTag=%d, h=%s" % (rqTag, hexify(h)))
  return toBS(rqTag) + env_GET + toBS(len(h)) + h

def encodeContains(rqTag, h):
  ''' Write contains(h) to stream.
      Does not .flush() the stream.
  '''
  debug("encodeContains(rqTag=%d, h=%s" % (rqTag, hexify(h)))
  return toBS(rqTag) + enc_CONTAINS + toBS(len(h)) + h

def encodeSync(rqTag):
  ''' Write sync() to stream.
      Does not .flush() the stream.
  '''
  debug("encodeSync(rqTag=%d)" % rqTag)
  return toBS(rqTag) + enc_SYNC

def encodeQuit(rqTag):
  ''' Write T_QUIT to stream.
      Does not .flush() the stream.
  '''
  debug("encodeQuit(rqTag=%d" % rqTag)
  return toBS(rqTag) + enc_QUIT

class StreamStore(BasicStore):
  ''' A Store connected to a StreamDaemon backend.
  '''
  def __init__(self, name, sendRequestFP, recvReplyFP):
    ''' Connect to a StreamDaemon via sendRequestFP and recvReplyFP.
    '''
    BasicStore.__init__(self, "StreamStore:%s"%name)
    self.sendRequestFP = sendRequestFP
    self.recvReplyFP = recvReplyFP
    self.__sendLock = allocate_lock()
    self.__tagmapLock = allocate_lock()
    self.__tagmap = {}
    self.reader = Thread(target=self.readReplies)
    self.reader.start()

  def flush(self):
    with self.__sendLock:
      self.sendRequestFP.flush()

  def shutdown(self):
    ''' Close the StreamStore.
    '''
    tag, ch = self._tagch()
    self.__maptag(tag, ch)
    with self.__sendLock:
      if isdebug:
        info("sending T_QUIT...", "close")
      packet = encodeQuit(tag)
      self.sendRequestFP.write(packet)
      self.sendRequestFP.flush()
      if isdebug:
        info("sent T_QUIT", "close")
    x = ch.get()
    if isdebug:
      info("got result from channel: %s" % (x,), "close")
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
      self.sendRequestFP.write(packet)
      if not noFlush:
        self.sendRequestFP.flush()

  def contains(self, h):
    _, ch = self.contains_bg(h)
    return ch.get()
  def contains_bg(self, h, ch=None, noFlush=False):
    if ch is None:
      ch = Q1()
    tag = seq()
    packet = encodeContains(tag, h)
    self._sendPacket(packet, tag, ch, noFlush)
    return tag, ch
  __contains__ = contains

  def get(self, h):
    _, ch = self.get_bg(h)
    return ch.get()
  def get_bg(self, h, ch=None, noFlush=False):
    if ch is None:
      ch = Q1()
    tag = seq()
    packet = encodeGet(tag, h)
    self._sendPacket(packet, tag, ch, noFlush)
    return tag, ch

  def add(self, data, noFlush=False):
    _, ch = self.add_bg(data, noFlush=noFlush)
    return ch.get()
  def add_bg(self, data, noFlush=False, ch=None):
    if ch is None:
      ch = Q1()
    tag = seq()
    packet = encodeAdd(tag, data)
    self._sendPacket(packet, tag, ch, noFlush)
    return tag, ch

  def sync(self):
    _, ch = self.sync_bg()
    return ch.get()
  def sync_bg(self, ch=None, noFlush=False):
    if ch is None:
      ch = Q1()
    tag = seq()
    packet = encodeSync(tag)
    self._sendPacket(packet, tag, ch, noFlush)
    return tag, ch

  def readReplies(self):
    debug("RUN _StreamClientReader")
    recvReplyFP = self.recvReplyFP
    respond = self._respond
    debug("_StreamClientReader: recvReplyFP=%s" % (recvReplyFP,))
    rqTag = fromBSfp(recvReplyFP)
    while rqTag is not None:
      rqType = fromBSfp(recvReplyFP)
      debug("_StreamClientReader: result header: tag=%d, type=%s" % (rqTag, rqType))
      if rqType == T_QUIT:
        debug("received T_QUIT from daemon")
        respond(rqTag, None)
        break
      if rqType == T_STORE:
        hlen = fromBSfp(recvReplyFP)
        assert hlen > 0
        h = recvReplyFP.read(hlen)
        assert len(h) == hlen, "%s: read %d bytes, expected %d" \
                                 % (recvReplyFP, len(h), hlen)
        respond(rqTag, h)
        continue
      if rqType == T_GET:
        blen = fromBSfp(recvReplyFP)
        assert blen >= 0
        if blen == 0:
          block = ''
        else:
          block = recvReplyFP.read(blen)
          assert len(block) == blen
        respond(rqTag, block)
        continue
      if rqType == T_CONTAINS:
        yesno = bool(fromBSfp(recvReplyFP))
        respond(rqTag, yesno)
        continue
      if rqType == T_SYNC:
        respond(rqTag, None)
        continue
      assert False, "unhandled reply type %s" % rqType

      # fetch next result
      rqTag = fromBSfp(recvReplyFP)

    debug("END _StreamClientReader")
