#!/usr/bin/python
#
# Convenience facilities for streams.
# - Cameron Simpson <cs@cskk.id.au> 21aug2015
#

''' A general purpose bidirectional packet stream connection.
'''

from collections import defaultdict, namedtuple
from contextlib import contextmanager
import errno
import os
import sys
from time import sleep
from threading import Lock
from typing import Callable, Tuple, Union

from icontract import ensure

from cs.binary import SimpleBinary, BSUInt, BSData
from cs.buffer import CornuCopyBuffer
from cs.context import stackattrs
from cs.deco import promote
from cs.excutils import logexc
from cs.later import Later
from cs.logutils import debug, warning, error, exception
from cs.pfx import Pfx, PrePfx, pfx_method
from cs.predicate import post_condition
from cs.progress import Progress, progressbar
from cs.queues import IterableQueue
from cs.resources import not_closed, ClosedError, MultiOpenMixin, RunState
from cs.result import Result, ResultSet
from cs.seq import seq, Seq
from cs.threads import locked, bg as bg_thread
from cs.units import BINARY_BYTES_SCALE, DECIMAL_SCALE
from cs.upd import run_task

def tick_fd_2(bs):
  ''' A low level tick function to write a short binary tick
      to the standard error file descriptor.

      This may be called by the send and receive workers to give
      an indication of activity type.
  '''
  os.write(2, bs)

__version__ = '20240412-post'

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
        'cs.seq',
        'cs.threads',
        'icontract',
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
  def parse(cls, bfr):
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

Request_State = namedtuple('RequestState', 'decode_response result')

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

  # special packet indicating requests
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
  @promote
  def __init__(
      self,
      recv: CornuCopyBuffer,
      send,
      name=None,
      *,
      request_handler=None,
      packet_grace=None,
      tick=None,
      recv_len_func=None,
      send_len_func=None,
  ):
    ''' Initialise the PacketConnection.

        Parameters:
        * `recv`: inbound binary stream.
          This value is automatically promoted to a `cs.buffer.CornuCopyBuffer`
          by the `CornuCopyBuffer.promote` method.
        * `recv_len_func`: optional function to compute the data
          length of a received packet; the default watches the offset
          on the receive stream
        * `send`: outbound binary stream.
          If this is an `int` it is taken to be an OS file descriptor,
          otherwise it should be a binary file like object with `.write(bytes)`
          and `.flush()` methods.
          This objects _is not closed_ by the `PacketConnection`;
          the caller has responsibility for that.
        * `send_len_func`: optional function to compute the data
          length of a sent packet; the default watches the offset
          on the send stream
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
        * `tick`: optional tick parameter, default `None`.
          If `None`, do nothing.
          If a Boolean, call `tick_fd_2` if true, otherwise do nothing.
          Otherwise `tick` should be a callable accepting a byteslike value.
    '''
    if name is None:
      name = str(seq())
    self.name = name
    self._recv = recv
    if isinstance(send, int):
      self._send = os.fdopen(send, 'wb')
    else:
      self._send = send
    if packet_grace is None:
      packet_grace = DEFAULT_PACKET_GRACE
    if tick is None:
      tick = lambda bs: None  # pylint: disable=unnecessary-lambda-assignment
    elif isinstance(tick, bool):
      if tick:
        tick = tick_fd_2
      else:
        tick = lambda bs: None  # pylint: disable=unnecessary-lambda-assignment
    self._recv_last_offset = 0
    if recv_len_func is None:

      def recv_len_func(_):
        ''' The default length of a packet is the length of the _recv.offset change.
        '''
        new_offset = self._recv.offset
        length = new_offset - self._recv_last_offset
        self._recv_last_offset = new_offset
        return length

    self._recv_len_func = recv_len_func
    self._send_last_offset = 0
    if send_len_func is None:

      def send_len_func(_):
        ''' The default length of a packet is the length of the _send.offset change.
        '''
        new_offset = self._send.offset
        length = new_offset - self._send_last_offset
        self._send_last_offset = new_offset
        return length

    self._send_len_func = send_len_func
    self.packet_grace = packet_grace
    self.request_handler = request_handler
    self.tick = tick
    self._pending = None
    self._runstate = RunState(str(self))
    self._lock = Lock()
    self._sendQ = None
    self._sent = None
    self._send_queued = None

  def __str__(self):
    return "PacketConnection[%s]" % (self.name,)

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
          _sendQ=IterableQueue(16),
          # debugging: check for reuse of (channel,tag) etc
          _sent=set(),
          _send_queued=set(),
      ):
        # advance the channel 0 tag sequence past CH0_TAG_START
        # to avoid the tags used by the EOF and ERQ packets
        tag_seq0 = self._tag_seq[0]
        for _ in range(self.CH0_TAG_START):
          next(tag_seq0)
        with self._later:
          runstate = self._runstate
          with runstate:
            # runstate->RUNNING
            with rq_in_progress.bar(stalled="idle",
                                    report_print=True) as rq_in_bar:
              with rq_out_progress.bar(stalled="idle",
                                       report_print=True) as rq_out_bar:
                # dispatch Thread to process received packets
                self._recv_thread = bg_thread(
                    self._receive_loop,
                    name="%s[_receive_loop]" % (self.name,),
                    kwargs=dict(
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
                        rq_in_progress=rq_in_progress,
                        rq_out_progress=rq_out_progress,
                    ),
                )
                try:
                  yield
                finally:
                  # announce end of requests to the remote end
                  self.end_requests()
          # runstate->STOPPED, should block new requests
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
                "surprise! %d outstanding Later jobs", len(later.outstanding)
            )
            with run_task(f'{self}: wait for outstanding LateFunctions',
                          report_print=True):
              later.wait_outstanding()
        # close the stream to the remote and wait
        with run_task("%s: close sendQ, wait for sender" % (self,),
                      report_print=True):
          self._sendQ.close(enforce_final_close=True)
          self._send_thread.join()
        with run_task(
            "%s: wait for _recv_thread %s" % (
                self,
                self._recv_thread,
            ),
            report_print=True,
        ):
          self._recv_thread.join()
        ps = self._pending_states()
        if ps:
          warning("%d PENDING STATES AT SHUTDOWN", len(ps))

  def join(self):
    ''' Wait for the receive side of the connection to terminate.
    '''
    self._recv_thread.join()

  def _new_tag(self, channel: int) -> int:
    return next(self._tag_seq[channel])

  def _pending_states(self):
    ''' Return a list of ( (channel, tag), Request_State ) for the currently pending requests.
    '''
    states = []
    pending = self._pending
    if pending is not None:
      for channel, channel_states in sorted(pending.items()):
        for tag, channel_state in sorted(channel_states.items()):
          states.append(((channel, tag), channel_state))
    return states

  @locked
  def _pending_add(self, channel, tag, state):
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
  def _pending_pop(self, channel, tag):
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
        _, result = self._pending_pop(channel, tag)
        result.cancel()

  def _queue_packet(self, P):
    if self._sendQ is None:
      raise EOFError("_sendQ is None")
    sig = (P.channel, P.tag, P.is_request)
    if sig in self._send_queued:
      raise RuntimeError("requeue of %s: %s" % (sig, P))
    self._send_queued.add(sig)
    try:
      self._sendQ.put(P)
    except ClosedError as e:
      warning("%s: packet not sent: %s (P=%s)", self._sendQ, e, P)

  def _reject(self, channel, tag, payload=bytes(())):
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
    except EOFError as e:
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
  def request(
      self,
      rq_type,
      flags=0,
      payload=b'',
      decode_response=None,
      channel=0,
  ) -> Result:
    ''' Compose and dispatch a new request, returns a `Result`.

        Allocates a new tag, a Result to deliver the response, and
        records the response decode function for use when the
        response arrives.

        Parameters:
        * `rq_type`: request type code, an int
        * `flags`: optional flags to accompany the request, an int;
          default `0`.
        * `payload`: optional bytes-like object to accompany the request;
          default `b''`
        * `decode_response`: optional callable accepting (response_flags,
          response_payload_bytes) and returning the decoded response payload
          value; if unspecified, the response payload bytes are used

        The Result will yield an `(ok, flags, payload)` tuple, where:
        * `ok`: whether the request was successful
        * `flags`: the response flags
        * `payload`: the response payload, decoded by decode_response
          if specified
    '''
    if not self._runstate.running:
      raise ClosedError(f'not running: {self._runstate}')
    if rq_type < 0:
      raise ValueError("rq_type may not be negative (%s)" % (rq_type,))
    # reserve type 0 for end-of-requests
    rq_type += 1
    tag = self._new_tag(channel)
    R = Result(f'{self.name}:{tag}')
    self._pending_add(channel, tag, Request_State(decode_response, R))
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
    return R

  @not_closed
  def do(self, rq_type, flags=0, payload=b'', decode_response=None, channel=0):
    ''' Synchronous request.
        Submits the request, then calls the `Result` returned from the request.
    '''
    R = self.request(
        rq_type,
        flags=flags,
        payload=payload,
        decode_response=decode_response,
        channel=channel
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
        result = handler(rq_type, flags, payload)
        if result is None:
          # no meaningful result - return the default (0,b'')
          pass
        else:
          if isinstance(result, int):
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

  def end_requests(self):
    ''' Queue the magic end-of-requests `Packet`.
    '''
    self._sendQ.put(self.ERQ_Packet)

  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
  @logexc
  @pfx_method
  @ensure(lambda self: self._recv is None)
  def _receive_loop(
      self,
      *,
      notify_recv_eof: set,
      runstate: RunState,
      rq_in_progress: Progress,
      rq_out_progress: Progress,
  ):
    ''' Receive packets from upstream, decode into requests and responses.
    '''
    ##XX = self.tick
    for packet in progressbar(
        Packet.scan(self._recv),
        label=f'<= {self.name}',
        units_scale=BINARY_BYTES_SCALE,
        itemlenfunc=self._recv_len_func,
    ):
      if packet == self.EOF_Packet:
        break
      if packet == self.ERQ_Packet:
        self.requests_allowed = False
        continue
      channel = packet.channel
      tag = packet.tag
      flags = packet.flags
      payload = packet.payload
      if packet.is_request:
        # request from upstream client
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
              if rq_type == 0:
                # magic EOF rq_type - must be malformed (!=EOF_Packet)
                error("malformed EOF packet received: %s", packet)
                break
              # normalise rq_type
              rq_type -= 1
              requests[channel].add(tag)
              # queue the work function and track it
              LF = self._later.defer(
                  self._run_request,
                  channel,
                  tag,
                  self.request_handler,
                  rq_type,
                  flags,
                  payload,
              )
              # record the LateFunction and arrange its removal on completion
              self.requests_in_progress.add(LF)
              LF.notify(self.requests_in_progress.remove)
      else:
        # response to a previous request from us
        with Pfx("response[%d:%d]", channel, tag):
          # response: get state of matching pending request, remove state
          try:
            rq_state = self._pending_pop(channel, tag)
          except ValueError as e:
            # no such pending pair - response to unknown request
            error("%d.%d: response to unknown request: %s", channel, tag, e)
          else:
            rq_out_progress += 1  # note completion
            decode_response, R = rq_state
            # first flag is "ok"
            ok = (flags & 0x01) != 0
            flags >>= 1
            payload = packet.payload
            if ok:
              # successful reply
              # return (True, flags, decoded-response)
              if decode_response is None:
                # return payload bytes unchanged
                R.result = (True, flags, payload)
              else:
                # decode payload
                try:
                  result = decode_response(flags, payload)
                except Exception:  # pylint: disable=broad-except
                  R.exc_info = sys.exc_info()
                else:
                  R.result = (True, flags, result)
            else:
              # unsuccessful: return (False, other-flags, payload-bytes)
              R.result = (False, flags, payload)
    # end of received packets: cancel any outstanding requests
    self._pending_cancel()
    # alert any listeners of receive EOF
    for notify in notify_recv_eof:
      notify(self)
    self._recv = None

  # pylint: disable=too-many-branches
  @logexc
  def _send_loop(
      self,
      *,
      rq_in_progress: Progress,
      rq_out_progress: Progress,
  ):
    ''' Send packets upstream.
        Write every packet directly to self._send.
        Flush whenever the queue is empty.

        This runs until either of:
        - the send queue is closed
        - the remote has announced end-of-requests and we have no
          outstanding requests
    '''
    ##XX = self.tick
    with PrePfx("_SEND [%s]", self):
      with post_condition(("_send is None", lambda: self._send is None)):
        fp = self._send
        Q = self._sendQ
        grace = self.packet_grace
        for P in progressbar(
            Q,
            label=f'=> {self.name}',
            units_scale=BINARY_BYTES_SCALE,
            itemlenfunc=len,
        ):
          sig = (P.channel, P.tag, P.is_request)
          if sig in self._sent:
            if P == self.EOF_Packet:
              warning("second send of EOF_Packet")
            elif P == self.ERQ_Packet:
              warning("second send of ERQ_Packet")
            else:
              raise RuntimeError("second send of %s" % (P,))
          self._sent.add(sig)
          if P.is_request:
            rq_out_progress.total += 1  # note new sent rq
          else:
            rq_in_progress += 1  # note we completed a rq
          try:
            ##XX(b'>')
            P.write(fp)
            if Q.empty():
              # no immediately ready further packets: flush the output buffer
              if grace > 0:
                # allow a little time for further Packets to queue
                ##XX(b'Sg')
                sleep(grace)
                if Q.empty():
                  # still nothing, flush
                  ##XX(b'F')
                  fp.flush()
              else:
                # no grace period, flush immediately
                ##XX(b'F')
                fp.flush()
          except OSError as e:
            if e.errno == errno.EPIPE:
              warning("remote end closed")
              break
            raise
          if not self.requests_allowed and not self.requests_in_progress:
            # all requests completed, no new ones allowed
            break
        # send EOF packet to remote receiver and close self._send
        try:
          ##XX(b'>EOF')
          self.EOF_Packet.write(self._send, flush=True)
        except (OSError, IOError) as e:
          if e.errno == errno.EPIPE:
            debug("remote end closed: %s", e)
          elif e.errno == errno.EBADF:
            warning("local end closed: %s", e)
          else:
            raise
        except Exception as e:
          error("(_SEND) UNEXPECTED EXCEPTION: %s %s", e, e.__class__)
          raise
        self._send = None

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('cs.stream_tests')
