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
from cs.binary import PacketField, EmptyField, Packet, BSString, BSUInt
from cs.buffer import CornuCopyBuffer
from cs.excutils import logexc
from cs.logutils import warning
from cs.pfx import Pfx
from cs.resources import ClosedError
from cs.packetstream import PacketConnection
from cs.threads import locked
from .hash import (
    decode as hash_decode,
    decode_buffer as hash_from_buffer,
    HASHCLASS_BY_NAME,
    HashCodeField,
)
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
      self, name, recv, send,
      *,
      addif=False,
      connect=None,
      local_store=None,
      exports=None,
      **kw
  ):
    ''' Initialise the Stream Store.

        Parameters:
        * `name`: the Store name.
        * `recv`: inbound binary stream.
          If this is an `int` it is taken to be an OS file descriptor,
          otherwise it should be a `cs.buffer.CornuCopyBuffer`
          or a file like object with a `read1` or `read` method.
        * `send`: outbound binary stream.
          If this is an `int` it is taken to be an OS file descriptor,
          otherwise it should be a file like object with `.write(bytes)`
          and `.flush()` methods.
          For a file descriptor sending is done via an os.dup() of
          the supplied descriptor, so the caller remains responsible
          for closing the original descriptor.
        * `addif`: optional mode causing .add to probe the peer for
          the data chunk's hash and to only submit a ADD request
          if the block is missing; this is a bandwith optimisation
          at the expense of latency.
        * `connect`: if not None, a function to return `recv` and `send`.
          If specified, the `recv` and `send` parameters must be None.
        * `local_store`: optional local Store for serving requests from the peer.
        * `exports`: a mapping of name=>Store providing requestable Stores

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
      self._conn = self._packet_connection(recv, send)
    else:
      # defer protocol setup until needed
      if recv is not None or send is not None:
        raise ValueError("connect is not None and one of recv or send is not None")
      self.connect = connect

  @property
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
        recv, send = self.connect()
      except Exception as e:
        raise AttributeError("%r: connect fails: %s: %s" % (attr, type(e).__name__, e)) from e
      else:
        conn = self._conn = self._packet_connection(recv, send)
        return conn
    raise AttributeError(attr)

  def _packet_connection(self, recv, send):
    ''' Wrap a pair of binary streams in a PacketConnection.
    '''
    conn = PacketConnection(
        recv, send, self._handle_request,
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

  def do(self, rq):
    ''' Wrapper for self._conn.do to catch and report failed autoconnection.
    '''
    with Pfx("%s.do(%s)", self, rq):
      try:
        conn = self._conn
      except AttributeError as e:
        raise StoreError("no connection: %s" % (e,)) from e
      else:
        try:
          return conn.do(rq.RQTYPE, rq.flags, bytes(rq))
        except ClosedError as e:
          del self._conn
          raise StoreError("connection closed: %s" % (e,)) from e

  @staticmethod
  def decode_request(rq_type, flags, payload):
    ''' Decode `(flags, payload)` into a request packet.
    '''
    with Pfx(
        "decode_request(rq_type=%s, flags=0x%02x, payload=%d bytes)",
        rq_type, flags, len(payload)
    ):
      request_class = RqType(rq_type).request_class
      payload_bfr = CornuCopyBuffer.from_bytes(payload)
      rq = request_class.from_buffer(payload_bfr, flags=flags)
      if payload_bfr.offset < len(payload):
        warning(
            "%d unparsed bytes remaining in payload",
            len(payload) - payload_bfr.offset)
      return rq

  @logexc
  def _handle_request(self, rq_type, flags, payload):
    ''' Perform the action for a request packet.
        Return as for the `request_handler` parameter to `PacketConnection`.
    '''
    local_store = self._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    rq = self.decode_request(rq_type, flags, payload)
    return rq.do(self)

  def add(self, data):
    hashclass = self.hashclass
    h = hashclass.from_chunk(data)
    if self.mode_addif:
      if self.contains(h):
        return h
    ok, flags, payload = self.do(AddRequest(data))
    if not ok:
      raise StoreError(
          "NOT OK response from add(data=%d bytes): flags=0x%0x, payload=%r"
          % (len(data), flags, payload))
    h2, offset = hash_decode(payload)
    if offset != len(payload):
      raise StoreError("extra payload data after hashcode: %r" % (payload[offset:],))
    assert flags == 0
    if h != h2:
      raise RuntimeError(
          "hashclass=%s: precomputed hash %s:%s != hash from .add %s:%s"
          % (hashclass, type(h), h, type(h2), h2))
    return h

  def get(self, h):
    ok, flags, payload = self.do(GetRequest(h))
    if not ok:
      raise StoreError(
          "NOT OK response from get(h=%s): flags=0x%0x, payload=%r"
          % (h, flags, payload))
    found = flags & 0x01
    if found:
      flags &= ~0x01
    if flags:
      raise StoreError("unexpected flags: 0x%02x" % (flags,))
    if found:
      return payload
    if payload:
      raise ValueError("not found, but payload=%r" % (payload,))
    return None

  def contains(self, h):
    ''' Dispatch a contains request, return a Result for collection.
    '''
    ok, flags, payload = self.do(ContainsRequest(h))
    if not ok:
      raise ValueError(
          "NOT OK response from contains(h=%s): flags=0x%0x, payload=%r"
          % (h, flags, payload))
    found = flags & 0x01
    if found:
      flags &= ~0x01
    if flags:
      raise StoreError("unexpected flags: 0x%02x" % (flags,))
    if payload:
      raise StoreError("unexpected payload: %r" % (payload,))
    return found

  def flush(self):
    _, flags, payload = self.do(FlushRequest())
    assert flags == 0
    assert not payload
    local_store = self.local_store
    if local_store is not None:
      local_store.flush()

  def hashcodes_missing(self, other, window_size=None):
    ''' Generator yielding hashcodes in `other` which are missing in `self`.
    '''
    return missing_hashcodes_by_checksum(self, other, window_size=window_size)

  def hashcodes(
      self,
      start_hashcode=None,
      reverse=False, after=False, length=None
  ):
    if length is not None and length < 1:
      raise ValueError("length should be None or >1, got: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError("after=%s but start_hashcode=%s" % (after, start_hashcode))
    if length is not None and length < 1:
      raise ValueError("length should be None or >1, got: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError("after=%s but start_hashcode=%s" % (after, start_hashcode))
    ok, flags, payload = self.do(HashCodesRequest(
        hashclass=self.hashclass,
        start_hashcode=start_hashcode,
        reverse=reverse, after=after, length=length))
    if not ok:
      raise StoreError(
          "NOT OK response from hashcodes(h=%s): flags=0x%0x, payload=%r"
          % (start_hashcode, flags, payload))
    if flags:
      raise StoreError("unexpected flags: 0x%02x" % (flags,))
    offset = 0
    hashary = []
    while offset < len(payload):
      hashcode, offset = hash_decode(payload, offset)
      hashary.append(hashcode)
    return hashary

  def hash_of_hashcodes(
      self,
      hashclass=None,
      start_hashcode=None,
      reverse=False, after=False, length=None
  ):
    if length is not None and length < 1:
      raise ValueError("length should be None or >1, got: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError("after=%s but start_hashcode=%s" % (after, start_hashcode))
    if hashclass is None:
      hashclass = self.hashclass
    ok, flags, payload = self.do(HashOfHashCodesRequest(
        hashclass=hashclass,
        start_hashcode=start_hashcode,
        reverse=reverse, after=after, length=length))
    if not ok:
      raise StoreError(
          "NOT OK response from hash_of_hashcodes: flags=0x%0x, payload=%r"
          % (flags, payload))
    if flags:
      raise StoreError("unexpected flags: 0x%02x" % (flags,))
    hashcode, offset = hash_decode(payload, 0)
    if offset == len(payload):
      h_final = None
    else:
      h_final, offset = hash_decode(payload, offset)
      if offset < len(payload):
        raise StoreError(
            "after hashcode (%s) and h_final (%s), extra bytes: %r"
            % (hashcode, h_final, payload[offset:]))
    return hashcode, h_final

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

####################################################################
# Request packet definitions here.
# The RqType enum is defined below these, associating the packet
# classes with each RqType value.
#

class VTPacket(PacketField):
  ''' Base packet class for VT stream requests and responses.

      The real stream packet has the VTPacket.flags value folded
      into its flags.
  '''

  def __init__(self, value):
    super().__init__(value)
    self.flags = 0

class AddRequest(VTPacket):
  ''' An add(bytes) request, returning the hashcode for the stored data.
  '''

  RQTYPE = RqType.ADD

  @classmethod
  def value_from_buffer(cls, bfr, flags=0):
    if flags:
      raise ValueError("flags should be 0x00, received 0x%02x" % (flags,))
    return b''.join(bfr)

  def transcribe(self):
    ''' Return the payload as is.
    '''
    return self.value

  def do(self, stream):
    ''' Add data to the local store, return hashcode.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    return local_store.add(self.value).encode()

class GetRequest(VTPacket):
  ''' A get(hashcode) request, returning the associated bytes.
  '''

  RQTYPE = RqType.GET

  @staticmethod
  def value_from_buffer(bfr, flags=0):
    if flags:
      raise ValueError("flags should be 0x00, received 0x%02x" % (flags,))
    return hash_from_buffer(bfr)

  def transcribe(self):
    ''' Return the serialised hashcode.
    '''
    return self.value.encode()

  def do(self, stream):
    ''' Return data from the local store by hashcode.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    hashcode = self.value
    data = local_store.get(hashcode)
    if data is None:
      return 0
    return 1, data

class ContainsRequest(VTPacket):

  RQTYPE = RqType.CONTAINS

  @staticmethod
  def value_from_buffer(bfr, flags=0):
    if flags:
      raise ValueError("flags should be 0x00, received 0x%02x" % (flags,))
    return hash_from_buffer(bfr)

  def transcribe(self):
    ''' Return the serialised hashcode.
    '''
    return self.value.encode()

  def do(self, stream):
    ''' Return data from the local store by hashcode.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    hashcode = self.value
    return 1 if hashcode in local_store else 0

class FlushRequest(VTPacket):

  RQTYPE = RqType.FLUSH

  def __init__(self):
    super().__init__(None)

  @staticmethod
  def value_from_buffer(bfr, flags=0):
    if flags:
      raise ValueError("flags should be 0x00, received 0x%02x" % (flags,))
    return None

  @staticmethod
  def do(stream):
    ''' Return data from the local store by hashcode.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    local_store.flush()

class HashCodesRequest(Packet):

  RQTYPE = RqType.HASHCODES

  def __init__(
      self,
      reverse=False, after=False,
      hashclass=None,
      start_hashcode=None,
      length=None,
  ):
    assert isinstance(reverse, bool)
    assert isinstance(after, bool)
    if hashclass is None:
      raise ValueError("missing hashclass")
    if length is None:
      length = 0
    if after and start_hashcode is None:
      raise ValueError(
          "after=%s but start_hashcode=%s"
          % (after, start_hashcode))
    self.reverse = reverse
    self.after = after
    super().__init__(
        hashname=BSString(hashclass.HASHNAME),
        start_hashcode=(
            HashCodeField(start_hashcode)
            if start_hashcode is not None
            else EmptyField),
        length=BSUInt(length),
    )

  @property
  def flags(self):
    ''' Compute the flags for the request.
    '''
    return (
        ( 0x01 if self.reverse else 0x00 )
        | ( 0x02 if self.after else 0x00 )
        | ( 0x04 if self.start_hashcode is not None else 0x00 )
    )

  @classmethod
  def from_buffer(cls, bfr, flags=0):
    ''' Parse a HashCodesRequest from a buffer and construct.
    '''
    reverse = (flags & 0x01) != 0
    after = (flags & 0x02) != 0
    has_start_hashcode = (flags & 0x04) != 0
    extra_flags = flags & ~0x07
    if extra_flags:
      raise ValueError("extra flags: 0x%02x" % (extra_flags,))
    hashname = BSString.value_from_buffer(bfr)
    hashclass = HASHCLASS_BY_NAME[hashname]
    if has_start_hashcode:
      start_hashcode = HashCodeField.value_from_buffer(bfr)
      if type(start_hashcode) is not hashclass:
        raise ValueError(
            "request hashclass %s does not match start_hashcode class %s"
            % (rq.hashclass, type(start_hashcode)))
    else:
      start_hashcode = None
    length = BSUInt.value_from_buffer(bfr)
    return cls(
        reverse=reverse, after=after,
        hashclass=hashclass,
        start_hashcode=start_hashcode,
        length=length)

  def do(self, stream):
    ''' Return hashcodes from the local store.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    # return joined encoded hashcodes
    length = self.length
    if length == 0:
      length = None
    return b''.join(
        h.encode() for h in local_store.hashcodes(
            start_hashcode=self.start_hashcode,
            reverse=self.reverse,
            after=self.after,
            length=length))

class HashOfHashCodesRequest(HashCodesRequest):
  ''' A request for a hashcode of 
  '''

  RQTYPE = RqType.HASHCODES_HASH

  def do(self, stream):
    ''' Return a hash of hashcodes from the local store.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    over_hashcode, final_hashcode = local_store.hash_of_hashcodes(
        start_hashcode=self.start_hashcode,
        reverse=self.reverse,
        after=self.after,
        length=self.length)
    payload = over_hashcode.encode()
    if final_hashcode:
      payload += final_hashcode.encode()
    return payload

RqType.ADD.request_class = AddRequest
RqType.GET.request_class = GetRequest
RqType.CONTAINS.request_class = ContainsRequest
RqType.FLUSH.request_class = FlushRequest
RqType.HASHCODES.request_class = HashCodesRequest
RqType.HASHCODES_HASH.request_class = HashOfHashCodesRequest

if __name__ == '__main__':
  from .stream_tests import selftest
  selftest(sys.argv)
