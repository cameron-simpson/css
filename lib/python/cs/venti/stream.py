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
from .store import BasicStore
from .hash import decode as decode_hash

RqType = Enum('T_ADD', 'T_GET', 'T_CONTAINS')
T_ADD = RqType(0)           # data->hashcode
T_GET = RqType(1)           # hashcode->data
T_CONTAINS = RqType(2)      # hash->boolean

class StreamStore(BasicStore):
  ''' A Store connected to a remote Store via a PacketConnection.
      Optionally accept a local store to facilitate bidirectional activities.
  '''

  def __init__(self, name, send_fp, recv_fp, local_store=None):
    BasicStore.__init__(self, ':'.join('StreamStore', name))
    self._conn = PacketConnection(send_fp, recv_fp, self._handle_request)
    self.local_store = local_store
    self.closed = False

  def shutdown(self):
    ''' Close the StreamStore.
    '''
    self.closed = True
    debug("%s.shutdown...", self)
    if not self._conn.closed:
      self._conn.shutdown()
    BasicStore.shutdown(self)

  def join(self):
    ''' Wait for the Store to be closed down.
    '''
    self._conn.join()
    if not self.closed:
      self.shutdown

  @staticmethod
  def _handle_request(rq_type, flags, payload):
    ''' Perform the action for a request packet.
    '''
    if self.local_store is None:
      raise ValueError("no local_store, request rejected")
    if rq_type == T_ADD:
      return self.local_store.add(data).encode()
    if rq_type == T_GET:
      hashcode, offset = decode_hash(payload, offset)
      if offset < len(payload):
        raise ValueError("unparsed data after hashcode at offset %d: %r"
                         % (offset, payload[offset:]))
      return self.local_store.get(hashcode)
    if rq_type == T_CONTAINS:
      hashcode, offset = decode_hash(payload, offset)
      if offset < len(payload):
        raise ValueError("unparsed data after hashcode at offset %d: %r"
                         % (offset, payload[offset:]))
      return 1 if hashcode in self.local_store else 0
    raise ValueError("unrecognised request code: %d; data=%r"
                     % (rq_type, payload[offset:]))

  def add_bg(self, data):
    ''' Dispatch an add request, return a Result for collection.
    '''
    return self._conn.request(0, put_bs(T_ADD) + data, self._decode_response_add)

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
