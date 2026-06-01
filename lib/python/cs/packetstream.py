#!/usr/bin/python
#
# Convenience facilities for streams.
# - Cameron Simpson <cs@cskk.id.au> 21aug2015
#

''' A general purpose bidirectional packet stream connection.
'''

from abc import abstractmethod
from collections import defaultdict, namedtuple
from contextlib import contextmanager
from functools import partial
import os
import sys
from time import sleep
from threading import Lock
from typing import Callable, List, Mapping, Optional, Protocol, Tuple, Union, runtime_checkable

from cs.binary import AbstractBinary, SimpleBinary, BSUInt, BSData
from cs.buffer import CornuCopyBuffer
from cs.context import closeall, contextif, stackattrs
from cs.excutils import logexc
from cs.later import Later
from cs.logutils import warning, error, exception
from cs.pfx import Pfx, PrePfx, pfx_method
from cs.progress import Progress, progressbar
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin, RunState
from cs.result import Result, ResultSet
from cs.semantics import not_closed, ClosedError
from cs.seq import seq, Seq
from cs.threads import locked, bg as bg_thread
from cs.units import BINARY_BYTES_SCALE, DECIMAL_SCALE
from cs.upd import run_task

__version__ = '20240630-post'

DISTINFO = {
    'description':
    "general purpose bidirectional packet stream connection",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Topic :: System :: Networking",
    ],
    'install_requires': [
        'cs.binary',
        'cs.buffer',
        'cs.context',
        'cs.deco',
        'cs.progress',
        'cs.units',
        'cs.excutils',
        'cs.later',
        'cs.logutils',
        'cs.pfx',
        'cs.predicate',
        'cs.queues',
        'cs.resources',
        'cs.result',
        'cs.semantics',
        'cs.seq',
        'cs.threads',
    ]
}

# default pause before flush to allow for additional packet data to arrive
DEFAULT_PACKET_GRACE = 0.01

class Packet(SimpleBinary):
  ''' A protocol packet.
  '''

  # pylint: disable=signature-differs
  def __str__(self):
    payload = self.payload
    if len(payload) > 16:
      payload_s = repr(payload[:16]) + '...'
    else:
      payload_s = repr(payload)
    return (
        "%s(is_request=%s,channel=%s,tag=%s,flags=0x%02x,payload=%s)" % (
            type(self).__name__, self.is_request, self.channel, self.tag,
            self.flags, payload_s
        )
    )

  def __repr__(self):
    return str(self)

  def __eq__(self, other):
    return (
        bool(self.is_request) == bool(other.is_request)
        and self.channel == other.channel and self.tag == other.tag
        and self.flags == other.flags
        and (not self.is_request or self.rq_type == other.rq_type)
        and self.payload == other.payload
    )

  @classmethod
  def parse(cls, bfr, log=None):
    ''' Parse a `Packet` from a buffer.
    '''
    raw_payload = BSData.parse_value(bfr)
    payload_bfr = CornuCopyBuffer([raw_payload])
    self = cls()
    # pylint: disable=attribute-defined-outside-init
    self.tag = BSUInt.parse_value(payload_bfr)
    flags = BSUInt.parse_value(payload_bfr)
    has_channel = (flags & 0x01) != 0
    self.is_request = (flags & 0x02) != 0
    flags >>= 2
    self.flags = flags
    if has_channel:
      self.channel = BSUInt.parse_value(payload_bfr)
    else:
      self.channel = 0
    if self.is_request:
      self.rq_type = BSUInt.parse_value(payload_bfr)
    self.payload = b''.join(payload_bfr)
    if log:
      log("<== PARSE %-20s", self)
    return self

  def transcribe(self):
    ''' Transcribe this packet.
    '''
    is_request = self.is_request
    channel = self.channel
    bss = [
        BSUInt.transcribe_value(self.tag),
        BSUInt.transcribe_value(
            (0x01 if channel != 0 else 0x00)
            | (0x02 if is_request else 0x00)
            | (self.flags << 2)
        ),
        BSUInt.transcribe_value(channel) if channel != 0 else b'',
        BSUInt.transcribe_value(self.rq_type) if is_request else b'',
        self.payload,
    ]
    length = sum(len(bs) for bs in bss)
    # spit out a BSData manually to avoid pointless bytes.join
    yield BSUInt.transcribe_value(length)
    yield bss

  def write(self, file, flush=False, log=None):
    ''' Write the `Packet` to `file`.
    '''
    if log:
      log("==> WRITE %-20s, flush=%s", self, flush)
    return super().write(file, flush=flush)

class RequestState(namedtuple('RequestState', 'decode_response result')):
  ''' A state object tracking a particular request.
  '''

  def cancel(self):
    ''' Cancel this request.
    '''
    self.result.cancel()

  def complete(self, flags, payload):
    ''' Complete the request from an "ok" `flags` and `payload`.
    '''
    if self.decode_response is None:
      # return the payload bytes unchanged
      self.result.result = (True, flags, payload)
    else:
      # fulfil by decoding the payload
      try:
        decoded = self.decode_response(flags, payload)
      except Exception:
        self.result.raise_()
      else:
        self.result.result = (True, flags, decoded)

  def fail(self, flags, payload):
    ''' Fail the request from a "not ok" `flags` and `payload`.
    '''
    self.result.result = (False, flags, payload)

# type specifications for the recv_send parameter
@runtime_checkable
class ReadableFile(Protocol):
  ''' The requirements for a file used to receive.
  '''

  def read(self, size: int) -> bytes:
    ''' Read up to `size` bytes.
    '''
    ...

@runtime_checkable
class SendableFile(Protocol):
  ''' The requirements for a file used to send.
  '''

  def write(self, bs: bytes) -> int:
    ''' Write bytes, return the number of bytes written.
    '''
    ...

  def flush(self) -> None:
    ''' Flush any buffer of written bytes.
    '''
    ...

# we read from a file descriptor or a readable file or a CornuCopyBuffer
PacketConnectionRecv = Union[int, ReadableFile, CornuCopyBuffer]
# we send to a writable file descriptor or a writeable buffered file
PacketConnectionSend = Union[int, SendableFile]
PacketConnectionRecvSend = Union[
    Tuple[PacketConnectionRecv, PacketConnectionSend],
    Callable[
        (),
        Tuple[
            PacketConnectionRecv,
            PacketConnectionSend,
            Callable[(), None],
        ],
    ],
]

# pylint: disable=too-many-instance-attributes
class PacketConnection(MultiOpenMixin):
  ''' A bidirectional binary connection for exchanging requests and responses.
  '''

  CH0_TAG_START = 0

  # special packet indicating end of stream
  EOF_Packet = Packet(
      is_request=True,
      channel=0,
      tag=CH0_TAG_START,
      flags=0,
      rq_type=0,
      payload=b''
  )
  CH0_TAG_START += 1

  # special packet indicating that there will be no more requests
  ERQ_Packet = Packet(
      is_request=True,
      channel=0,
      tag=CH0_TAG_START,
      flags=0,
      rq_type=0,
      payload=b''
  )
  CH0_TAG_START += 1

  # pylint: disable=too-many-arguments
  def __init__(
      self,
      recv_send: PacketConnectionRecvSend,
      name=None,
      *,
      request_handler=None,
      packet_grace=None,
      trace_log: Optional[Callable] = None,
  ):
    ''' Initialise the `PacketConnection`.

        Parameters:
        * `recv_send`: specify the receive and send streams
        * `packet_grace`:
          default pause in the packet sending worker
          to allow another packet to be queued
          before flushing the output stream.
          Default: `DEFAULT_PACKET_GRACE`s.
          A value of `0` will flush immediately if the queue is empty.
        * `request_handler`: an optional callable accepting
          (`rq_type`, `flags`, `payload`).
          The request_handler may return one of 5 values on success:
          * `None`: response will be 0 flags and an empty payload.
          * `int`: flags only. Response will be the flags and an empty payload.
          * `bytes`: payload only. Response will be 0 flags and the payload.
          * `str`: payload only. Response will be 0 flags and the str
                  encoded as bytes using UTF-8.
          * `(int, bytes)`: Specify flags and payload for response.
          An unsuccessful request should raise an exception, which
          will cause a failure response packet.

        The `recv_send` parameter is used to prepare the connection.
        It may take the following forms:
        * a 2-tuple of `(recv,send)` specifying the receive and send streams
        * an `int` specifying a single file descriptor used for
          both receive and send
        * a callable returning a 3-tuple of `(recv,send,close)` as
          for `PacketConnection`'s callable mode
        The `(recv,send)` pair indicate the inbound and outbound binary streams.

        For preexisting streams such as pipes or sockets these can be:
        * `recv`: anything acceptable to `CornuCopyBuffer.promote()`,
          typically a file descriptor or a binary file with `.write`
          and `.flush` methods.
        * `send`: a file descriptor or a binary file with `.write`
          and `.flush` methods.

        For "on demand" use, `recv` may be a callable and `send` may be `None`.
        In this case, `recv()` must return a 3-tuple of
        `(recv,send,shutdown)` being values for `recv` and `send`
        as above, and a shutdown function to do the necessary "close"
        of the new `recv` and `send`. `shutdown` may be `None` if there is
        no meaningful close operation.
        The `PacketConnection`'s `startup_shutdown` method will
        call `recv()` to obtain the binary streams and call the
        `shutdown` on completion.
        This supports use for on demand connections, eg:

            P = PacketConnection(connect_to_server)
            ......
            with P:
                ... use P to to work ...

        where `connect_to_server()` might connect to some remote service.
    '''
    if name is None:
      name = str(seq())
    self.name = name
    self.recv_send = recv_send
    if packet_grace is None:
      packet_grace = DEFAULT_PACKET_GRACE
    self.packet_grace = packet_grace
    self.request_handler = request_handler
    if trace_log is None:
      trace_log = lambda msg, *a: None
    self.trace_log = trace_log
    self._pending = None
    self._runstate = RunState(str(self))
    self._lock = Lock()
    self._sendQ = None
    self._sent = None
    self._send_queued = None

  def __str__(self):
    return "PacketConnection[%s]" % (self.name,)

  def __repr__(self):
    return f'{self.__class__.__name__}[{getattr(self,"name","not-yet-named")}]{id(self)}'

  @contextmanager
  def startup_shutdown(self):
    with super().startup_shutdown():
      rq_in_progress = Progress(
          "%s: rq in" % (self,),
          total=0,
          units_scale=DECIMAL_SCALE,
      )
      rq_out_progress = Progress(
          "%s: request out" % (self,),
          total=0,
          units_scale=DECIMAL_SCALE,
      )
      later = Later(4, name="%s:Later" % (self,))
      with stackattrs(
          self,
          requests_allowed=self.request_handler is not None,
          # TODO: drop notify_recv_eof - only used by vt.stream and
          #       possibly no longer
          notify_recv_eof=set(),
          notify_send_eof=set(),
          # tags of remote requests in play against the local system,
          # per channel
          _channel_request_tags={0: set()},
          # LateFunctions for the requests we are performing for the remote system
          requests_in_progress=ResultSet(),
          # requests we have outstanding against the remote system, per channel
          _pending={0: {}},
          # sequence of tag numbers
          # TODO: later, reuse old tags to prevent monotonic growth of tag field?
          _tag_seq=defaultdict(Seq),
          # work queue for local requests
          _later=later,
          # dispatch queue of Packets to send
          _sendQ=IterableQueue(16, f'sendQ[{self}]'),
          # debugging: check for reuse of (channel,tag) etc
          _sent=set(),
          _send_queued=set(),
      ):
        # advance the channel 0 tag sequence past CH0_TAG_START
        # to avoid the tags used by the EOF and ERQ packets
        tag_seq0 = self._tag_seq[0]
        for _ in range(self.CH0_TAG_START):
          next(tag_seq0)
        recv_send = self.recv_send
        if callable(recv_send):
          send, recv, shutdown_recv_send = recv_send()
        else:
          recv, send = recv_send
          shutdown_recv_send = None
        try:
          recv_bfr = CornuCopyBuffer.promote(recv)
          if isinstance(send, int):
            # fdopen the file descriptor and close the file when done
            # NB: *do not* close the file descriptor
            sendf = os.fdopen(send, 'wb', closefd=False)
            sendf_close = sendf.close
          else:
            # use as is, do not close
            sendf = send
            sendf_close = lambda: None
          try:
            with self._later:
              runstate = self._runstate
              with runstate:
                # runstate->RUNNING
                with contextif(sys.stderr.isatty(), rq_in_progress.bar,
                               stalled="idle", report_print=True):
                  with contextif(sys.stderr.isatty(), rq_out_progress.bar,
                                 stalled="idle", report_print=True):
                    # dispatch Thread to process received packets
                    self._recv_thread = bg_thread(
                        self._recv_loop,
                        name="%s[_recv_loop]" % (self.name,),
                        kwargs=dict(
                            recv_bfr=recv_bfr,
                            notify_recv_eof=self.notify_recv_eof,
                            runstate=runstate,
                            rq_in_progress=rq_in_progress,
                            rq_out_progress=rq_out_progress,
                        ),
                    )
                    # dispatch Thread to send data
                    # primary purpose is to bundle output by deferring flushes
                    self._send_thread = bg_thread(
                        self._send_loop,
                        name="%s[_send]" % (self.name,),
                        kwargs=dict(
                            sendf=sendf,
                            rq_in_progress=rq_in_progress,
                            rq_out_progress=rq_out_progress,
                        ),
                    )
                    try:
                      yield
                    finally:
                      # announce end of requests to the remote end
                      self.send_erq()
              # runstate->STOPPED, should block new requests
              assert not runstate.is_running
              assert runstate.is_stopped
              # complete accepted but incomplete requests
              if self.requests_in_progress:
                with run_task(
                    "%s: wait for local running requests" % (self,),
                    report_print=True,
                ):
                  self.requests_in_progress.wait()
              # complete any outstanding requests from the remote
              if later.outstanding:  ## HUH??
                warning(
                    "%d unexpected outstanding Later jobs",
                    len(later.outstanding)
                )
                with run_task(f'{self}: wait for outstanding LateFunctions',
                              report_print=True):
                  later.wait_outstanding()
            with run_task("%s: close sendQ, wait for sender" % (self,),
                          report_print=2.0):
              self._sendQ.close(enforce_final_close=True)
              self._send_thread.join()
            if not self._sendQ.empty():
              n_extra = 0
              for item in self._sendQ:
                if item in (PacketConnection.EOF_Packet,
                            PacketConnection.ERQ_Packet):
                  continue
                n_extra += 1
                warning(
                    "  EXTRA %s:%s %s",
                    type(item),
                    item,
                    (
                        "EOF_Packet" if item == PacketConnection.EOF_Packet
                        else "ERQ_Packet"
                        if item == PacketConnection.ERQ_Packet else ""
                    ),
                )
              if n_extra > 0:
                warning(
                    "%s: %d EXTRA unsent items in _sentQ %s", self, n_extra,
                    self._sendQ
                )
            with run_task(
                "%s: wait for _recv_thread %s" % (
                    self,
                    self._recv_thread.name,
                ),
                report_print=2.0,
            ):
              self._recv_thread.join()
          finally:
            # close the send file
            sendf_close()
            # close the receive buffer
            recv_bfr.close()
            recv_bfr = None
        finally:
          if shutdown_recv_send is not None:
            shutdown_recv_send()
        ps = self._pending_states()
        if ps:
          warning("%d PENDING STATES AT SHUTDOWN", len(ps))

  def join_recv(self):
    ''' Wait for the end of the receive worker.
        Servers should call this.
    '''
    self._recv_thread.join()

  def join(self):
    ''' Wait for the send and receive workers to terminate.
    '''
    self._recv_thread.join()
    self._send_thread.join()

  def _new_tag(self, channel: int) -> int:
    return next(self._tag_seq[channel])

  @locked
  def _pending_states(self) -> List[Tuple[Tuple[int, int], RequestState]]:
    ''' Return a `list` of `( (channel,tag), RequestState )` 2-tuples
        for the currently pending requests.
    '''
    states = []
    pending = self._pending
    if pending is not None:
      for channel, channel_states in sorted(pending.items()):
        for tag, channel_state in sorted(channel_states.items()):
          states.append(((channel, tag), channel_state))
    return states

  @locked
  def _pending_add(self, channel: int, tag: int, state: RequestState):
    ''' Record some state against `(channel,tag)`.
    '''
    pending = self._pending
    if channel not in pending:
      raise ValueError("unknown channel %d" % (channel,))
    channel_info = pending[channel]
    if tag in channel_info:
      raise ValueError("tag %d already pending in channel %d" % (tag, channel))
    self._pending[channel][tag] = state

  @locked
  def _pending_pop(self, channel: int, tag: int) -> RequestState:
    ''' Retrieve and remove the state associated with `(channel,tag)`.
    '''
    pending = self._pending
    if channel not in pending:
      raise ValueError("unknown channel %d" % (channel,))
    channel_info = pending[channel]
    if tag not in channel_info:
      raise ValueError("tag %d unknown in channel %d" % (tag, channel))
    ##if False and tag == 15:
    ##  raise RuntimeError("BANG")
    return channel_info.pop(tag)

  def _pending_cancel(self):
    ''' Cancel all the pending requests.
    '''
    pending = self._pending_states()
    if pending:
      for chtag, _ in progressbar(
          pending,
          f'{self}: cancel pending requests',
          units_scale=DECIMAL_SCALE,
          report_print=True,
      ):
        channel, tag = chtag
        rq_state = self._pending_pop(channel, tag)
        rq_state.cancel()

  def _queue_packet(self, P: Packet):
    if self._sendQ is None:
      raise EOFError("_sendQ is None")
    sig = (P.channel, P.tag, P.is_request)
    if sig in self._send_queued:
      raise RuntimeError("requeue of %s: %s" % (sig, P))
    self._send_queued.add(sig)
    self.trace_log("==> SENDQ %-20s %s", P, self)
    try:
      self._sendQ.put(P)
    except ClosedError as e:
      warning("%s: packet not queued: %s (P=%s)", self._sendQ, e, P)

  def _reject(self, channel, tag, payload=b''):
    ''' Issue a rejection of the specified request.
    '''
    error("rejecting request: " + str(payload))
    if isinstance(payload, str):
      payload = payload.encode('utf-8')
    try:
      self._queue_packet(
          Packet(
              is_request=False,
              channel=channel,
              tag=tag,
              flags=0,
              payload=payload
          )
      )
    except EOFError:
      pass

  def _respond(self, channel, tag, flags, payload):
    ''' Issue a valid response.
        Tack a 1 (ok) flag onto the flags and dispatch.
    '''
    assert isinstance(channel, int)
    assert isinstance(tag, int)
    assert isinstance(flags, int)
    assert isinstance(payload, bytes)
    flags = (flags << 1) | 1
    self._queue_packet(
        Packet(
            is_request=False,
            channel=channel,
            tag=tag,
            flags=flags,
            payload=payload
        )
    )

  @not_closed
  # pylint: disable=too-many-arguments
  def submit(
      self,
      rq_type: int,
      flags: int = 0,
      payload: bytes = b'',
      *,
      decode_response=None,
      channel=0,
      label=None,
  ) -> Result:
    ''' Compose and dispatch a new request, returns a `Result`.

        Allocates a new tag, a `Result` to deliver the response, and
        records the response decode function for use when the
        response arrives.

        Parameters:
        * `rq_type`: request type code, an `int`
        * `flags`: optional flags to accompany the request, an int;
          default `0`.
        * `payload`: optional bytes-like object to accompany the request;
          default `b''`
        * `decode_response`: optional callable accepting (response_flags,
          response_payload_bytes) and returning the decoded response payload
          value; if unspecified, the response payload bytes are used
        * `label`: optional label for this request to aid debugging

        The `Result` will yield an `(ok, flags, payload)` tuple, where:
        * `ok`: whether the request was successful
        * `flags`: the response flags
        * `payload`: the response payload, decoded by decode_response
          if specified
    '''
    if label is None:
      label = ','.join(
          filter(
              len, (
                  f'rq_type={rq_type}',
                  f'0b{flags:b}' if flags else '',
                  f'{len(payload)}bs' if len(payload) else '',
              )
          )
      )
    close = closeall([self])

    def post_request_close(result, transition):
      close()

    close.__name__ = f'closeall[{self}]'
    queued = False
    try:
      if rq_type < 0:
        raise ValueError("rq_type may not be negative (%s)" % (rq_type,))
      # reserve type 0 for end-of-requests
      rq_type += 1
      tag = self._new_tag(channel)
      R = Result(f'{self.name}:{tag}:{label}')
      R.fsm_callback('DONE', post_request_close)
      R.fsm_callback('CANCELLED', post_request_close)
      self._pending_add(channel, tag, RequestState(decode_response, R))
      self._queue_packet(
          Packet(
              is_request=True,
              channel=channel,
              tag=tag,
              flags=flags,
              rq_type=rq_type,
              payload=payload
          )
      )
      queued = True
      return R
    finally:
      if not queued:
        close()

  @not_closed
  def __call__(
      self,
      rq_type,
      payload=b'',
      flags=0,
      *,
      decode_response=None,
      channel=0,
      label=None,
  ):
    ''' Calling the `PacketConnection` performs a synchronous request.
        Submits the request, then calls the `Result` returned from the request.
    '''
    R = self.submit(
        rq_type,
        flags=flags,
        payload=payload,
        channel=channel,
        decode_response=decode_response,
        label=label,
    )
    return R()

  @logexc
  # pylint: disable=too-many-arguments
  def _run_request(
      self,
      # (channel,tag) identify a particular request
      channel: int,
      tag: int,
      # a callable(rq_type,flags,payload) to perform the request
      handler: Callable[[int, int, bytes], Union[None, int, bytes, str,
                                                 Tuple[int, bytes]]],
      # the Packet contents for use by the handler
      rq_type: int,
      flags: int,
      payload: bytes,
  ):
    ''' Run a request and queue a response packet.
    '''
    self.trace_log("==> RUNRQ (%s,%s):%r %s", channel, tag, rq_type, self)
    with Pfx(
        "_run_request:%d:%d[rq_type=%d,flags=0x%02x,payload_len=%d]",
        channel,
        tag,
        rq_type,
        flags,
        len(
            payload
        ),  ##repr(payload) if len(payload) <= 32 else repr(payload[:32]) + '...'
    ):
      # the default result
      result_flags = 0
      result_payload = b''
      try:
        self.trace_log("==> HANDL (%s,%s):%r %s", channel, tag, rq_type, self)
        result = handler(rq_type, flags, payload)
        self.trace_log("<== HANDL %s %s", result, self)
        if result is None:
          # no meaningful result - return the default (0,b'')
          pass
        elif isinstance(result, int):
          result_flags = result
        elif isinstance(result, bytes):
          result_payload = result
        elif isinstance(result, str):
          result_payload = result.encode(
              encoding='utf-8', errors='xmlcharrefreplace'
          )
        else:
          result_flags, result_payload = result
      except Exception as e:  # pylint: disable=broad-except
        exception("exception: %s", e)
        self._reject(channel, tag, "exception during handler")
      else:
        self._respond(channel, tag, result_flags, result_payload)
      # release this tag, potentially available for reuse
      self._channel_request_tags[channel].remove(tag)

  def send_erq(self):
    ''' Queue the magic end-of-requests `Packet`.
    '''
    self.trace_log("==> SENDQ send ERQ_Packet from %s", self)
    self._sendQ.put(self.ERQ_Packet)

  def send_eof(self):
    ''' Queue the magic EOF `Packet`.
    '''
    self.trace_log("==> SENDQ send EOF_Packet from %s", self)
    self._sendQ.put(self.EOF_Packet)

  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
  @logexc
  @pfx_method
  def _recv_loop(
      self,
      recv_bfr,
      *,
      notify_recv_eof: set,
      runstate: RunState,
      rq_in_progress: Progress,
      rq_out_progress: Progress,
  ):
    ''' Receive packets from upstream, decode into requests and responses.
    '''
    recv_last_offset = recv_bfr.offset

    def recv_len_func(_):
      ''' Meaure the bytes consumed scanning a packet.
      '''
      nonlocal recv_last_offset
      new_offset = recv_bfr.offset
      length = new_offset - recv_last_offset
      recv_last_offset = new_offset
      return length

    def recv_request(packet):
      ''' Process a request Packet from the upstream client.
      '''
      # request from upstream client
      channel = packet.channel
      tag = packet.tag
      flags = packet.flags
      payload = packet.payload
      with Pfx("request[%d:%d]", channel, tag):
        rq_in_progress.total += 1  # note new request
        if self.request_handler is None:
          # we are only a client, not a server
          self._reject(channel, tag, "no request handler")
        elif not self.requests_allowed:
          self._reject(channel, tag, "requests no longer allowed")
        elif runstate.cancelled:
          # we are shutting down, no new work accepted
          self._reject(channel, tag, "rejecting request: runstate.cancelled")
        else:
          requests = self._channel_request_tags
          if channel not in requests:
            # unknown channel
            self._reject(channel, tag, "unknown channel %d")
          elif tag in self._channel_request_tags[channel]:
            self._reject(
                channel, tag,
                "channel %d: tag already in use: %d" % (channel, tag)
            )
          else:
            # payload for requests is the request enum and data
            rq_type = packet.rq_type
            # normalise rq_type
            rq_type -= 1
            requests[channel].add(tag)
            # queue the work function and track it
            self.trace_log("==> LATER %-20s %s %s", packet, self, self._later)
            LF = self._later.submit(
                partial(
                    self._run_request,
                    channel,
                    tag,
                    self.request_handler,
                    rq_type,
                    flags,
                    payload,
                ),
                name=f'{self}._recv_loop._run_request:{packet}',
            )
            self.trace_log("<== LATER %-20s %s %s", packet, self, LF)
            # record the LateFunction and arrange its removal on completion
            self.requests_in_progress.add(LF)
            LF.notify(self.requests_in_progress.remove)

    def recv_response(packet):
      ''' Process a response Packet from the upstream client.
      '''
      # response to a previous request from us
      channel = packet.channel
      tag = packet.tag
      flags = packet.flags
      with Pfx("response[%d:%d]", channel, tag):
        # response: get state of matching pending request, remove state
        try:
          rq_state = self._pending_pop(channel, tag)
        except ValueError as e:
          # no such pending pair - response to unknown request
          error("%d.%d: response to unknown request: %s", channel, tag, e)
        else:
          rq_out_progress.position += 1  # note completion
          # first flag is "ok", pop it off
          ok = (flags & 0x01) != 0
          flags >>= 1
          payload = packet.payload
          if ok:
            # successful reply
            rq_state.complete(flags, payload)
          else:
            # unsuccessful: return (False, other-flags, payload-bytes)
            rq_state.fail(flags, payload)

    # process the packets from upstream
    for packet in progressbar(
        Packet.scan(recv_bfr, log=self.trace_log),
        label=f'<= {self.name}',
        units_scale=BINARY_BYTES_SCALE,
        itemlenfunc=recv_len_func,
    ):
      self.trace_log("==> _RECV %-20s %s", packet, self)
      if packet == self.EOF_Packet:
        self.trace_log("==> EOFDQ %-20s %s", packet, self)
        break
      if packet == self.ERQ_Packet:
        self.trace_log("==> ENDRQ %-20s %s", packet, self)
        self.requests_allowed = False
        continue
      if packet.is_request and packet.rq_type == 0:
        # magic EOF rq_type - must be malformed (!=EOF_Packet)
        error("malformed EOF packet received: %s", packet)
        break
      if packet.is_request:
        recv_request(packet)
      else:
        recv_response(packet)
    else:
      self.trace_log("==> end of Packet.scan %s", self)
    self.trace_log("::: END RECV LOOP %s", self)
    # end of received packets: cancel any outstanding requests
    self._pending_cancel()
    # alert any listeners of receive EOF
    if notify_recv_eof:
      for notify in notify_recv_eof:
        notify(self)

  # pylint: disable=too-many-branches
  @logexc
  def _send_loop(
      self,
      *,
      sendf,
      rq_in_progress: Progress,
      rq_out_progress: Progress,
  ):
    ''' Send packets upstream.
        Write every packet directly to `sendf`.
        Flush whenever the queue is empty.

        This runs until either of:
        - the send queue is closed
        - the remote has announced end-of-requests and we have no
          outstanding requests
    '''
    with PrePfx("_SEND [%s]", self):
      Q = self._sendQ
      grace = self.packet_grace
      # run until the _sendQ closes or we get an EOF_Packet
      for P in progressbar(
          Q,
          label=f'=> {self.name}',
          units_scale=BINARY_BYTES_SCALE,
          itemlenfunc=len,
      ):
        if P == self.EOF_Packet:
          warning("explicit send of EOF_Packet")
          break
        sig = (P.channel, P.tag, P.is_request)
        if sig in self._sent:
          if P == self.ERQ_Packet:
            pass  ##warning("second send of ERQ_Packet")
          else:
            raise RuntimeError("second send of %s" % (P,))
        self._sent.add(sig)
        if P.is_request:
          rq_out_progress.total += 1  # note new sent rq
        else:
          rq_in_progress.position += 1  # note we completed a rq
        P.write(sendf, log=self.trace_log)
        if Q.empty():
          # no immediately ready further packets: flush the output buffer
          if grace > 0:
            # allow a little time for further Packets to queue
            sleep(grace)
            if Q.empty():
              # still nothing, flush
              sendf.flush()
          else:
            # no grace period, flush immediately
            sendf.flush()
      self.trace_log("::: END SEND LOOP %s", self)
      self.EOF_Packet.write(sendf, flush=True, log=self.trace_log)
      sendf.flush()

class BaseRequest:
  ''' A base class for request classes to use with `HasPacketConnection`.

      This is a mixin aimed at `*Binary` classes representing the
      request payload and supplies an `__init__` method which saves
      the optional `flags` parameter as `.flags` and passes all
      other parameters to the superclass' `__init__`
      (the `*Binary` superclass).

      As such, it is important to define the subclass like this:

          class AddRequest(
              BaseRequest,
              BinaryMultiValue('AddRequest', dict(hashenum=BSUInt, data=BSData)),
          ):

      with `BaseRequest` _first_ if you wish to omit an `__init__` method.

      Subclasses must implement the `fulfil` method to perform the
      request operation.

      Often a subclass will also implement the
      `decode_response_payload(flags,payload)` method.
      This provides the result of a request, returned by
      `HasPacketConnection.conn_do_remote` or by the `Result`
      returned from `HasPacketConnection.conn_submit`.
      The default returns the response `flags` and `payload` directly.

      This base class subclasses `AbstractBinary` to encode and
      decode the request `payload` and has an additions `flags`
      attribute for the `Packet.flags`.  As such, subclasses have
      two main routes for implemetation:

      1: Subclass an existing `AbstractBinary` subclass. For example,
         the `cs.vt.stream.ContainsRequest` looks up a hash code, and
         subclasses the `cs.vt.hash.HashField` class.

      2: Provide a `parse(bfr)` factory method and `transcribe()`
         method to parse and transcribe the request `payload` like any
         other `AbstractBinary` subclass.

      Approach 1 does not necessarily need a distinct class;
      a binary class can often be constructed in the class header.
      For example, the `cs.vt.stream.AddRequest` payload is an `int`
      representing the hash class and then the data to add. The class
      header looks like this:

          class AddRequest(
              BaseRequest,
              BinaryMultiValue('AddRequest', dict(hashenum=BSUInt, data=BSData)),
          ):

      much as one might subclass a `namedtuple` in other circumstances.
  '''

  _seq = Seq()

  def __init__(self, flags=0, **binary_kw):
    self.flags = flags
    super().__init__(**binary_kw)

  @abstractmethod
  def fulfil(self, context) -> Union[None, int, bytes, str, Tuple[int, bytes]]:
    ''' Fulfil this request at the receiving end of the connection using
        `context`, some outer object using the connection.
        Raise an exception if the request cannot be fulfilled.

        Return values suitable for the response:
        * `None`: equivalent to `(0,b'')`
        * `int`: returned in the flags with `b''` for the payload
        * `bytes`: returned as the payload with `0` as the flags
        * `str`: return `encode(s,'ascii')` as the payload with `0` as the flags
        * `(int,bytes)`: the flags and payload

        A typical implementation looks like this:

            def fulfil(self, context):
                return context.some_method(params...)

        where the `params` come from the request attributes.
    '''
    raise NotImplementedError

  @classmethod
  def from_request_payload(cls, flags: int, payload: bytes) -> "BaseRequest":
    ''' Decode a _request_ `flags` and `payload`, return a `BaseRequest` instance.

        This is called with the correct `BaseRequest` subclass
        derived from the received `Packet.rq_type`.
        It decodes the 

        This default implementation assumes that `flags==0`
        and calls `cls.from_bytes(payload)`.
    '''
    assert cls is not BaseRequest and issubclass(cls, BaseRequest)
    self = cls.from_bytes(payload)
    assert isinstance(self, cls)
    self.flags = flags
    return self

  def decode_response_payload(self, flags: int, payload: bytes):
    ''' Decode a _response_ `flags` and `payload`.

        This default implementation returns the `flags` and `payload` unchanged.
    '''
    return flags, payload

class HasPacketConnection:
  ''' This is a mixin class to aid writing classes which use a
      `PacketConnection` to communicate with some service.

      The supported request/response packet types are provided as
      a mapping of `int` `Packet.rq_type` values to a class
      implementing that request type, a subclass of `BaseRequest`.

      For example, a `cs.vt.stream.StreamStore` subclasses `HasPacketConnection`
      and initialises the mixin with this call:

          HasPacketConnection.__init__(
              self,
              recv_send,
              name,
              { 0: AddRequest,  # add chunk, return hashcode
                1: GetRequest,  # get chunk from hashcode
                .......
              },
          )

      See the `BaseRequest` class for details on how to implement
      each request type.
  '''

  _conn_seq = Seq()

  def __init__(
      self,
      recv_send: PacketConnectionRecvSend,
      name: str = None,
      *,
      rq_type_map: Mapping[int, BaseRequest],
      **packet_kw,
  ):
    ''' Initialise `self.conn` as a `PacketConnection`.

        Parameters:
        * `recv_send`: as for `PacketConnection`
        * `name`: an optional name for the connection
        * `rq_type_map`: a mapping of request types to `BaseRequest` subclasses

        Other keyword arguments are passed to `PacketConnection()`.
    '''
    self.conn = PacketConnection(
        recv_send,
        name,
        request_handler=self.conn_handle_request,
        **packet_kw,
    )
    self.conn_rq_class_by_type = rq_type_map
    self.conn_type_by_rq_class = {
        rq_class: rq_type
        for rq_type, rq_class in rq_type_map.items()
    }

  def conn_submit(
      self,
      rq: BaseRequest,
      *,
      channel=0,
      label=None,
  ) -> Result:
    ''' Submit this request to the connection, return a `Result`.
    '''
    if label is None:
      label = f'{self.__class__.__name__}-{next(self.__class__._conn_seq)}'
    return self.conn.submit(
        self.conn_type_by_rq_class[type(rq)],
        rq.flags,
        bytes(rq),
        channel=channel,
        label=label,
        ## done in conn_do_remote ## decode_response=rq.decode_response_payload,
    )

  def conn_do_remote(self, rq: BaseRequest, **submit_kw):
    ''' Run `rq` remotely.
        Raises `ValueError` if the response is not ok.
        Otherwise returns `rq.decode_response(flags, payload)`.
    '''
    with self.conn:
      R = self.conn_submit(rq, **submit_kw)
      ok, flags, payload = R()
      if not ok:
        raise ValueError(
            f'"not ok" from remote: flags=0x{flags:02x}, payload={payload.decode("utf-8",errors="replace")!r}'
        )
      return rq.decode_response_payload(flags, payload)

  def conn_handle_request(self, rq_type: int, flags: int, payload: bytes):
    ''' Handle receipt of a request packet.
        Decode the packet into a request `rq` and return `rq.fulfil(self)`.
    '''
    with self.conn:
      rq_class = self.conn_rq_class_by_type[rq_type]
      rq = rq_class.from_request_payload(flags, payload)
      return rq.fulfil(self)

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('cs.stream_tests')
