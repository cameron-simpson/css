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
from typing import Optional, Tuple

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
from cs.context import contextif, stackattrs
from cs.deco import fmtdoc
from cs.lex import r
from cs.logutils import debug, warning, error
from cs.packetstream import PacketConnection, PacketConnectionRecvSend
from cs.pfx import Pfx, pfx_method
from cs.resources import ClosedError
from cs.result import CancellationError, Result
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
from .store import StoreError, StoreSyncBase

STREAM_CAPACITY_DEFAULT = 1024

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
  CONTAINS_INDIRECT = 10  # hashcode->Boolean

# pylint: disable=too-many-ancestors
class StreamStore(StoreSyncBase):
  ''' A `Store` connected to a remote `Store` via a `PacketConnection`.
      Optionally accept a local store to facilitate bidirectional activities
      or simply to implement the server side.
  '''

  @fmtdoc
  @typechecked
  def __init__(
      self,
      name: str,
      recv_send: PacketConnectionRecvSend,
      *,
      addif: bool = False,
      local_store=None,
      exports=None,
      capacity=None,
      sync: bool = False,
      on_demand=False,
      **syncstore_kw,
  ):
    ''' Initialise the `StreamStore`.

        Parameters:
        * `name`: the Store name.
        * `recv_send`: used to prepare the underlying `PacketConnection`
        * `addif`: optional mode causing `.add` to probe the peer for
          the data chunk's hash and to only submit an ADD request
          if the block is missing; this is a bandwith optimisation
          at the expense of latency.
        * `capacity`: the capacity of the queue associaed with this Store;
          default from `STREAM_CAPACITY_DEFAULT` (`{STREAM_CAPACITY_DEFAULT}`)
        * `exports`: a mapping of name=>Store providing requestable Stores
        * `local_store`: optional local Store for serving requests from the peer.
        * `on_demand`: default `False; if true, use the Store in on-demand mode
        * `sync`: optional flag, default `False`;
          if true a `.add()` will block until acknowledged by the far end;
          if false a `.add()` will queue the add packet and return
          the hashcode immediately
        Other keyword arguments are passed to `StoreSyncBase.__init__`.

        The `recv_send` parameter is used to prepare the underlying
        `PacketConnection`. It may take the following forms:
        * a 2-tuple of `(recv,send)` suitable for passing directly
          to the `PacketConnection` setup; typically this is a pair
          of file descriptors or a pair of binary file streams
        * a callable returning a 3-tuple of `(recv,send,close)` as
          for `PacketConnection`'s callable mode

        The on demand mode makes use of a `PacketConnection`'s on
        demand mode and does not open the connection when the Store
        is opened.  Instead each operation (`add()` etc) uses the
        connection which will be opened and closed around it. Users
        doing multiple operations should run them inside the `connected()`
        context manager method. Example:

            with self.connected():
                if block in not self:
                    self.add(chunk)

        This supports a Store using a remote backend which may not
        always be available.

        Note: because a `sync` mode of `False` (the default) breaks this assertion:

            h = S.add(data)
            assert h in S

        due to the possibility of the contains test completing
        before the `data` have been added the `StreamStore.add`
        method has an additional optional `sync=None` parameter to
        override the default `sync` mode.
    '''
    if capacity is None:
      capacity = STREAM_CAPACITY_DEFAULT
    super().__init__(name, capacity=capacity, **syncstore_kw)
    self.conn = PacketConnection(
        recv_send,
        self.name,
        request_handler=self._handle_request,
        packet_grace=0,
        tick=True,
    )
    self.on_demand = on_demand
    self._lock = Lock()
    self.mode_addif = addif
    self._contains_cache = set()
    self.mode_sync = sync
    self._local_store = local_store
    self.exports = exports
    # caching method
    self.get_Archive = lru_cache(maxsize=64)(self.raw_get_Archive)
    assert not self.closed
    assert not self.is_open()

  def __str__(self):
    return "%s:%s(addif=%r,sync=%r)" % (
        self.__class__.__name__, self.name, self.mode_addif, self.mode_sync
    )

  def init(self):
    ''' Initialise store prior to any use.
    '''

  @property
  def local_store(self):
    ''' The local `Store`. Read only.
    '''
    return self._local_store

  @contextmanager
  def startup_shutdown(self):
    ''' Open/close `self.local_store` if not `None`.
    '''
    with super().startup_shutdown():
      with contextif(self.local_store):
        with contextif(not self.on_demand, self.conn):
          try:
            yield
          finally:
            warning("%s: STREAM STORE SHUTDOWN", self)

  @contextmanager
  def connected(self):
    ''' A context manager ensuring that the `PacketConnection` is open.
    '''
    with self.conn:
      yield

  def join(self):
    ''' Wait for the `PacketConnection` to shut down.
    '''
    self.conn.join()

  def do_bg(self, rq) -> Result:
    ''' Wrapper for `self.conn.request` to catch and report failed autoconnection.
        Raises `StoreError` on `ClosedError` or `CancellationError`.
    '''
    conn = self.conn
    try:
      submitted = conn.request(
          rq.RQTYPE, getattr(rq, 'packet_flags', 0), bytes(rq)
      )
    except ClosedError as e:
      raise StoreError("connection closed: %s" % (e,), request=rq) from e
    except CancellationError as e:
      raise StoreError("request cancelled: %s" % (e,), request=rq) from e

    def decode_response(response):
      ''' Decode the response returned by from the connection.
      '''
      if response is None:
        raise StoreError("NO RESPONSE", request=rq)
      ok, flags, payload = response
      if not ok:
        raise StoreError(
            "NOT OK response", request=rq, flags=flags, payload=payload
        )
      return flags, payload

    return submitted.post_notify(decode_response)

  @typechecked
  def do(self, rq) -> Tuple[int, bytes]:
    ''' Synchronous interface to `self.conn.request`
        In addition to the exceptions raised by `self.do_bg()`
        this method raises `StoreError` if the response is not ok.
        Otherwise it returns a `(flags,payload)` tuple
        and the caller can assume the request was ok.
    '''
    with self.conn:
      R = self.do_bg(rq)
      result = R()
      return result

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
    with local_store:
      return rq.do_local(self)

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

  def add(self, data: bytes, *, sync=None):
    ''' Add `data` to the Store, return its hashcode.

        The optinal `sync` parameter may be used to control whether
        this add is synchronous (return after the remote Store
        has completed the add) or asynchronous (return as soon as
        the add requests has been queued for the remote Store).
        If not specified, use the `sync` mode supplied when the
        `StreamStore` was initialised.
    '''
    with self.conn:
      h = self.hash(data)
      if self.mode_addif:
        # lower bandwidth, higher latency
        if self.contains(h):
          return h
      rq = AddRequest(data=data, hashenum=self.hashclass.hashenum)
      if sync is None:
        sync = self.mode_sync
      if sync:
        flags, payload = self.do(rq)
        assert flags == 0
        h2 = HashCode.decode(payload)
        assert h == h2
      else:
        self.do_bg(rq)
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
    if h in self._contains_cache:
      return True
    rq = ContainsRequest(h)
    flags, payload = self.do(rq)
    found = flags & 0x01
    if found:
      flags &= ~0x01
    if flags:
      raise StoreError("unexpected flags: 0x%02x" % (flags,))
    if payload:
      raise StoreError("unexpected payload: %r" % (payload,))
    if found:
      self._contains_cache.add(h)
    return found

  def is_complete_indirect(self, ih):
    ''' Check whether `ih`, the hashcode of an indirect Block,
        has its data and all its implied data present in this Store.
    '''
    rq = ContainsIndirectRequest(ih)
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

  @require(
      lambda self, start_hashcode: (
          start_hashcode is None or self.hashclass is None or
          isinstance(start_hashcode, self.hashclass)
      )
  )
  @typechecked
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
      start_hashcode = hashcode  # pylint: disable=undefined-loop-variable
      after = True

  def raw_get_Archive(self, archive_name, missing_ok=False):
    ''' Factory to return a `StreamStoreArchive` for `archive_name`.
    '''
    return StreamStoreArchive(self, archive_name)

class StreamStoreArchive(BaseArchive):
  ''' An `Archive` associated with a `StreamStore`.

      Its methods proxy requests to the archive name at the remote end.
  '''

  def __init__(self, S, archive_name):
    super().__init__()
    self.S = S
    self.archive_name = archive_name

  def __str__(self):
    return "%s(%s,%r)" % (type(self).__name__, self.S, self.archive_name)

  @property
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
      Es = BSString.parse_value(bfr)
      E = _Dirent.parse(Es, expected_cls=_Dirent)
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

class AddRequest(
    UnFlaggedPayloadMixin,
    BinaryMultiValue('AddRequest', dict(hashenum=BSUInt, data=BSData)),
):
  ''' An add(bytes) request, returning the hashcode for the stored data.
  '''

  RQTYPE = RqType.ADD

  @property
  def hashclass(self):
    ''' The hash class derived from the hashenum.
    '''
    return HashCode.by_index(self.hashenum)

  def do_local(self, stream):
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

  def do_local(self, stream):
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

  def do_local(self, stream):
    ''' Test for hashcode, return `1` for present, `0` otherwise.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    hashcode = self.hashcode
    return 1 if hashcode in local_store else 0

class ContainsIndirectRequest(UnFlaggedPayloadMixin, HashCodeField):
  ''' A request to test for the presence all of the blocks
      of an indirect block hashcode.
  '''

  RQTYPE = RqType.CONTAINS_INDIRECT

  @property
  def hashcode(self):
    return self.value

  def do_local(self, stream):
    ''' Test for hashcode, return `1` for present, `0` otherwise.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    hashcode = self.hashcode
    return 1 if local_store.is_complete_indirect(hashcode) else 0

class FlushRequest(UnFlaggedPayloadMixin, BinaryMultiValue('FlushRequest',
                                                           {})):
  ''' A flush request.
  '''

  RQTYPE = RqType.FLUSH

  @staticmethod
  def do_local(stream):
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
  def do_local(stream):
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

  @require(lambda hashclass: issubclass(hashclass, HashCode))
  @require(
      lambda after, start_hashcode: not after or start_hashcode is not None
  )
  @typechecked
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
    yield BSString.transcribe_value(self.hashclass.hashname)
    start_hashcode = self.start_hashcode
    if start_hashcode is not None:
      yield HashCodeField.transcribe_value(start_hashcode)
    yield BSUInt.transcribe_value(self.length)

  def do_local(self, stream):
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

  def do_local(self, stream):
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

class ArchiveLastRequest(
    UnFlaggedPayloadMixin,
    BinaryMultiValue('ArchiveLastRequest', dict(s=BSString,)),
):
  ''' Return the last entry in a remote Archive.
  '''

  RQTYPE = RqType.ARCHIVE_LAST

  def do_local(self, stream):
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

class ArchiveListRequest(
    UnFlaggedPayloadMixin,
    BinaryMultiValue('ArchiveListRequest', dict(s=BSString)),
):
  ''' List the entries in a remote Archive.
  '''

  RQTYPE = RqType.ARCHIVE_LIST

  def do_local(self, stream):
    ''' Return ArchiveEntry transcriptions from the named Archive.
    '''
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    archive = local_store.get_Archive(self.s)
    return b''.join(bytes(entry) for entry in archive)

class ArchiveUpdateRequest(
    UnFlaggedPayloadMixin,
    BinaryMultiValue(
        'ArchiveUpdateRequest',
        dict(archive_name=BSString, entry=ArchiveEntry),
    ),
):
  ''' Add an entry to a remote Archive.
  '''

  RQTYPE = RqType.ARCHIVE_UPDATE

  def do_local(self, stream):
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
RqType.CONTAINS_INDIRECT.request_class = ContainsIndirectRequest

def CommandStore(shcmd, addif=False):
  ''' Factory to return a StreamStore talking to a command.
  '''
  name = "StreamStore(%r)" % ("|" + shcmd,)
  # TODO: a PopenStore to close the Popen?
  P = Popen(shcmd, shell=True, stdin=PIPE, stdout=PIPE)
  return StreamStore(name, (P.stdin, P.stdout), local_store=None, addif=addif)
