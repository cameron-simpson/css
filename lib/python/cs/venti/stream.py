#!/usr/bin/python
#
# Stream protocol for vt stores.
#       - Cameron Simpson <cs@zip.com.au> 06dec2007
#
# TODO: T_SYNC, to wait for pending requests before returning
#

from __future__ import with_statement
from threading import Lock
from threading import Thread
from Queue import Queue
import sys
if sys.hexversion < 0x02060000: from sets import Set as set
from cs.misc import seq
from cs.logutils import Pfx, info, debug, warning
from cs.serialise import toBS, fromBSfp
from cs.lex import unctrl
from cs.threads import Q1, IterableQueue
from cs.lex import hexify
from .store import BasicStore

class RqType(int):
  ''' Debugging wrapper for int, reporting symbolic names of op codes.
  '''
  def __str__(self):
    if self == T_ADD:
      s = "T_ADD"
    elif self == T_GET:
      s = "T_GET"
    elif self == T_CONTAINS:
      s = "T_CONTAINS"
    else:
      s = "UNKNOWN"
    return "%s(%d)" % (s, self)

T_ADD = RqType(0)       # block->hash
T_GET = RqType(1)       # hash->block
T_CONTAINS = RqType(2)     # hash->boolean

# encode tokens once for performance
enc_STORE = toBS(T_ADD)
env_GET = toBS(T_GET)
enc_CONTAINS = toBS(T_CONTAINS)

def encodeAdd(block):
  ''' Accept a block to be added, return the request tag and the request packet.
  '''
  assert len(block) > 0
  tag = seq()
  return tag, toBS(tag) + enc_STORE + toBS(len(block)) + block

def encodeGet(rqTag, h):
  ''' Accept a hash to be fetched, return the request tag and the request packet.
  '''
  tag = seq()
  return tag, toBS(tag) + env_GET + toBS(len(h)) + h

def encodeContains(rqTag, h):
  ''' Accept a hash to check for, return the request tag and the request packet.
  '''
  tag = seq()
  return tag, toBS(tag) + enc_CONTAINS + toBS(len(h)) + h

def encodeAddResult(tag, h):
  return toBS(tag) + enc_STORE + toBS(len(h)) + h

def encodeGetResult(tag, block):
  assert len(block) > 0
  if block is None:
    return toBS(tag) + enc_GET + toBS(0)
  return toBS(tag) + enc_GET + toBS(len(block)) + block

def encodeContainsResult(tag, yesno):
  return toBS(tag) + enc_CONTAINS + toBS(1 if yesno else 0)

def decodeRequestStream(fp):
  ''' Generator that yields (rqTag, rqType, info) from the request stream.
  '''
  with Pfx("decodeRequestStream(%s)", fp):
    while True:
      rqTag = fromBSfp(fp)
      if rqTag is None:
        # end of stream
        break
      rqType = RqType(fromBSfp(fp))
      if rqType == T_ADD:
        size = fromBSfp(fp)
        assert size >= 0, "negative size(%d) for T_ADD" % size
        if size == 0:
          block = None
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
      else:
        assert False, "unsupported request type (%s)" % (rqType,)

def decodeResultStream(self):
  ''' Generator that yields (rqTag, rqType, result) from the result stream.
  '''
  with Pfx("decodeResultStream(%s)", fp):
    while True:
      rqTag = fromBSfp(fp)
      if rqTag is None:
        break
      rqType = fromBSfp(fp)
      if rqType == T_ADD:
        hlen = fromBSfp(fp)
        assert hlen > 0
        h = fp.read(hlen)
        assert len(h) == hlen, "read %d bytes, expected %d" \
                                 % (len(h), hlen)
        yield rqTag, rqType, h
      elif rqType == T_GET:
        blen = fromBSfp(fp)
        assert blen >= 0
        if blen == 0:
          block = None
        else:
          block = fp.read(blen)
          assert len(block) == blen
        yield rqTag, rqType, block
      elif rqType == T_CONTAINS:
        yesno = bool(fromBSfp(fp))
        yield rqTag, rqType, yesno
      else:
        assert False, "unhandled reply type %s" % rqType

class StreamDaemon(object):
  ''' A daemon to handle requests from a stream and apply them to a backend
      store.
  '''
  def __init__(self, S, recvRequestFP, sendResultsFP, inBoundCapacity=None):
    ''' Read Store requests from `recvRequestFP`, apply to the Store `S`,
        report results upstream via `sendResultsFP`.
    '''
    if inBoundCapacity is None:
      inBoundCapacity = 128
    self.S = S
    self._streamQ = Later(128, inboundCapacity=inboundCapacity)
    self.recvRequestFP = recvRequestFP
    self.sendResultsFP = sendResultsFP
    self._resultsQ = IterableQueue(128)
    self.readerThread = Thread(target=self._process_request_stream,
                               name="%s._process_request_stream" % (self,))
    self.resultsThread = Thread(target=self._result_sender,
                                name="%s._process_results" % (self,))
    self.readerThread.start()
    self.resultsThread.start()

  def _process_request_stream(self, fp):
    SQ = self._streamQ
    with Pfx("%s._process_requests", self):
      for rqTag, rqType, rqData in decodeRequestStream(fp):
        # submit request - will
        SQ.defer(self._process_request, rqTag, rqType, rqData)

  def _process_request(self, rqTag, rqType, rqData):
    if rqType == T_ADD:
      result = S.add(rqData)
    elif rqType == T_GET:
      result = S.get(rqData)
    elif rqType == T_CONTAINS:
      result = rqData in S
    self._resultsQ.put(rqTag, rqType, result)

  def _process_results(self, Q):
    for rqTag, rqType, result in Q:
      if rqType == T_ADD:
        packet = encodeAddResult(rqTag, result)
      elif rqType == T_GET:
        packet = encodeGetResult(rqTag, result)
      elif rqType == T_CONTAINS:
        packet = encodeContainsResult(rqTag, result)
      else:
        assert "unimplemented result type %s" % (rqType,)
      self.sendResultsFP.write(packet)
      if self.Q.empty():
        self.sendResultsFP.flush()

  def join(self):
    ''' Wait for the control threads to terminate.
    '''
    self.readerThread.join()    # wait for requests to cease
    self.resultsThread.join()   # wait for results to drain

class StreamStore(BasicStore):
  ''' A Store connected to a StreamDaemon backend.
  '''
  def __init__(self, name, sendRequestsFP, recvResultsFP):
    ''' Connect to a StreamDaemon via sendRequestsFP and recvResultsFP.
    '''
    BasicStore.__init__(self, "StreamStore:%s"%name)
    self.sendRequestsFP = sendRequestsFP
    self.recvResultsFP = recvResultsFP
    self._requestQ = IterableQueue(128)
    self._pendingLock = Lock()
    self._pending = {}
    self.writer = Thread(target=self._process_requests)
    self.writer.start()
    self.reader = Thread(target=self._process_results_stream)
    self.reader.start()

  def add(self, block):
    assert len(block) > 0
    tag, packet = encodeAdd(block)
    return self._sendPacket(tag, packet).get()

  def get(self, h, default=None):
    tag, packet = encodeGet(h)
    block = self._sendPacket(tag, packet).get()
    if block is None:
      return default
    return block

  def contains(self, h):
    tag, packet = encodeContains(h)
    return self._sendPacket(tag, packet).get()

  def _sendPacket(self, tag, packet):
    retQ = Q1()
    self._requestQ.put(tag, packet, retQ)
    return retQ

  def _process_requests(self):
    for tag, packet, retQ in self._requestQ:
      with self._pendingLock:
        assert tag not in self._pending
        self._pending[tag] = retQ
      self.sendRequestsFP.write(packet)
      if self._requestQ.empty():
        self.sendRequestsFP.flush()

  def _process_results_stream(self):
    for rqTag, rqType, result in decodeRequestStream(self.recvResultsFP):
      with self._pendingLock:
        self._pending[tag].put(result)
        del self._pending[tag]

  def flush(self):
    with self.__sendLock:
      self.sendRequestsFP.flush()

  def shutdown(self):
    ''' Close the StreamStore.
    '''
    debug("%s.shutdown...", self)
    self._requestQ.close()
    self.writer.join()
    self.writer = None
    self.sendRequestsFP.close()
    self.sendRequestsFP = None

    self.reader.join()
    self.reader = None
    self.recvResultsFP.close()
    self.recvReqestsFP = None

    BasicStore.shutdown(self)
