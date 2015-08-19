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
import sys
from cs.py3 import Queue
from cs.seq import seq
from cs.inttypes import Enum
from cs.logutils import Pfx, info, debug, warning
from cs.serialise import put_bs, read_bs
from cs.lex import unctrl
from cs.queues import IterableQueue
from cs.threads import Q1
from cs.lex import hexify
from .store import BasicStore

RqType = Enum('T_ADD', 'T_GET', 'T_CONTAINS')
T_ADD = RqType(0)       # data->hash
T_GET = RqType(1)       # hash->data
T_CONTAINS = RqType(2)     # hash->boolean

# encode tokens once for performance
enc_STORE = put_bs(T_ADD)
enc_GET = put_bs(T_GET)
enc_CONTAINS = put_bs(T_CONTAINS)

def encodeAdd(data):
  ''' Accept a data block to be added, return the request tag and the request packet.
  '''
  if len(data) < 1:
    raise ValueError("expected non-empty data block")
  tag = seq()
  return tag, put_bs(tag) + enc_STORE + put_bs(len(data)) + data

def encodeGet(rqTag, h):
  ''' Accept a hash to be fetched, return the request tag and the request packet.
  '''
  tag = seq()
  return tag, put_bs(tag) + enc_GET + put_bs(len(h)) + h

def encodeContains(rqTag, h):
  ''' Accept a hash to check for, return the request tag and the request packet.
  '''
  tag = seq()
  return tag, put_bs(tag) + enc_CONTAINS + put_bs(len(h)) + h

def encodeAddResult(tag, h):
  return put_bs(tag) + enc_STORE + put_bs(len(h)) + h

def encodeGetResult(tag, data):
  if len(data) < 1:
    raise ValueError("expected non-empty data block")
  if data is None:
    return put_bs(tag) + enc_GET + put_bs(0)
  return put_bs(tag) + enc_GET + put_bs(len(data)) + data

def encodeContainsResult(tag, yesno):
  return put_bs(tag) + enc_CONTAINS + put_bs(1 if yesno else 0)

def decodeRequestStream(fp):
  ''' Generator that yields (rqTag, rqType, info) from the request stream.
  '''
  with Pfx("decodeRequestStream(%s)", fp):
    while True:
      rqTag = read_bs(fp)
      if rqTag is None:
        # end of stream
        break
      with Pfx(str(rqTag)):
        rqType = RqType(read_bs(fp))
        if rqType == T_ADD:
          size = read_bs(fp)
          if size == 0:
            data = None
          else:
            data = fp.read(size)
            if len(data) != size:
              raise ValueError("expected %d data bytes but got %d: %r", size, len(data), data)
          yield rqTag, rqType, data
        elif rqType == T_GET or rqType == T_CONTAINS:
          hlen = read_bs(fp)
          if hlen < 1:
            raise ValueError("expected hash length >= 1, but was told %d", hlen)
          h = fp.read(hlen)
          if len(h) != heln:
            raise ValueError("expected %d hash data bytes but got %d: %r", size, len(h), h)
          yield rqTag, rqType, h
        else:
          raise RuntimeError("unimplemented request type")

def decodeResultStream(self):
  ''' Generator that yields (rqTag, rqType, result) from the result stream.
  '''
  with Pfx("decodeResultStream(%s)", fp):
    while True:
      rqTag = read_bs(fp)
      if rqTag is None:
        break
      with Pfx(str(rqTag)):
        rqType = read_bs(fp)
        if rqType == T_ADD:
          hlen = read_bs(fp)
          if hlen < 1:
            raise ValueError("expected hash length >= 1, but was told %d", hlen)
          h = fp.read(hlen)
          if len(h) != heln:
            raise ValueError("expected %d hash data bytes but got %d: %r", size, len(h), h)
          yield rqTag, rqType, h
        elif rqType == T_GET:
          size = read_bs(fp)
          if size == 0:
            data = None
          else:
            data = fp.read(size)
            if len(data) != size:
              raise ValueError("expected %d data bytes but got %d: %r", size, len(data), data)
          yield rqTag, rqType, data
        elif rqType == T_CONTAINS:
          yesno = bool(read_bs(fp))
          yield rqTag, rqType, yesno
        else:
          raise RuntimeError("unimplemented reply type")

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

  def add(self, data):
    assert len(data) > 0
    tag, packet = encodeAdd(data)
    return self._sendPacket(tag, packet).get()

  def get(self, h, default=None):
    tag, packet = encodeGet(h)
    data = self._sendPacket(tag, packet).get()
    if data is None:
      return default
    return data

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
