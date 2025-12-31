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
from functools import lru_cache
from subprocess import Popen, PIPE
from threading import Lock
import time
from typing import Optional, Union

from icontract import ensure, require
from typeguard import typechecked

from cs.binary import (
    BinaryMultiValue,
    BSString,
    BSUInt,
    BSData,
    SimpleBinary,
)
from cs.buffer import CornuCopyBuffer
from cs.context import contextif
from cs.deco import decorator, fmtdoc
from cs.logutils import warning
from cs.packetstream import (
    BaseRequest,
    PacketConnectionRecvSend,
    HasPacketConnection,
)
from cs.pfx import Pfx, pfx_method

from . import Store
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

@decorator
def uses_local_store(method):
  ''' A decorator for functions called as `method(self,local_stream,*a,**kw)`
     which is called as `method(self,stream,*a,**kw)`.
     The wrapper passes in `stream._local_store` which has been held open.
     This raises `ValueError` if `stream._local_store` is `None`.

     Example:

         @uses_local_store
         def fulfil(self, local_store):
             ... fulfil this requests against local_store ...
  '''

  def using_local_store(self, stream, *a, **kw):
    local_store = stream._local_store
    if local_store is None:
      raise ValueError("no local_store, request rejected")
    with local_store:
      return method(self, local_store, *a, **kw)

  return using_local_store

class AddRequest(
    BaseRequest,
    BinaryMultiValue('AddRequest', dict(hashenum=BSUInt, data=BSData)),
):
  ''' An add(bytes) request, returning the hashcode for the stored data.
  '''

  @property
  def hashclass(self):
    ''' The hash class derived from the hashenum.
    '''
    return HashCode.by_index(self.hashenum)

  @hashclass.setter
  def hashclass(self, hashcls):
    ''' Setting the hashclass sets the `.hashenum` attribute.
    '''
    self.hashenum = hashcls.hashenum

  @uses_local_store
  def fulfil(self, local_store: Store):
    ''' Add data to the local store, return serialised hashcode.
    '''
    if self.hashclass is not local_store.hashclass:
      raise ValueError(
          "request hashclass=%s but local store %s.hashclass=%s" %
          (self.hashclass, local_store, local_store.hashclass)
      )
    # return the serialised hashcode of the added data
    return local_store.add(self.data).encode()

  @require(lambda flags: flags == 0)
  @ensure(lambda self, result: isinstance(result, self.hashclass))
  def decode_response_payload(self, flags: int, payload: bytes):
    ''' We expect a serialised `HashCode`.
    '''
    return HashCodeField.value_from_bytes(payload)

class GetRequest(BaseRequest, HashCodeField, value_type=HashCode):
  ''' A get(hashcode) request, returning the associated bytes.
  '''

  def __init__(self, hashcode: HashCode):
    super().__init__(value=hashcode)

  @property
  def hashcode(self):
    return self.value

  @uses_local_store
  def fulfil(self, local_store: Store):
    ''' Return data from the local store by hashcode.
    '''
    hashcode = self.hashcode
    data = local_store.get(hashcode)
    if data is None:
      return 0
    return 1, data

  @require(lambda flags: flags in (0x00, 0x01))
  def decode_response_payload(self, flags: int, payload: bytes):
    ''' Return `True` or `False`.
    '''
    return None if flags == 0 else payload

class ContainsRequest(BaseRequest, HashCodeField, value_type=HashCode):
  ''' A request to test for the presence of a hashcode.
  '''

  def __init__(self, hashcode: HashCode):
    super().__init__(value=hashcode)

  @property
  def hashcode(self):
    return self.value

  @uses_local_store
  def fulfil(self, local_store: Store) -> int:
    ''' Test for hashcode, return `1` for present, `0` otherwise.
    '''
    return int(self.hashcode in local_store)

  @require(lambda flags: flags in (0x00, 0x01))
  @require(lambda payload: payload == b'')
  @typechecked
  def decode_response_payload(self, flags: int, payload: bytes) -> bool:
    return bool(flags)

class ContainsIndirectRequest(BaseRequest, HashCodeField, value_type=HashCode):
  ''' A request to test for the presence all of the blocks
      of an indirect block hashcode.
  '''

  def __init__(self, hashcode: HashCode):
    super().__init__(value=hashcode)

  @property
  def hashcode(self):
    return self.value

  @uses_local_store
  def fulfil(self, local_store: Store):
    ''' Test for hashcode, return `1` for present, `0` otherwise.
    '''
    hashcode = self.hashcode
    return int(local_store.is_complete_indirect(hashcode))

  @require(lambda flags: flags in (0x00, 0x01))
  @require(lambda payload: payload == b'')
  @typechecked
  def decode_response_payload(self, flags: int, payload: bytes) -> bool:
    return bool(flags)

class FlushRequest(BaseRequest, BinaryMultiValue('FlushRequest', {})):
  ''' A flush request.
  '''

  @uses_local_store
  def fulfil(self, local_store: Store):
    ''' Flush the `local_store`.
    '''
    local_store.flush()

class LengthRequest(BaseRequest, BinaryMultiValue('LengthRequest', {})):
  ''' Request the length (number of indexed Blocks) of the remote Store.
  '''

  @uses_local_store
  def fulfil(self, local_store: Store):
    ''' Return the number of indexed blocks in `local_store`.
    '''
    return bytes(BSUInt(len(local_store)))

  @require(lambda flags: flags == 0)
  @typechecked
  def decode_response_payload(self, flags: int, payload: bytes) -> int:
    ''' We expect a serialised `HashCode`.
    '''
    return BSUInt.value_from_bytes(payload)

class HashCodesRequest(BaseRequest, SimpleBinary, HasDotHashclassMixin):
  ''' A request for remote hashcodes.
  '''

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
    SimpleBinary.__init__(
        self,
        hashclass=hashclass,
        hashname=hashclass.hashname,
        start_hashcode=start_hashcode,
        length=length,
    )

  @classmethod
  @require(lambda flags: flags < 0x04)
  def from_request_payload(cls, flags: int, payload: bytes):
    ''' Parse a HashCodesRequest from a buffer and construct.
    '''
    after = (flags & 0x01) != 0
    has_start_hashcode = (flags & 0x02) != 0
    bfr = CornuCopyBuffer([payload])
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
  def flags(self):
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

  @uses_local_store
  def fulfil(self, local_store: Store):
    ''' Return serialised hashcodes from the local store.
    '''
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

  @uses_local_store
  def fulfil(self, local_store: Store):
    ''' Return a hash of hashcodes from the local store.
    '''
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
    BaseRequest,
    BinaryMultiValue('ArchiveLastRequest', dict(s=BSString,)),
):
  ''' Return the last entry in a remote Archive.
  '''

  @uses_local_store
  def fulfil(self, local_store: Store):
    ''' Return data from the local store by hashcode.
    '''
    archive = local_store.get_Archive(self.s)
    entry = archive.last
    if entry.dirent is None:
      return 0
    return (1, bytes(entry))

class ArchiveListRequest(
    BaseRequest,
    BinaryMultiValue('ArchiveListRequest', dict(s=BSString)),
):
  ''' List the entries in a remote Archive.
  '''

  @uses_local_store
  def fulfil(self, local_store: Store):
    ''' Return ArchiveEntry transcriptions from the named Archive.
    '''
    archive = local_store.get_Archive(self.s)
    return b''.join(bytes(entry) for entry in archive)

class ArchiveUpdateRequest(
    BaseRequest,
    BinaryMultiValue(
        'ArchiveUpdateRequest',
        dict(archive_name=BSString, entry=ArchiveEntry),
    ),
):
  ''' Add an entry to a remote Archive.
  '''

  @uses_local_store
  def fulfil(self, local_store: Store):
    ''' Return data from the local store by hashcode.
    '''
    archive = local_store.get_Archive(self.archive_name)
    entry = self.entry
    archive.update(entry.dirent, when=entry.when)

# pylint: disable=too-many-ancestors
class StreamStore(StoreSyncBase, HasPacketConnection):
  ''' A `Store` connected to a remote `Store` via a `PacketConnection`.
      Optionally accept a local store to facilitate bidirectional activities
      or simply to implement the server side.
  '''

  @fmtdoc
  ##@typechecked ## this fails the Protocol checks
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
      trace_log=None,
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
        * `capacity`: the capacity of the queue associated with this Store;
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
    StoreSyncBase.__init__(self, name, capacity=capacity, **syncstore_kw)
    HasPacketConnection.__init__(
        self,
        recv_send,
        name,
        rq_type_map={
            0: AddRequest,  # data->hashcode
            1: GetRequest,  # hashcode->data
            2: ContainsRequest,  # hashcode->bool
            3: FlushRequest,  # flush local and remote servers
            4: HashCodesRequest,  # (hashcode,length)->hashcodes
            5:
            HashOfHashCodesRequest,  # (hashcode,length)->hashcode of hashcodes
            6: ArchiveLastRequest,  # archive_name->(when,E)
            7: ArchiveUpdateRequest,  # (archive_name,when,E)
            8: ArchiveListRequest,  # (count,archive_name)->(when,E)...
            9: LengthRequest,  # ()->remote-store-length
            10: ContainsIndirectRequest,  # hashcode->bool
        },
        trace_log=trace_log,
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
        # lambda shuffle because a PacketConnection is callable
        with contextif(not self.on_demand, lambda: self.conn):
          yield

  @contextmanager
  def connected(self):
    ''' A context manager ensuring that the `PacketConnection` is open.
    '''
    with self.conn:
      with super().connected():
        yield

  def serve(self):
    ''' Serve the connection until the receive worker terminates.
    '''
    self.conn.join_recv()

  def join(self):
    ''' Wait for the `PacketConnection` to shut down.
    '''
    self.conn.join()

  @pfx_method
  def __len__(self) -> int:
    return self.conn_do_remote(LengthRequest())

  @typechecked
  def add(self, data: bytes, *, sync=None) -> HashCode:
    ''' Add `data` to the Store, return its hashcode.

        The optinal `sync` parameter may be used to control whether
        this add is synchronous (return after the remote Store has
        completed the add) or asynchronous (return as soon as the
        add request has been queued for delivery to for the remote
        Store).
        If not specified, use the `sync` mode supplied when the
        `StreamStore` was initialised.
    '''
    with self.connected():
      h = self.hash(data)
      if self.mode_addif:
        # lower bandwidth, higher latency
        if self.contains(h):
          return h
      rq = AddRequest(data=data, hashenum=self.hashclass.hashenum)
      if sync is None:
        sync = self.mode_sync
      if sync:
        h2 = self.conn_do_remote(rq)
        assert h == h2
        self._contains_cache.add(h)
      else:
        # async: submit and return the hashcode immediately
        R = self.conn_submit(rq)
        R.fsm_callback(
            'DONE', lambda fsm, fsm_trans: self._contains_cache.add(h)
        )
      return h

  @typechecked
  def get(self, h: HashCode, default=None) -> Union[bytes, None]:
    data = self.conn_do_remote(GetRequest(h))
    if data is None:
      return default
    self._contains_cache.add(h)
    return data

  @typechecked
  def contains(self, h) -> bool:
    ''' Dispatch a contains request, return a `Result` for collection.
    '''
    if h in self._contains_cache:
      return True
    found = self.conn_do_remote(ContainsRequest(h))
    if found:
      self._contains_cache.add(h)
    return found

  @typechecked
  def is_complete_indirect(self, ih: HashCode) -> bool:
    ''' Check whether `ih`, the hashcode of an indirect Block,
        has its data and all its implied data present in this Store.
    '''
    return self.conn_do_remote(ContainsIndirectRequest(ih))

  def flush(self):
    flags, payload = self.conn_do_remote(FlushRequest())
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
    flags, payload = self.conn_do_remote(
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
    self._contains_cache.update(hashary)
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

def CommandStore(shcmd, addif=False):
  ''' Factory to return a StreamStore talking to a command.
  '''
  name = "StreamStore(%r)" % ("|" + shcmd,)
  # TODO: a PopenStore to close the Popen?
  P = Popen(shcmd, shell=True, stdin=PIPE, stdout=PIPE)
  return StreamStore(name, (P.stdin, P.stdout), local_store=None, addif=addif)
