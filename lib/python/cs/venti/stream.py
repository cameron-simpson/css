#!/usr/bin/python
#
# Stream protocol for vt stores.
#       - Cameron Simpson <cs@zip.com.au> 06dec2007
#
# TODO: T_SYNC, to wait for pending requests before returning
#

from __future__ import with_statement
import sys
from cs.inttypes import Enum
from cs.logutils import Pfx, info, debug, warning
from cs.serialise import put_bs
from cs.stream import PacketConnection
from .store import BasicStoreAsync
from .hash import decode as decode_hash

RqType = Enum('T_ADD', 'T_GET', 'T_CONTAINS', 'T_FLUSH')
T_ADD = RqType(0)           # data->hashcode
T_GET = RqType(1)           # hashcode->data
T_CONTAINS = RqType(2)      # hash->boolean
T_FLUSH = RqType(3)         # flush local and remote store
T_FIRST = RqType(4)         # ->first hashcode
T_HASHCODES = RqType(5)     # (hashcode,length)=>hashcodes

class StreamStore(BasicStoreAsync):
  ''' A Store connected to a remote Store via a PacketConnection.
      Optionally accept a local store to facilitate bidirectional activities.
  '''

  def __init__(self, name, send_fp, recv_fp, local_store=None):
    BasicStoreAsync.__init__(self, ':'.join( ('StreamStore', name) ))
    self._conn = PacketConnection(send_fp, recv_fp, self._handle_request,
                                  name=':'.join( (self.name, 'PacketConnection') ))
    self.local_store = local_store

  def startup(self):
    BasicStoreAsync.startup(self)
    local_store = self.local_store
    if local_store:
      local_store.open()

  def shutdown(self):
    ''' Close the StreamStore.
    '''
    if not self._conn.closed:
      self._conn.shutdown()
    local_store = self.local_store
    if local_store:
      local_store.close()
    BasicStoreAsync.shutdown(self)

  def join(self):
    ''' Wait for the PacketConnection to shut down.
    '''
    self._conn.join()

  def _handle_request(self, rq_type, flags, payload):
    ''' Perform the action for a request packet.
    '''
    if self.local_store is None:
      raise ValueError("no local_store, request rejected")
    if rq_type == T_ADD:
      return self.local_store.add(payload).encode()
    if rq_type == T_GET:
      hashcode, offset = decode_hash(payload)
      if offset < len(payload):
        raise ValueError("unparsed data after hashcode at offset %d: %r"
                         % (offset, payload[offset:]))
      data = self.local_store.get(hashcode)
      if data is None:
        return 0
      return 1, data
    if rq_type == T_CONTAINS:
      hashcode, offset = decode_hash(payload)
      if offset < len(payload):
        raise ValueError("unparsed data after hashcode at offset %d: %r"
                         % (offset, payload[offset:]))
      return 1 if hashcode in self.local_store else 0
    if rq_type == T_FLUSH:
      if payload:
        raise ValueError("unexpected payload for flush")
      self.local_store.flush()
      return 0
    if rq_type == T_FIRST:
      if payload:
        raise ValueError("unexpected payload")
      return self.local_store.first().encode()
    if rq_type == T_HASHCODES:
      if not payload:
        # no payload ==> return all hashcodes
        hashcodes = list(self.local_store.hashcodes())
      else:
        # starting hashcode and length
        hashcode, offset = decode_hash(payload)
        if offset >= len(payload):
          raise ValueError("missing length")
        return b''.join(h.encode()
                        for h
                        in self.local_store.hashcodes(hashcode=hashcode,
                                                      length=length))
    raise ValueError("unrecognised request code: %d; data=%r"
                     % (rq_type, payload))

  def add_bg(self, data):
    ''' Dispatch an add request, return a Result for collection.
    '''
    return self._conn.request(T_ADD, 0, data, self._decode_response_add)

  @staticmethod
  def _decode_response_add(flags, payload):
    ''' Decode the reply to an add, should be no flags and a hashcode.
    '''
    if flags:
      raise ValueError("unexpected flags: 0x%02x" % (flags,))
    hashcode, offset = decode_hash(payload)
    if offset < len(payload):
      raise ValueError("unexpected data after hashcode: %r" % (payload[offset:],))
    return hashcode

  def get_bg(self, h):
    ''' Dispatch a get request, return a Result for collection.
    '''
    return self._conn.request(T_GET, 0, h.encode(), self._decode_response_get)

  @staticmethod
  def _decode_response_get(flags, payload):
    ''' Decode the reply to a get, should be ok and possible payload.
    '''
    ok = flags & 0x01
    if ok:
      flags &= ~0x01
    if flags:
      raise ValueError("unexpected flags: 0x%02x" % (flags,))
    if ok:
      return payload
    if payload:
      raise ValueError("not ok, but payload=%r", payload)
    return None

  def contains_bg(self, h):
    ''' Dispatch a contains request, return a Result for collection.
    '''
    return self._conn.request(T_CONTAINS, 0, h.encode(), self._decode_response_contains)

  @staticmethod
  def _decode_response_contains(flags, payload):
    ''' Decode the reply to a contains, should be a single flag.
    '''
    ok = flags & 0x01
    if ok:
      flags &= ~0x01
    if flags:
      raise ValueError("unexpected flags: 0x%02x" % (flags,))
    if payload:
      raise ValueError("non-empty payload: %r" % (payload,))
    return ok

  def flush_bg(self):
    ''' Dispatch a sync request, flush the local Store, return a Result for collection.
    '''
    R = self._conn.request(T_FLUSH, 0, b'', self._decode_response_flush)

    local_store = self.local_store
    if local_store:
      local_store.flush()
    return R

  @staticmethod
  def _decode_response_flush(flags, payload):
    ''' Decode the reply to a contains, should be  a single flag.
    '''
    ok = flags & 0x01
    if ok:
      flags &= ~0x01
    if flags:
      raise ValueError("unexpected flags: 0x%02x" % (flags,))
    if payload:
      raise ValueError("non-empty payload: %r" % (payload,))
    return ok

  def first_bg(self):
    ''' Dispatch a first-hashcode request, return a Result for collection.
    '''
    return self._conn.request(T_FIRST, 0, b'', self._decode_response_first)

  @staticmethod
  def _decode_response_first(flags, payload):
    ''' Decode the reply to a first, should be ok and hashcode payload.
    '''
    ok = flags & 0x01
    if ok:
      flags &= ~0x01
    if flags:
      raise ValueError("unexpected flags: 0x%02x" % (flags,))
    if ok:
      if not payload:
        # no hashcodes in remote Store
        return None
      hashcode, offset = decode_hash(payload)
      if offset < len(payload):
        raise ValueError("unparsed data after hashcode: %d, %r" % (len(payload)-offset, payload[offset:]))
      return hashcode
    if payload:
      raise ValueError("not ok, but payload=%r", payload)
    return None

  def hashcodes_bg(self, h):
    ''' Dispatch a hashcodes request, return a Result for collection.
    '''
    return self._conn.request(T_FIRST, 0, b'', self._decode_response_first)

  @staticmethod
  def _decode_hashcodes_first(flags, payload):
    ''' Decode the reply to a hashcodes, should be ok and hashcodes payload.
    '''
    ok = flags & 0x01
    if ok:
      flags &= ~0x01
    if flags:
      raise ValueError("unexpected flags: 0x%02x" % (flags,))
    if ok:
      offset = 0
      hashary = []
      while offset < len(payload):
        hashcode, offset = decode_hash(payload)
        hashary.append(hashcode)
      return hashary
    if payload:
      raise ValueError("not ok, but payload=%r", payload)
    return None

if __name__ == '__main__':
  import cs.venti.stream_tests
  cs.venti.stream_tests.selftest(sys.argv)
