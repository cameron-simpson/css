#!/usr/bin/env python3
#
# Stream protocol for stores.
#       - Cameron Simpson <cs@cskk.id.au> 06dec2007
#
# TODO: SYNC, to wait for pending requests before returning
#

''' Protocol for accessing Stores over a stream connection.
'''

from contextlib import contextmanager
from enum import IntEnum
from functools import lru_cache
from subprocess import Popen, PIPE
from threading import Lock
import time
from typing import Optional

from icontract import require
from typeguard import typechecked

from cs.binary import (
    BinaryMultiValue,
    BSString,
    BSUInt,
    BSData,
    SimpleBinary,
)
from cs.buffer import CornuCopyBuffer
from cs.logutils import debug, warning, error
from cs.packetstream import PacketConnection
from cs.pfx import Pfx, pfx_method
from cs.py.func import prop
from cs.resources import ClosedError
from cs.result import CancellationError
from cs.threads import locked
from .archive import BaseArchive, ArchiveEntry
from .dir import _Dirent
from .hash import (
    decode as hash_decode,
    HasDotHashclassMixin,
    HashCode,
    HashCodeField,
)
from .pushpull import missing_hashcodes_by_checksum
from .store import StoreError, BasicStoreSync
from .transcribe import parse

class RqType(IntEnum):
  ''' Packet opcode values.
  '''
  ADD = 0  # data -> hashcode
  GET = 1  # hashcode -> data
  CONTAINS = 2  # hashcode->Boolean
  FLUSH = 3  # flush local and remote servers
  HASHCODES = 4  # (hashcode,length) -> hashcodes
  HASHCODES_HASH = 5  # (hashcode,length) -> hashcode of hashcodes
  ARCHIVE_LAST = 6  # archive_name -> (when,E)
  ARCHIVE_UPDATE = 7  # (archive_name,when,E)
  ARCHIVE_LIST = 8  # (count,archive_name) -> (when,E)...
  LENGTH = 9  # () -> remote-store-length

class StreamStore(BasicStoreSync):
  ''' A Store connected to a remote Store via a `PacketConnection`.
      Optionally accept a local store to facilitate bidirectional activities
      or simply to implement the server side.
  '''

  def __init__(
      self,
      name,
      recv,
      send,
      *,
      addif=False,
      connect=None,
      local_store=None,
      exports=None,
      capacity=None,
      **kw
  ):
    ''' Initialise the `StreamStore`.

        Parameters:
        * `addif`: optional mode causing `.add` to probe the peer for
          the data chunk's hash and to only submit an ADD request
          if the block is missing; this is a bandwith optimisation
          at the expense of latency.
        * `connect`: if not `None`, a function to return `recv` and `send`.
          If specified, the `recv` and `send` parameters must be `None`.
        * `exports`: a mapping of name=>Store providing requestable Stores
        * `local_store`: optional local Store for serving requests from the peer.
        * `name`: the Store name.
        * `recv`: inbound binary stream.
          If this is an `int` it is taken to be an OS file descriptor,
          otherwise it should be a `cs.buffer.CornuCopyBuffer`
          or a file like object with a `read1` or `read` method.
        * `send`: outbound binary stream.
          If this is an `int` it is taken to be an OS file descriptor,
          otherwise it should be a file like object with `.write(bytes)`
          and `.flush()` methods.
          For a file descriptor sending is done via an `os.dup()` of
          the supplied descriptor, so the caller remains responsible
          for closing the original descriptor.

        Other keyword arguments are passed to `BasicStoreSync.__init__`.
    '''
    if capacity is None:
      capacity = 1024
    super().__init__('StreamStore:%s' % (name,), capacity=capacity, **kw)
    self._lock = Lock()
    self.mode_addif = addif
    self._local_store = local_store
    self.exports = exports
    # parameters controlling connection hysteresis
    self._conn_attempt_last = 0.0
    self._conn_attempt_delay = 10.0
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
        raise ValueError(
            "connect is not None and one of recv or send is not None"
        )
      self.connect = connect
      self._conn = None
    # caching method
    self.get_Archive = lru_cache(maxsize=64)(self.raw_get_Archive)

  def init(self):
    ''' Initialise store prior to any use.
    '''

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

  @contextmanager
  def startup_shutdown(self):
    ''' Open/close `self.local_store` if not `None`.
    '''
    with super().startup_shutdown():
      local_store = self.local_store
      if local_store is not None:
        local_store.open()
      try:
        yield
      finally:
        conn = self._conn
        if conn:
          conn.shutdown()
          self._conn = None
        local_store = self.local_store
        if local_store is not None:
          local_store.close()

  @pfx_method
  def connection(self):
    ''' Return the current connection, creating it if necessary.
        Returns `None` if there was no current connection
        and it is too soon since the last connection attempt.
    '''
    with self._lock:
      conn = self._conn
      if conn is None:
        next_attempt = self._conn_attempt_last + self._conn_attempt_delay
        now = time.time()
        if now >= next_attempt:
          self._conn_attempt_last = now
          try:
            recv, send = self.connect()
          except Exception as e:  # pylint: disable=broad-except
            error("connect fails: %s: %s", type(e).__name__, e)
          else:
            self._conn = conn = self._packet_connection(recv, send)
            # arrange to disassociate if the channel goes away
            conn.notify_recv_eof.add(self._packet_disconnect)
            conn.notify_send_eof.add(self._packet_disconnect)
    return conn

  def _packet_connection(self, recv, send):
    ''' Wrap a pair of binary streams in a `PacketConnection`.
    '''
    conn = PacketConnection(
        recv,
        send,
        self._handle_request,
        name='PacketConnection:' + self.name,
        packet_grace=0,
        tick=True
    )
    return conn

  @locked
  def _packet_disconnect(self, conn):
    debug("PacketConnection DISCONNECT notification: %s", conn)
    oconn = self._conn
    if oconn is conn:
      self._conn = None
      self._conn_attempt_last = time.time()
    else:
      debug(
          "disconnect of %s, but that is not the current connection, ignoring",
          conn
      )

  def join(self):
    ''' Wait for the `PacketConnection` to shut down.
    '''
    conn = self._conn
    if conn:
      conn.join()

  def do(self, rq):
    ''' Wrapper for `self._conn.do` to catch and report failed autoconnection.
        Raises `StoreError` on protocol failure or not `ok` responses.
        Returns `(flags,payload)` otherwise.
    '''
    conn = self.connection()
    if conn is None:
      raise StoreError("no connection")
    try:
      retval = conn.do(rq.RQTYPE, getattr(rq, 'packet_flags', 0), bytes(rq))
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
            "NOT OK response", request=rq, flags=flags, payload=payload
        )
      return flags, payload
    raise RuntimeError("NOTREACHED")

  @staticmethod
  def decode_request(rq_type, flags, payload):
    ''' Decode `(flags,payload)` into a request packet.
    '''
    with Pfx("decode_request(rq_type=%s, flags=0x%02x, payload=%d bytes)",
             rq_type, flags, len(payload)):
      request_class = RqType(rq_type).request_class
      rq, offset = request_class.parse_bytes(payload, parse_flags=flags)
      if offset < len(payload):
        warning(
            "%d unparsed bytes remaining in payload",
            len(payload) - offset
        )
      return rq

  def _handle_request(self, rq_type, flags, payload):
    ''' Perform the action for a request packet.
        Return as for the `request_handler` parameter to `PacketConnection`.
    '''
    local_store = self._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    rq = self.decode_request(rq_type, flags, payload)
    return rq.do(self)

  @pfx_method
  def __len__(self):
    try:
      flags, payload = self.do(LengthRequest())
    except StoreError as e:
      error("connection: %s", e)
      return None
    assert flags == 0
    length, offset = BSUInt.parse_value_from_bytes(payload)
    if offset < len(payload):
      warning("unparsed bytes after BSUInt(length): %r", payload[offset:])
    return length

  def add(self, data):
    h = self.hash(data)
    if self.mode_addif:
      if self.contains(h):
        return h
    rq = AddRequest(data=data, hashenum=self.hashclass.HASHENUM)
    flags, payload = self.do(rq)
    h2, offset = hash_decode(payload)
    if offset != len(payload):
      raise StoreError(
          "extra payload data after hashcode: %r" % (payload[offset:],)
      )
    assert flags == 0
    if h != h2:
      raise RuntimeError(
          "precomputed hash %s:%s != hash from .add %s:%s" %
          (type(h), h, type(h2), h2)
      )
    return h

  def get(self, h, default=None):
    try:
      flags, payload = self.do(GetRequest(h))
    except StoreError as e:
      error("h=%s: %s", h, e)
      return None
    found = flags & 0x01
    if found:
      flags &= ~0x01
    if flags:
      raise StoreError("unexpected flags: 0x%02x" % (flags,))
    if found:
      return payload
    if payload:
      raise ValueError("not found, but payload=%r" % (payload,))
    return default

  def contains(self, h):
    ''' Dispatch a contains request, return a `Result` for collection.
    '''
    rq = ContainsRequest(h)
    flags, payload = self.do(rq)
    found = flags & 0x01
    if found:
      flags &= ~0x01
    if flags:
      raise StoreError("unexpected flags: 0x%02x" % (flags,))
    if payload:
      raise StoreError("unexpected payload: %r" % (payload,))
    return found

  def flush(self):
    if self._conn is None:
      pass  # XP("SKIP FLUSH WHEN _conn=None")
    else:
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

  @typechecked
  @require(
      lambda self, start_hashcode: (
          start_hashcode is None or self.hashclass is None or
          isinstance(start_hashcode, self.hashclass)
      )
  )
  def hashcodes(
      self,
      start_hashcode=None,
      after: bool = False,
      length: Optional[int] = None
  ):
    hashclass = self.hashclass
    if length is not None and length < 1:
      raise ValueError("length should be None or >1, got: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError(
          "after=%s but start_hashcode=%s" % (after, start_hashcode)
      )
    if length is not None and length < 1:
      raise ValueError("length should be None or >1, got: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError(
          "after=%s but start_hashcode=%s" % (after, start_hashcode)
      )
    flags, payload = self.do(
        HashCodesRequest(
            start_hashcode=start_hashcode,
            hashclass=hashclass,
            after=after,
            length=length
        )
    )
    if flags:
      raise StoreError("unexpected flags: 0x%02x" % (flags,))
    bfr = CornuCopyBuffer([payload])
    hashary = list(HashCodeField.scan_values(bfr))
    # verify hashcode types
    mismatches = set(
        type(hashcode).__name__
        for hashcode in hashary
        if not isinstance(hashcode, hashclass)
    )
    if mismatches:
      raise StoreError(
          "expected hashcodes of type %s, got %d mismatches of of type %s" %
          (hashclass.__name__, len(mismatches), sorted(mismatches))
      )
    return hashary

  @require(
      lambda self, start_hashcode: start_hashcode is None or
      isinstance(start_hashcode, self.hashclass)
  )
  def hash_of_hashcodes(self, start_hashcode=None, after=False, length=None):
    hashclass = self.hashclass
    if length is not None and length < 1:
      raise ValueError("length should be None or >1, got: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError(
          "after=%s but start_hashcode=%s" % (after, start_hashcode)
      )
    if hashclass is None:
      hashclass = self.hashclass
    flags, payload = self.do(
        HashOfHashCodesRequest(
            start_hashcode=start_hashcode,
            hashclass=hashclass,
            after=after,
            length=length
        )
    )
    if flags:
      raise StoreError("unexpected flags: 0x%02x" % (flags,))
    hashcode, offset = hash_decode(payload, 0)
    if offset == len(payload):
      h_final = None
    else:
      h_final, offset = hash_decode(payload, offset)
      if offset < len(payload):
        raise StoreError(
            "after hashcode (%s) and h_final (%s), extra bytes: %r" %
            (hashcode, h_final, payload[offset:])
        )
    return hashcode, h_final

  @require(
      lambda self, start_hashcode: start_hashcode is None or
      isinstance(start_hashcode, self.hashclass)
  )
  def hashcodes_from(self, start_hashcode=None):
    ''' Unbounded sequence of hashcodes
        obtained by successive calls to `self.hashcodes`.
    '''
    length = 64
    after = False
    while True:
      hashcodes = self.hashcodes(
          start_hashcode=start_hashcode, after=after, length=length
      )
      if not hashcodes:
        return
      for hashcode in hashcodes:
        yield hashcode
      # set the resume point: after the last yielded hashcode
      start_hashcode = hashcode
      after = True

  def raw_get_Archive(self, archive_name, missing_ok=False):
    ''' Factory to return a `StreamStoreArchive` for `archive_name`.
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
    ''' The last Archive entry `(when,E)` or `(None,None)`.
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
      when = BSString.parse_value(bfr)
      when = float(when)
      E = BSString.parse_value(bfr)
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
        * `source`: optional source indicator for the update, default `None`
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

class UnFlaggedPayloadMixin:
  ''' Leading mixin for packets with no parse flags
      from the `PacketStream` encapsualtion.

      This provides `parse(bfr,parse_flags)`
      and `parse_bytes(bs,parse_flags)`
      which ensure that `parse_flags==0`
      and then call the superclass method without flags.
  '''

  @classmethod
  def parse(cls, bfr, *, parse_flags=0):
    ''' Check that `parse_flags==0`
        and then call the superclass `parse` without flags.
    '''
    assert parse_flags == 0
    return super().parse(bfr)

  @classmethod
  def parse_bytes(cls, payload, *, parse_flags):
    ''' Check that `parse_flags==0`
        and then call the superclass `parse_bytes` without flags.
    '''
    assert parse_flags == 0
    return super().parse_bytes(payload)

class AddRequest(UnFlaggedPayloadMixin, BinaryMultiValue('AddRequest',
                                                         dict(hashenum=BSUInt,
                                                              data=BSData))):
  ''' An add(bytes) request, returning the hashcode for the stored data.
  '''

  RQTYPE = RqType.ADD

  @property
  def hashclass(self):
    ''' The hash class derived from the hashenum.
    '''
    return HashCode.by_index(self.hashenum)

  def do(self, stream):
    ''' Add data to the local store, return serialised hashcode.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    if self.hashclass is not local_store.hashclass:
      raise ValueError(
          "request hashclass=%s but local store %s.hashclass=%s" %
          (self.hashclass, local_store, local_store.hashclass)
      )
    # return the serialised hashcode of the added data
    return local_store.add(self.data).encode()

class GetRequest(UnFlaggedPayloadMixin, HashCodeField):
  ''' A get(hashcode) request, returning the associated bytes.
  '''

  RQTYPE = RqType.GET

  @property
  def hashcode(self):
    return self.value

  def do(self, stream):
    ''' Return data from the local store by hashcode.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    hashcode = self.hashcode
    data = local_store.get(hashcode)
    if data is None:
      return 0
    return 1, data

class ContainsRequest(UnFlaggedPayloadMixin, HashCodeField):
  ''' A request to test for the presence of a hashcode.
  '''

  RQTYPE = RqType.CONTAINS

  @property
  def hashcode(self):
    return self.value

  def do(self, stream):
    ''' Test for hashcode, return `1` for present, `0` otherwise.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    hashcode = self.hashcode
    return 1 if hashcode in local_store else 0

class FlushRequest(UnFlaggedPayloadMixin, BinaryMultiValue('FlushRequest',
                                                           {})):
  ''' A flush request.
  '''

  RQTYPE = RqType.FLUSH

  @staticmethod
  def do(stream):
    ''' Flush the `local_store`.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    local_store.flush()

class LengthRequest(UnFlaggedPayloadMixin, BinaryMultiValue('LengthRequest',
                                                            {})):
  ''' Request the length (number of indexed Blocks) of the remote Store.
  '''

  RQTYPE = RqType.LENGTH

  @staticmethod
  def do(stream):
    ''' Return the number of indexed blocks in `local_store`.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    return bytes(BSUInt(len(local_store)))

class HashCodesRequest(SimpleBinary, HasDotHashclassMixin):
  ''' A request for remote hashcodes.
  '''

  RQTYPE = RqType.HASHCODES

  @typechecked
  @require(lambda hashclass: issubclass(hashclass, HashCode))
  @require(
      lambda after, start_hashcode: not after or start_hashcode is not None
  )
  def __init__(
      self,
      *,
      after: bool = False,
      hashclass,
      start_hashcode=None,
      length: Optional[int] = None,
  ):
    if length is None:
      length = 0
    self.after = after
    super().__init__(
        hashclass=hashclass,
        hashname=hashclass.hashname,
        start_hashcode=start_hashcode,
        length=length,
    )

  @classmethod
  def parse(cls, bfr, *, parse_flags):
    ''' Parse a HashCodesRequest from a buffer and construct.
    '''
    after = (parse_flags & 0x01) != 0
    has_start_hashcode = (parse_flags & 0x02) != 0
    extra_flags = parse_flags & ~0x03
    if extra_flags:
      raise ValueError("extra flags: 0x%02x" % (extra_flags,))
    hashname = BSString.parse_value(bfr)
    hashclass = HashCode.by_index(hashname)
    if has_start_hashcode:
      start_hashcode = HashCodeField.parse_value(bfr)
      if type(start_hashcode) is not hashclass:  # pylint: disable=unidiomatic-typecheck
        raise ValueError(
            "request hashclass %s does not match start_hashcode class %s" %
            (hashclass, type(start_hashcode))
        )
    else:
      start_hashcode = None
    length = BSUInt.parse_value(bfr)
    return cls(
        after=after,
        hashclass=hashclass,
        start_hashcode=start_hashcode,
        length=length
    )

  @property
  def packet_flags(self):
    ''' Compute the flags for the request packet envelope.
    '''
    return (
        (0x01 if self.after else 0x00)
        | (0x02 if self.start_hashcode is not None else 0x00)
    )

  def transcribe(self):
    yield BSString.transcribe_value(self.hashclass.HASHNAME)
    start_hashcode = self.start_hashcode
    if start_hashcode is not None:
      yield HashCodeField.transcribe_value(start_hashcode)
    yield BSUInt.transcribe_value(self.length)

  def do(self, stream):
    ''' Return serialised hashcodes from the local store.
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
            after=self.after,
            length=length
        )
    )

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
        after=self.after,
        length=self.length
    )
    payload = over_hashcode.encode()
    if final_hashcode:
      payload += final_hashcode.encode()
    return payload

class ArchiveLastRequest(UnFlaggedPayloadMixin,
                         BinaryMultiValue('ArchiveLastRequest',
                                          dict(s=BSString))):
  ''' Return the last entry in a remote Archive.
  '''

  RQTYPE = RqType.ARCHIVE_LAST

  def do(self, stream):
    ''' Return data from the local store by hashcode.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    archive = local_store.get_Archive(self.s)
    entry = archive.last
    if entry.dirent is None:
      return 0
    return (1, bytes(entry))

class ArchiveListRequest(UnFlaggedPayloadMixin,
                         BinaryMultiValue('ArchiveListRequest',
                                          dict(s=BSString))):
  ''' List the entries in a remote Archive.
  '''

  RQTYPE = RqType.ARCHIVE_LIST

  def do(self, stream):
    ''' Return ArchiveEntry transcriptions from the named Archive.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    archive = local_store.get_Archive(self.s)
    return b''.join(bytes(entry) for entry in archive)

class ArchiveUpdateRequest(UnFlaggedPayloadMixin,
                           BinaryMultiValue('ArchiveUpdateRequest',
                                            dict(archive_name=BSString,
                                                 entry=ArchiveEntry))):
  ''' Add an entry to a remote Archive.
  '''

  RQTYPE = RqType.ARCHIVE_UPDATE

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
RqType.LENGTH.request_class = LengthRequest

def CommandStore(shcmd, addif=False):
  ''' Factory to return a StreamStore talking to a command.
  '''
  name = "StreamStore(%r)" % ("|" + shcmd,)
  P = Popen(shcmd, shell=True, stdin=PIPE, stdout=PIPE)
  return StreamStore(name, P.stdin, P.stdout, local_store=None, addif=addif)
