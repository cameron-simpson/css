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
from functools import lru_cache
from subprocess import Popen, PIPE
import time
from icontract import require
from cs.binary import PacketField, EmptyField, Packet, BSString, BSUInt, BSData
from cs.buffer import CornuCopyBuffer
from cs.excutils import logexc
from cs.logutils import debug, warning, error
from cs.packetstream import PacketConnection
from cs.pfx import Pfx
from cs.py.func import prop
from cs.resources import ClosedError
from cs.result import CancellationError
from cs.threads import locked
from .archive import BaseArchive, ArchiveEntry
from .dir import _Dirent
from .hash import (
    decode as hash_decode,
    decode_buffer as hash_from_buffer,
    HASHCLASS_BY_NAME,
    HASHCLASS_BY_ENUM,
    HashCode,
    HashCodeField,
    MissingHashcodeError,
)
from .pushpull import missing_hashcodes_by_checksum
from .store import StoreError, BasicStoreSync
from .transcribe import parse

class RqType(IntEnum):
  ''' Packet opcode values.
  '''
  ADD = 0               # data -> hashcode
  GET = 1               # hashcode -> data
  CONTAINS = 2          # hashcode->Boolean
  FLUSH = 3             # flush local and remote servers
  HASHCODES = 4         # (hashcode,length) -> hashcodes
  HASHCODES_HASH = 5    # (hashcode,length) -> hashcode of hashcodes
  ARCHIVE_LAST = 6      # archive_name -> (when,E)
  ARCHIVE_UPDATE = 7    # (archive_name,when,E)
  ARCHIVE_LIST = 8      # (count,archive_name) -> (when,E)...

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

        Other keyword arguments are passed to `BasicStoreSync.__init__`.
    '''
    super().__init__('StreamStore:%s' % (name,), **kw)
    self.mode_addif = addif
    self._local_store = local_store
    self.exports = exports
    # parameters controlling connection hysteresis
    self._conn_attempt_last = 0.0
    self._conn_attempt_delay = 1.0
    if connect is None:
      # set up protocol on existing stream
      # no reconnect facility
      self._conn = conn = self._packet_connection(recv, send)
      # arrange to disassociate if the channel goes away
      conn.notify_recv_eof.add(self._packet_disconnect)
      conn.notify_send_eof.add(self._packet_disconnect)
    else:
      # defer protocol setup until needed
      if recv is not None or send is not None:
        raise ValueError("connect is not None and one of recv or send is not None")
      self.connect = connect
      self._conn = None
    # caching method
    self.get_Archive = lru_cache(maxsize=64)(self.raw_get_Archive)

  def init(self):
    ''' Initialise store prior to any use.
    '''
    pass

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
      conn = self._conn
      if conn:
        conn.shutdown()
        self._conn = None
      local_store = self.local_store
      if local_store is not None:
        local_store.close()
      super().shutdown()

  def connection(self):
    ''' Return the current connection, creating it if necessary.
        Returns None if there was no current connection
        and it is too soon since the last connection attempt.
    '''
    with self._lock:
      conn = self._conn
      if conn is None:
        next_attempt = self._conn_attempt_last + self._conn_attempt_delay
        now = time.time()
        if now >= next_attempt:
          self._conn_attemp_last = now
          try:
            recv, send = self.connect()
          except Exception as e:
            error("connect fails: %s: %s", type(e).__name__, e)
          else:
            self._conn = conn = self._packet_connection(recv, send)
            # arrange to disassociate if the channel goes away
            conn.notify_recv_eof.add(self._packet_disconnect)
            conn.notify_send_eof.add(self._packet_disconnect)
    return conn

  def _packet_connection(self, recv, send):
    ''' Wrap a pair of binary streams in a PacketConnection.
    '''
    conn = PacketConnection(
        recv, send, self._handle_request,
        name='PacketConnection:'+self.name)
    return conn

  @locked
  def _packet_disconnect(self, conn):
    debug("PacketConnection DISCONNECT notification: %s", conn)
    oconn = self._conn
    if oconn is conn:
      self._conn = None
      self._conn_attemp_last = time.time()
    else:
      debug("disconnect of %s, but that is not the current connection, ignoring", conn)

  def join(self):
    ''' Wait for the PacketConnection to shut down.
    '''
    conn = self._conn
    if conn:
      conn.join()

  def do(self, rq):
    ''' Wrapper for self._conn.do to catch and report failed autoconnection.
        Raises StoreError on protocol failure or not `ok` responses.
        Returns `(flags,payload)` otherwise.
    '''
    with Pfx("%s.do(%s)", self, rq):
      conn = self.connection()
      if conn is None:
        raise StoreError("no connection")
      try:
        retval = conn.do(rq.RQTYPE, rq.flags, bytes(rq))
      except ClosedError as e:
        self._conn = None
        raise StoreError("connection closed: %s" % (e,), request=rq) from e
      except CancellationError as e:
        raise StoreError("request cancelled: %s" % (e,), request=rq) from e
      else:
        if retval is None:
          raise StoreError("NO RESPONSE", request=rq)
        ok, flags, payload = retval
        if not ok:
          raise StoreError(
              "NOT OK response",
              request=rq, flags=flags, payload=payload)
        return flags, payload
      raise RuntimeError("NOTREACHED")

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

  def add(self, data, hashclass=None):
    h = self.hash(data, hashclass)
    if self.mode_addif:
      if self.contains(h):
        return h
    flags, payload = self.do(AddRequest(data, type(h)))
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
    try:
      flags, payload = self.do(GetRequest(h))
    except StoreError as e:
      raise MissingHashcodeError(str(e)) from e
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
    flags, payload = self.do(ContainsRequest(h))
    found = flags & 0x01
    if found:
      flags &= ~0x01
    if flags:
      raise StoreError("unexpected flags: 0x%02x" % (flags,))
    if payload:
      raise StoreError("unexpected payload: %r" % (payload,))
    return found

  def flush(self):
    flags, payload = self.do(FlushRequest())
    assert flags == 0
    assert not payload
    local_store = self.local_store
    if local_store is not None:
      local_store.flush()

  def hashcodes_missing(self, other, **kw):
    ''' Generator yielding hashcodes in `other` which are missing in `self`.
    '''
    return missing_hashcodes_by_checksum(self, other, **kw)

  @require(lambda start_hashcode, hashclass:
           start_hashcode is None or hashclass is None
           or isinstance(start_hashcode, hashclass))
  def hashcodes(
      self,
      start_hashcode=None, hashclass=None,
      reverse=False, after=False, length=None
  ):
    if hashclass is None:
      if start_hashcode is None:
        hashclass = self.hashclass
      else:
        hashclass = type(start_hashcode)
    if length is not None and length < 1:
      raise ValueError("length should be None or >1, got: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError("after=%s but start_hashcode=%s" % (after, start_hashcode))
    if length is not None and length < 1:
      raise ValueError("length should be None or >1, got: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError("after=%s but start_hashcode=%s" % (after, start_hashcode))
    flags, payload = self.do(HashCodesRequest(
        start_hashcode=start_hashcode, hashclass=hashclass,
        reverse=reverse, after=after, length=length))
    if flags:
      raise StoreError("unexpected flags: 0x%02x" % (flags,))
    offset = 0
    hashary = []
    while offset < len(payload):
      hashcode, offset = hash_decode(payload, offset)
      hashary.append(hashcode)
    return hashary

  @require(lambda start_hashcode, hashclass:
           start_hashcode is None or hashclass is None
           or isinstance(start_hashcode, hashclass))
  def hash_of_hashcodes(
      self,
      start_hashcode=None, hashclass=None,
      reverse=False, after=False, length=None
  ):
    if hashclass is None:
      if start_hashcode is None:
        hashclass = self.hashclass
      else:
        hashclass = type(start_hashcode)
    if length is not None and length < 1:
      raise ValueError("length should be None or >1, got: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError("after=%s but start_hashcode=%s" % (after, start_hashcode))
    if hashclass is None:
      hashclass = self.hashclass
    flags, payload = self.do(HashOfHashCodesRequest(
        start_hashcode=start_hashcode, hashclass=hashclass,
        reverse=reverse, after=after, length=length))
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

  @require(lambda start_hashcode, hashclass:
           start_hashcode is None or hashclass is None
           or isinstance(start_hashcode, hashclass))
  def hashcodes_from(self, start_hashcode=None, hashclass=None, reverse=False):
    ''' Unbounded sequence of hashcodes
        obtained by successive calls to self.hashcodes.
    '''
    if hashclass is None:
      if start_hashcode is None:
        hashclass = self.hashclass
      else:
        hashclass = type(start_hashcode)
    length = 64
    after = False
    while True:
      hashcodes = self.hashcodes(
          start_hashcode=start_hashcode, hashclass=hashclass,
          reverse=reverse, after=after, length=length)
      if not hashcodes:
        return
      for hashcode in hashcodes:
        yield hashcode
      # set the resume point: after the last yielded hashcode
      start_hashcode = hashcode
      after = True

  def raw_get_Archive(self, archive_name, missing_ok=False):
    ''' Factory to return a StreamStoreArchive for `archive_name`.
    '''
    return StreamStoreArchive(self, archive_name)

class StreamStoreArchive(BaseArchive):
  ''' An Archive associates with this StreamStore.

      Its methods proxy requests to the archive name at the remote end.
  '''

  def __init__(self, S, archive_name):
    super().__init__()
    self.S = S
    self.archive_name = archive_name

  def __str__(self):
    return "%s(%s,%r)" % (type(self).__name__, self.S, self.archive_name)

  @prop
  def last(self):
    ''' The last Archive entry (when, E) or (None, None).
    '''
    with Pfx("%s.last", self):
      try:
        flags, payload = self.S.do(ArchiveLastRequest(self.archive_name))
      except StoreError as e:
        warning("%s, returning (None, None)", e)
        return ArchiveEntry(None, None)
      found = flags & 0x01
      if not found:
        return ArchiveEntry(None, None)
      bfr = CornuCopyBuffer.from_bytes(payload)
      entry = ArchiveEntry.from_buffer(bfr)
      return entry

  def __iter__(self):
    _, payload = self.S.do(ArchiveListRequest(self.archive_name))
    bfr = CornuCopyBuffer([payload])
    while not bfr.at_eof():
      when = BSString.value_from_buffer(bfr)
      when = float(when)
      E = BSString.value_from_buffer(bfr)
      E = parse(E)
      if not isinstance(E, _Dirent):
        raise ValueError("not a _Dirent: %r" % (E,))
      yield when, E

  def update(self, E, *, when=None, previous=None, force=False, source=None):
    ''' Save the supplied Dirent `E` with timestamp `when`.
        Return the Dirent transcription.
        Raises `StoreError` if the request fails.

        Parameters:
        * `E`: the Dirent to save.
        * `when`: the POSIX timestamp for the save, default now.
        * `source`: optional source indicator for the update, default None
    '''
    assert E is not None
    if when is None:
      when = time.time()
    self.S.do(ArchiveUpdateRequest(self.archive_name, ArchiveEntry(when, E)))
    return str(E)

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

  def __init__(self, data, hashclass):
    super().__init__(None)
    self.data = data
    self.hashclass = hashclass

  def __str__(self):
    return "%s(%s,%d:%r...)" % (
        type(self).__name__,
        type(self.hashclass).__name__, len(self.data), self.data[:16]
    )

  @classmethod
  def from_buffer(cls, bfr, flags=0):
    if flags:
      raise ValueError("flags should be 0x00, received 0x%02x" % (flags,))
    hashenum = BSUInt.value_from_buffer(bfr)
    hashclass = HASHCLASS_BY_ENUM[hashenum]
    data = BSData.value_from_buffer(bfr)
    return cls(data, hashclass)

  def transcribe(self):
    ''' Return the payload as is.
    '''
    yield self.hashclass.HASHENUM_BS
    yield BSData.transcribe_value(self.data)

  def do(self, stream):
    ''' Add data to the local store, return hashcode.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    return local_store.add(self.data, self.hashclass).encode()

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
  ''' A request to test for the presence of a hashcode.
  '''

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
  ''' A flush request.
  '''

  RQTYPE = RqType.FLUSH

  @require(lambda value: value is None)
  def __init__(self, value=None):
    super().__init__(None)

  @staticmethod
  def value_from_buffer(bfr, flags=0):
    if flags:
      raise ValueError("flags should be 0x00, received 0x%02x" % (flags,))
    return None

  def transcribe(self):
    pass

  @staticmethod
  def do(stream):
    ''' Return data from the local store by hashcode.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    local_store.flush()

class HashCodesRequest(Packet):
  ''' A request for remote hashcodes.
  '''

  RQTYPE = RqType.HASHCODES

  @require(lambda reverse: isinstance(reverse, bool))
  @require(lambda after: isinstance(after, bool))
  @require(lambda hashclass: issubclass(hashclass, HashCode))
  @require(lambda after, start_hashcode: not after or start_hashcode is not None)
  def __init__(
      self,
      *,
      reverse=False, after=False,
      hashclass=None,
      start_hashcode=None,
      length=None,
  ):
    if length is None:
      length = 0
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
            % (hashclass, type(start_hashcode)))
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
  ''' A request for a hashcode of remote hashcodes.
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

class ArchiveLastRequest(VTPacket):
  ''' Return the last entry in a remote Archive.
  '''

  RQTYPE = RqType.ARCHIVE_LAST

  @staticmethod
  def value_from_buffer(bfr, flags=0):
    if flags:
      raise ValueError("flags should be 0x00, received 0x%02x" % (flags,))
    return BSString.value_from_buffer(bfr)

  def transcribe(self):
    ''' Return the serialised hashcode.
    '''
    return BSString.transcribe_value(self.value)

  def do(self, stream):
    ''' Return data from the local store by hashcode.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    archive = local_store.get_Archive(self.value)
    entry = archive.last
    if entry.dirent is None:
      return 0
    return (
        1,
        bytes(entry)
    )

class ArchiveListRequest(VTPacket):
  ''' List the entries in a remote Archive.
  '''

  RQTYPE = RqType.ARCHIVE_LIST

  @staticmethod
  def value_from_buffer(bfr, flags=0):
    if flags:
      raise ValueError("flags should be 0x00, received 0x%02x" % (flags,))
    return BSString.value_from_buffer(bfr)

  def transcribe(self):
    ''' Return the archive name serialised.
    '''
    return BSString.transcribe_value(self.value)

  def do(self, stream):
    ''' Return ArchiveEntry transcriptions from the named Archive.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    archive = local_store.get_Archive(self.value)
    return b''.join( bytes(entry) for entry in archive )

class ArchiveUpdateRequest(VTPacket):
  ''' Add an entry to a remote Archive.
  '''

  RQTYPE = RqType.ARCHIVE_UPDATE

  def __init__(self, archive_name, entry, flags=0):
    super().__init__(None)
    self.flags = flags
    self.archive_name = archive_name
    self.entry = entry
    assert isinstance(entry, ArchiveEntry), "entry has type %s" % (type(entry),)

  @classmethod
  def from_buffer(cls, bfr, flags=0):
    if flags:
      raise ValueError("flags should be 0x00, received 0x%02x" % (flags,))
    archive_name = BSString.value_from_buffer(bfr)
    entry = ArchiveEntry.from_buffer(bfr)
    return cls(archive_name=archive_name, entry=entry, flags=flags)

  def transcribe(self):
    ''' Return the serialised archive_name and new entry.
    '''
    yield BSString.transcribe_value(self.archive_name)
    yield self.entry.transcribe()

  def do(self, stream):
    ''' Return data from the local store by hashcode.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    archive = local_store.get_Archive(self.archive_name)
    entry = self.entry
    archive.update(entry.dirent, when=entry.when)

RqType.ADD.request_class = AddRequest
RqType.GET.request_class = GetRequest
RqType.CONTAINS.request_class = ContainsRequest
RqType.FLUSH.request_class = FlushRequest
RqType.HASHCODES.request_class = HashCodesRequest
RqType.HASHCODES_HASH.request_class = HashOfHashCodesRequest
RqType.ARCHIVE_LAST.request_class = ArchiveLastRequest
RqType.ARCHIVE_LIST.request_class = ArchiveListRequest
RqType.ARCHIVE_UPDATE.request_class = ArchiveUpdateRequest

def CommandStore(shcmd, addif=False):
  ''' Factory to return a StreamStore talking to a command.
  '''
  name = "StreamStore(%r)" % ("|" + shcmd, )
  P = Popen(shcmd, shell=True, stdin=PIPE, stdout=PIPE)
  return StreamStore(name, P.stdin, P.stdout, local_store=None, addif=addif)
