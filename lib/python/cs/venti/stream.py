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
T_FLUSH = RqType(3)         # flush remote store

class StreamStore(BasicStoreAsync):
  ''' A Store connected to a remote Store via a PacketConnection.
      Optionally accept a local store to facilitate bidirectional activities.
  '''

  def __init__(self, name, send_fp, recv_fp, local_store=None):
    BasicStoreAsync.__init__(self, ':'.join( ('StreamStore', name) ))
    self._conn = PacketConnection(send_fp, recv_fp, self._handle_request)
    self.local_store = local_store

  def startup(self):
    BasicStoreAsync.startup(self)
    local_store = self.local_store
    if local_store:
      local_store.open()

  def shutdown(self):
    ''' Close the StreamStore.
    '''
    debug("%s.shutdown...", self)
    if not self._conn.closed:
      self._conn.shutdown()
    local_store = self.local_store
    if local_store:
      local_store.close()
    BasicStoreAsync.shutdown(self)

  def join(self):
    ''' Wait for the Store to be closed down.
    '''
    self._conn.join()
    if not self.closed:
      self.shutdown()

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
      self.local_store.sync()
      return 0
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

  def sync_bg(self):
    ''' Dispatch a sync request, flush the local Store, return a Result for collection.
    '''
    R = self._conn.request(T_FLUSH, 0, b'', self._decode_response_sync)

    local_store = self.local_store
    if local_store:
      local_store.sync()
    return R

  @staticmethod
  def _decode_response_sync(flags, payload):
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

if __name__ == '__main__':
  import cs.venti.stream_tests
  cs.venti.stream_tests.selftest(sys.argv)
