#!/usr/bin/python
#
# Stream protocol for stores.
#       - Cameron Simpson <cs@cskk.id.au> 06dec2007
#
# TODO: SYNC, to wait for pending requests before returning
#

''' Protocol for accessing Stores over a stream connection.
'''

from __future__ import with_statement
from enum import IntEnum
import sys
from cs.excutils import logexc
from cs.logutils import warning
from cs.pfx import Pfx
from cs.py.func import prop
from cs.resources import ClosedError
from cs.serialise import put_bs, get_bs, put_bsdata, get_bsdata, put_bss, get_bss
from cs.stream import PacketConnection
from cs.threads import locked
from .hash import decode as hash_decode, HASHCLASS_BY_NAME
from .pushpull import missing_hashcodes_by_checksum
from .store import StoreError, BasicStoreSync

class RqType(IntEnum):
  ''' Packet opcode values.
  '''
  ADD = 0               # data -> hashcode
  GET = 1               # hashcode -> data
  CONTAINS = 2          # hashcode->Boolean
  FLUSH = 3             # flush local and remote servers
  HASHCODES = 4         # (hashcode,length) -> hashcodes
  HASHCODES_HASH = 5    # (hashcode,length) -> hashcode of hashcodes

class StreamStore(BasicStoreSync):
  ''' A Store connected to a remote Store via a PacketConnection.
      Optionally accept a local store to facilitate bidirectional activities
      or simply to implement the server side.
  '''

  def __init__(
      self, name, send_fp, recv_fp,
      *,
      addif=False,
      connect=None,
      local_store=None,
      exports=None,
      **kw
  ):
    ''' Initialise the Stream Store.
        `name`: the Store name.
        `send_fp`: binary stream file for sending data to the peer.
        `recv_fp`: binary stream file for receiving data from the peer.
        `addif`: optional mode causing .add to probe the peer for
            the data chunk's hash and to only submit a ADD request
            if the block is missing; this is a bandwith optimisation
            at the expense of latency.
        `connect`: if not None, a function to return `send_fp` and `recv_fp`.
          If specified, the `send_fp` and `recv_fp` parameters must be None.
        `local_store`: optional local Store for serving requests from the peer.
        `exports`: a mapping of name=>Store providing requestable Stores
        Other keyword arguments are passed to BasicStoreSync.__init__.
    '''
    super().__init__('StreamStore:%s' % (name,), **kw)
    self.mode_addif = addif
    self._local_store = local_store
    self.exports = exports
    if local_store is not None:
      if local_store.hashclass is not self.hashclass:
        raise ValueError("local_store.hashclass %s is not self.hashclass %s"
                         % (local_store.hashclass, self.hashclass))
    if connect is None:
      # set up protocol on existing stream
      # no reconnect facility
      self._conn = self._packet_connection(send_fp, recv_fp)
    else:
      # defer protocol setup until needed
      if send_fp is not None or recv_fp is not None:
        raise ValueError("connect is not None and one of send_fp or recv_fp is not None")
      self.connect = connect

  @prop
  def local_store(self):
    ''' The current local Store.
    '''
    return self._local_store

  @local_store.setter
  def local_store(self, newS):
    ''' Switch out the local Store for a new one.
    '''
    oldS = self._local_store
    if newS is not oldS:
      if newS:
        newS.open()
      self._local_store = newS
      if oldS:
        oldS.close()

  def switch_to(self, export_name):
    ''' Switch the local backend Store to one of the exports.
    '''
    self.local_store = self.exports[export_name]

  def startup(self):
    ''' Start up the StreamStore.
        Open the local_store if not None.
    '''
    super().startup()
    local_store = self.local_store
    if local_store is not None:
      local_store.open()

  def shutdown(self):
    ''' Shut down the StreamStore.
    '''
    with Pfx("SHUTDOWN %s", self):
      if '_conn' in dir(self):
        self._conn.shutdown()
      local_store = self.local_store
      if local_store is not None:
        local_store.close()
      super().shutdown()

  @locked
  def __getattr__(self, attr):
    if attr == '_conn':
      try:
        send_fp, recv_fp = self.connect()
      except Exception as e:
        raise AttributeError("%r: connect fails: %s" % (attr, e)) from e
      else:
        conn = self._conn = self._packet_connection(send_fp, recv_fp)
        return conn
    raise AttributeError(attr)

  def _packet_connection(self, send_fp, recv_fp):
    ''' Wrap a pair of binary streams in a PacketConnection.
    '''
    conn = PacketConnection(
        send_fp, recv_fp, self._handle_request,
        name='PacketConnection:'+self.name)
    # arrange to disassociate if the channel goes away
    conn.notify_recv_eof.add(self._packet_disconnect)
    conn.notify_send_eof.add(self._packet_disconnect)
    return conn

  @locked
  def _packet_disconnect(self, conn):
    warning("PacketConnection DISCONNECT notification: %s", conn)
    oconn = self._conn
    if oconn is conn:
      del self._conn
    else:
      warning("disconnect of %s, but that is not the current connection, ignoring", conn)

  def join(self):
    ''' Wait for the PacketConnection to shut down.
    '''
    self._conn.join()

  def do(self, rqtype, flags, data):
    ''' Wrapper for self._conn.do to catch and report failed autoconnection.
    '''
    with Pfx(
        "%s.do(rqtype=%s,flags=0x%02x,data=%d-bytes)",
        self, rqtype, flags, len(data)):
      try:
        conn = self._conn
      except AttributeError as e:
        raise StoreError("no connection: %s" % (e,)) from e
      else:
        try:
          return conn.do(rqtype, flags, data)
        except ClosedError as e:
          del self._conn
          raise StoreError("connection closed: %s" % (e,)) from e

  @logexc
  def _handle_request(self, rq_type, flags, payload):
    ''' Perform the action for a request packet.
        Return as for the `request_handler` parameter to PacketConnection.
    '''
    local_store = self._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    if rq_type == RqType.ADD:
      # return encoded hashcode
      return local_store.add(payload).encode()
    if rq_type == RqType.GET:
      # return 0 or (1, data)
      hashcode, offset = hash_decode(payload)
      if offset < len(payload):
        raise ValueError(
            "unparsed data after hashcode at offset %d: %r"
            % (offset, payload[offset:]))
      data = local_store.get(hashcode)
      if data is None:
        return 0
      return 1, data
    if rq_type == RqType.CONTAINS:
      # return flag
      hashcode, offset = hash_decode(payload)
      if offset < len(payload):
        raise ValueError(
            "unparsed data after hashcode at offset %d: %r"
            % (offset, payload[offset:]))
      return 1 if hashcode in local_store else 0
    if rq_type == RqType.FLUSH:
      if payload:
        raise ValueError("unexpected payload for flush")
      local_store.flush()
      return None
    if rq_type == RqType.HASHCODES:
      # return joined encoded hashcodes
      hashclass, start_hashcode, reverse, after, length \
          = self._decode_request_hashcodes(flags, payload)
      hcodes = local_store.hashcodes(
          start_hashcode=start_hashcode,
          reverse=reverse,
          after=after,
          length=length)
      payload = b''.join(h.encode() for h in hcodes)
      return payload
    if rq_type == RqType.HASHCODES_HASH:
      hashclass, start_hashcode, reverse, after, length \
          = self._decode_request_hash_of_hashcodes(flags, payload)
      if hashclass is not local_store.hashclass:
        raise ValueError(
            "request hashclass %s does not match local_store hashclass %s"
            % (hashclass, local_store.hashclass))
      if length is not None and length < 1:
        raise ValueError("length < 1: %r" % (length,))
      if after and start_hashcode is None:
        raise ValueError("after=%s but start_hashcode=%s" % (after, start_hashcode))
      hashcode, h_final = local_store.hash_of_hashcodes(
          start_hashcode=start_hashcode,
          reverse=reverse,
          after=after,
          length=length)
      payload = hashcode.encode()
      if h_final is not None:
        payload += h_final.encode()
      return payload
    raise ValueError(
        "unrecognised request code: %d, payload=%r"
        % (rq_type, payload))

  def add(self, data):
    hashclass = self.hashclass
    h = hashclass.from_chunk(data)
    if self.mode_addif:
      if self.contains(h):
        return h
    ok, flags, payload = self._conn.do(RqType.ADD, 0, data)
    if not ok:
      raise ValueError(
          "NOT OK response from add(data=%r): flags=0x%0x, payload=%r"
          % (data, flags, payload))
    h2, offset = hash_decode(payload)
    if offset != len(payload):
      raise ValueError("extra payload data after hashcode: %r" % (payload[offset:],))
    assert flags == 0
    if h != h2:
      raise RuntimeError(
          "hashclass=%s: precomputed hash %s:%s != hash from .add %s:%s"
          % (hashclass, type(h), h, type(h2), h2))
    return h

  def get(self, h):
    ok, flags, payload = self._conn.do(RqType.GET, 0, h.encode())
    if not ok:
      raise ValueError(
          "NOT OK response from get(h=%s): flags=0x%0x, payload=%r"
          % (h, flags, payload))
    found = flags & 0x01
    if found:
      flags &= ~0x01
    if flags:
      raise ValueError("unexpected flags: 0x%02x" % (flags,))
    if found:
      return payload
    if payload:
      raise ValueError("not found, but payload=%r" % (payload,))
    return None

  def contains(self, h):
    ''' Dispatch a contains request, return a Result for collection.
    '''
    ok, flags, payload = self._conn.do(RqType.CONTAINS, 0, h.encode())
    if not ok:
      raise ValueError(
          "NOT OK response from contains(h=%s): flags=0x%0x, payload=%r"
          % (h, flags, payload))
    found = flags & 0x01
    if found:
      flags &= ~0x01
    if flags:
      raise ValueError("unexpected flags: 0x%02x" % (flags,))
    if payload:
      raise ValueError("unexpected payload: %r" % (payload,))
    return found

  def flush(self):
    _, flags, payload = self._conn.do(RqType.FLUSH, 0, b'')
    assert flags == 0
    assert not payload
    local_store = self.local_store
    if local_store is not None:
      local_store.flush()

  def hashcodes_missing(self, other, window_size=None):
    ''' Generator yielding hashcodes in `other` which are missing in `self`.
    '''
    return missing_hashcodes_by_checksum(self, other, window_size=window_size)

  def hashcodes(self, start_hashcode=None, reverse=None, after=False, length=None):
    if length is not None and length < 1:
      raise ValueError("length should be None or >1, got: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError("after=%s but start_hashcode=%s" % (after, start_hashcode))
    hashclass = self.hashclass
    if length is not None and length < 1:
      raise ValueError("length should be None or >1, got: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError("after=%s but start_hashcode=%s" % (after, start_hashcode))
    flags = (
        ( 0x01 if reverse else 0x00 )
        | ( 0x02 if after else 0x00 )
    )
    payload = (
        put_bss(hashclass.HASHNAME)
        + put_bsdata(b'' if start_hashcode is None else start_hashcode.encode())
        + put_bs(length if length else 0)
    )
    ok, flags, payload = self._conn.do(RqType.HASHCODES, flags, payload)
    if not ok:
      raise ValueError(
          "NOT OK response from hashcodes(h=%s): flags=0x%0x, payload=%r"
          % (start_hashcode, flags, payload))
    if flags:
      raise ValueError("unexpected flags: 0x%02x" % (flags,))
    offset = 0
    hashary = []
    while offset < len(payload):
      hashcode, offset = hash_decode(payload, offset)
      hashary.append(hashcode)
    return hashary

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
          raise ValueError(
              "extra data in hashcode_encoded: %r"
              % (hashcode_encoded[offset2:],))
      else:
        hashcode = None
      length, offset = get_bs(payload, offset)
      if length == 0:
        length = None
      if offset != len(payload):
        raise ValueError(
            "extra data in payload at offset=%d: %r"
            % (offset, payload[offset:]))
      return hashclass, hashcode, reverse, after, length

  def hash_of_hashcodes(
      self,
      hashclass=None,
      start_hashcode=None,
      reverse=None, after=False, length=None
  ):
    if length is not None and length < 1:
      raise ValueError("length should be None or >1, got: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError("after=%s but start_hashcode=%s" % (after, start_hashcode))
    if hashclass is None:
      hashclass = self.hashclass
    flags = (
        ( 0x01 if reverse else 0x00 )
        | ( 0x02 if after else 0x00 )
    )
    payload = (
        put_bss(hashclass.HASHNAME)
        + put_bsdata(b'' if start_hashcode is None else start_hashcode.encode())
        + put_bs(length if length else 0)
    )
    ok, flags, payload = self._conn.do(RqType.HASHCODES_HASH, flags, payload)
    if not ok:
      raise ValueError(
          "NOT OK response from hash_of_hashcodes: flags=0x%0x, payload=%r"
          % (flags, payload))
    if flags:
      raise ValueError("unexpected flags: 0x%02x" % (flags,))
    hashcode, offset = hash_decode(payload, 0)
    if offset == len(payload):
      h_final = None
    else:
      h_final, offset = hash_decode(payload, offset)
      if offset < len(payload):
        raise ValueError(
            "after hashcode (%s) and h_final (%s), extra bytes: %r"
            % (hashcode, h_final, payload[offset:]))
    return hashcode, h_final

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
          raise ValueError(
              "extra data in hashcode_encoded: %r"
              % (hashcode_encoded[offset2:],))
      else:
        hashcode = None
      length, offset = get_bs(payload, offset)
      if length == 0:
        length = None
      if offset != len(payload):
        raise ValueError(
            "extra data in payload at offset=%d: %r"
            % (offset, payload[offset:]))
      return hashclass, hashcode, reverse, after, length

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
      # set the resume point: after the last yielded hashcode
      start_hashcode = hashcode
      after = True

if __name__ == '__main__':
  from .stream_tests import selftest
  selftest(sys.argv)
