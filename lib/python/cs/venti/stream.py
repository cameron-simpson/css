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
from cs.logutils import setup_logging, Pfx, info, debug, warning, X, XP
from cs.serialise import put_bs, get_bs, put_bsdata, get_bsdata, put_bss, get_bss
from cs.stream import PacketConnection
from .store import BasicStoreAsync
from .hash import decode as hash_decode, HASHCLASS_BY_NAME
from .pushpull import missing_hashcodes_by_checksum

RqType = Enum('T_ADD', 'T_GET', 'T_CONTAINS', 'T_FLUSH')
T_ADD = RqType(0)           # data->hashcode
T_GET = RqType(1)           # hashcode->data
T_CONTAINS = RqType(2)      # hash->boolean
T_FLUSH = RqType(3)         # flush local and remote store
T_HASHCODES = RqType(4)     # (hashcode,length)=>hashcodes
T_HASHCODES_HASH = RqType(5)# (hashcode,length)=>hashcode_of_hashes

class StreamStore(BasicStoreAsync):
  ''' A Store connected to a remote Store via a PacketConnection.
      Optionally accept a local store to facilitate bidirectional activities
      or simply to implement the server side.
  '''

  def __init__(self, send_fp, recv_fp, local_store=None, addif=False, name=None, **kw):
    ''' Initialise the Stream Store.
        `send_fp`: binary stream file for sending data to the peer.
        `recv_fp`: binary stream file for receiving data from the peer.
        `local_store`: optional local Store for serving requests from the peer.
        `addif`: optional mode causing .add to probe the peer for
            the data chunk's hash and to only submit a T_ADD request
            if the block is missing; this is a bandwith optimisation
            at the expense of latency.
        `name`: the Store name.
    '''
    BasicStoreAsync.__init__(self, 'StreamStore:'+name, **kw)
    if local_store is not None:
      if local_store.hashclass is not self.hashclass:
        raise ValueError("local_store.hashclass %s is not self.hashclass %s"
                         % (local_store.hashclass, self.hashclass))
    self._conn = PacketConnection(send_fp, recv_fp, self._handle_request,
                                  name='PacketConnection:'+self.name)
    self.local_store = local_store
    self.mode_addif = addif

  def startup(self):
    BasicStoreAsync.startup(self)
    local_store = self.local_store
    if local_store is not None:
      local_store.open()

  def shutdown(self):
    ''' Close the StreamStore.
    '''
    with Pfx("SHUTDOWN %s", self):
      self._conn.shutdown()
      local_store = self.local_store
      if local_store is not None:
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
      hashcode, offset = hash_decode(payload)
      if offset < len(payload):
        raise ValueError("unparsed data after hashcode at offset %d: %r"
                         % (offset, payload[offset:]))
      data = self.local_store.get(hashcode)
      if data is None:
        return 0
      return 1, data
    if rq_type == T_CONTAINS:
      hashcode, offset = hash_decode(payload)
      if offset < len(payload):
        raise ValueError("unparsed data after hashcode at offset %d: %r"
                         % (offset, payload[offset:]))
      return 1 if hashcode in self.local_store else 0
    if rq_type == T_FLUSH:
      if payload:
        raise ValueError("unexpected payload for flush")
      self.local_store.flush()
      return 0
    if rq_type == T_HASHCODES:
      hashclass, start_hashcode, reverse, after, length = self._decode_request_hashcodes(flags, payload)
      hcodes = self.local_store.hashcodes(start_hashcode=start_hashcode,
                                          reverse=reverse,
                                          after=after,
                                          length=length)
      payload = b''.join(h.encode() for h in hcodes)
      return 1, payload
    if rq_type == T_HASHCODES_HASH:
      hashclass, start_hashcode, reverse, after, length \
        = self._decode_request_hash_of_hashcodes(flags, payload)
      if hashclass is not self.local_store.hashclass:
        raise ValueError("request hashclass %s does not match local_store hashclass %s"
                         % (hashclass, self.local_store.hashclass))
      etc = self.local_store.hash_of_hashcodes(start_hashcode=start_hashcode,
                                               reverse=reverse,
                                               after=after,
                                               length=length)
      hashcode, h_final = etc
      payload = hashcode.encode()
      if h_final is not None:
        payload += h_final.encode()
      return 1, payload
    raise ValueError("unrecognised request code: %d; data=%r"
                     % (rq_type, payload))

  def _add_direct_bg(self, data):
    return self._conn.request(T_ADD, 0, data, self._decode_response_add)

  def add_bg(self, data):
    ''' Dispatch an add request, return a Result for collection.
    '''
    if not self.mode_addif:
      return self._add_direct_bg(data)
    hashclass = self.hashclass
    def add_if_missing():
      h = hashclass.from_data(data)
      if self.contains(h):
        return h
      h2 = self._add_direct_bg(data)()
      if h != h2:
        raise RuntimeError("hashclass=%s: precomputed hash %s != hash from .add %s"
                           % (hashclass, h, h2))
      return h
    LF = self._defer(add_if_missing)
    return LF

  @staticmethod
  def _decode_response_add(flags, payload):
    ''' Decode the reply to an add, should be no flags and a hashcode.
    '''
    if flags:
      raise ValueError("unexpected flags: 0x%02x" % (flags,))
    hashcode, offset = hash_decode(payload)
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
    if local_store is not None:
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

  def hashcodes_missing(self, other, window_size=None):
    ''' Generator yielding hashcodes in `other` which are missing in `self`.
    '''
    return missing_hashcodes_by_checksum(self, other, window_size=window_size)

  def hashcodes(self, *a, **kw):
    return self.hashcodes_bg(*a, **kw)()

  def hashcodes_bg(self, start_hashcode=None, reverse=None, after=False, length=None):
    ''' Dispatch a hashcodes request, return a Result for collection.
    '''
    hashclass = self.hashclass
    if length is not None and length < 1:
      raise ValueError("length should be None or >1, got: %r", length)
    flags = ( 0x01 if reverse else 0x00 ) \
          | ( 0x02 if after else 0x00 )
    payload = put_bss(hashclass.HASHNAME) \
            + put_bsdata(b'' if start_hashcode is None else start_hashcode.encode()) \
            + put_bs(length if length else 0)
    return self._conn.request(T_HASHCODES, flags, payload, self._decode_response_hashcodes)

  @staticmethod
  def _decode_request_hashcodes(flags, payload):
    ''' Reverse of the encoding in hashcodes_bg.
    '''
    with Pfx("_decode_request_hashcodes(flags=0x%02x, payload=%r)", flags, payload):
      reverse = False
      after = False
      if flags & 0x01:
        reverse = True
        flags &= ~0x01
      if flags & 0x02:
        after = True
        flags &= ~0x02
      if flags:
        raise ValueError("extra flag values: 0x%02x" % (flags,))
      hashname, offset = get_bss(payload)
      hashclass = HASHCLASS_BY_NAME[hashname]
      hashcode_encoded, offset = get_bsdata(payload, offset)
      if hashcode_encoded:
        hashcode, offset2 = hash_decode(hashcode_encoded)
        if offset2 != len(hashcode_encoded):
          raise ValueError("extra data in hashcode_encoded: %r",
                           hashcode_encoded[offset2:])
      else:
        hashcode = None
      length, offset = get_bs(payload, offset)
      if length == 0:
        length = None
      if offset != len(payload):
        raise ValueError("extra data in payload at offset=%d: %r", offset, payload[offset:])
      return hashclass, hashcode, reverse, after, length

  @staticmethod
  def _decode_response_hashcodes(flags, payload):
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
        hashcode, offset = hash_decode(payload, offset)
        hashary.append(hashcode)
      return hashary
    if payload:
      raise ValueError("not ok, but payload=%r", payload)
    return None

  def hash_of_hashcodes_bg(self, hashclass=None, start_hashcode=None, reverse=None, after=False, length=None):
    ''' Dispatch a hash_of_hashcodes request, return a Result for collection.
    '''
    if hashclass is None:
      hashclass = self.hashclass
    if length is not None and length < 1:
      raise ValueError("length should be None or >1, got: %r", length)
    flags = ( 0x01 if reverse else 0x00 ) \
          | ( 0x02 if after else 0x00 )
    payload = put_bss(hashclass.HASHNAME) \
            + put_bsdata(b'' if start_hashcode is None else start_hashcode.encode()) \
            + put_bs(length if length else 0)
    return self._conn.request(T_HASHCODES_HASH, flags, payload, self._decode_response_hash_of_hashcodes)

  def hash_of_hashcodes(self, hashclass=None, start_hashcode=None, reverse=None, after=False, length=None):
    return self.hash_of_hashcodes_bg(hashclass=hashclass,
                                     start_hashcode=start_hashcode,
                                     reverse=reverse,
                                     after=after,
                                     length=length)()

  @staticmethod
  def _decode_request_hash_of_hashcodes(flags, payload):
    ''' Reverse of the encoding in hash_of_hashcodes_bg.
    '''
    with Pfx("_decode_request_hashcodes(flags=0x%02x, payload=%r)", flags, payload):
      reverse = False
      after = False
      if flags & 0x01:
        reverse = True
        flags &= ~0x01
      if flags & 0x02:
        after = True
        flags &= ~0x02
      if flags:
        raise ValueError("extra flag values: 0x%02x" % (flags,))
      hashname, offset = get_bss(payload)
      hashclass = HASHCLASS_BY_NAME[hashname]
      hashcode_encoded, offset = get_bsdata(payload, offset)
      if hashcode_encoded:
        hashcode, offset2 = hash_decode(hashcode_encoded)
        if offset2 != len(hashcode_encoded):
          raise ValueError("extra data in hashcode_encoded: %r",
                           hashcode_encoded[offset2:])
      else:
        hashcode = None
      length, offset = get_bs(payload, offset)
      if length == 0:
        length = None
      if offset != len(payload):
        raise ValueError("extra data in payload at offset=%d: %r", offset, payload[offset:])
      return hashclass, hashcode, reverse, after, length

  @staticmethod
  def _decode_response_hash_of_hashcodes(flags, payload):
    ''' Decode the reply to a hash_of_hashcodes, should be ok, hashcode of hashcodes, and optional h_final hashcode.
    '''
    ok = flags & 0x01
    if ok:
      flags &= ~0x01
    if flags:
      raise ValueError("unexpected flags: 0x%02x" % (flags,))
    if ok:
      hashcode, offset = hash_decode(payload, 0)
      if offset == len(payload):
        h_final = None
      else:
        h_final, offset = hash_decode(payload, offset)
        if offset < len(payload):
          raise ValueError("after hashcode (%s) and h_final (%s), extra bytes: %r"
                           % (hashcode, h_final, payload[offset:]))
      return hashcode, h_final
    if payload:
      raise ValueError("not ok, but payload=%r", payload)
    return None

  def hashcodes_from(self, start_hashcode=None, reverse=False):
    ''' Unbounded sequence of hashcodes obtained by successive calls to self.hashcodes.
    '''
    length = 64
    after = False
    while True:
      hashcodes = self.hashcodes(start_hashcode=start_hashcode,
                                 reverse=reverse,
                                 after=after,
                                 length=length)
      if not hashcodes:
        return
      for hashcode in hashcodes:
        yield hashcode
      start_hashcode = hashcode
      after = True

if __name__ == '__main__':
  import cs.venti.stream_tests
  cs.venti.stream_tests.selftest(sys.argv)
