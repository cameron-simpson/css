#!/usr/bin/python
#
# Convenience facilities for streams.
# - Cameron Simpson <cs@cskk.id.au> 21aug2015
#

''' A general purpose bidirectional packet stream connection.
'''

from collections import namedtuple
import errno
import os
import sys
from time import sleep
from threading import Lock
from cs.binary import SimpleBinary, BSUInt, BSData
from cs.buffer import CornuCopyBuffer
from cs.excutils import logexc
from cs.later import Later
from cs.logutils import debug, warning, error, exception
from cs.pfx import Pfx, PrePfx, pfx_method
from cs.predicate import post_condition
from cs.queues import IterableQueue
from cs.resources import not_closed, ClosedError
from cs.result import Result
from cs.seq import seq, Seq
from cs.threads import locked, bg as bg_thread

def tick_fd_2(bs):
  ''' A low level tick function to write a short binary tick
      to the standard error file descriptor.

      This may be called by the send and receive workers to give
      an indication of activity type.
  '''
  os.write(2, bs)

__version__ = '20211208-post'

DISTINFO = {
    'description':
    "general purpose bidirectional packet stream connection",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        "Topic :: System :: Networking",
    ],
    'install_requires': [
        'cs.binary',
        'cs.buffer',
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
    ''' Parse a packet from a buffer.
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
        self.payload
    ]
    length = sum(len(bs) for bs in bss)
    # spit out a BSData manually to avoid pointless bytes.join
    yield BSUInt.transcribe_value(length)
    yield bss

Request_State = namedtuple('RequestState', 'decode_response result')

# pylint: disable=too-many-instance-attributes
class PacketConnection(object):
  ''' A bidirectional binary connection for exchanging requests and responses.
  '''

  # special packet indicating end of stream
  EOF_Packet = Packet(
      is_request=True, channel=0, tag=0, flags=0, rq_type=0, payload=b''
  )

  # pylint: disable=too-many-arguments
  def __init__(
      self,
      recv,
      send,
      request_handler=None,
      name=None,
      packet_grace=None,
      tick=None
  ):
    ''' Initialise the PacketConnection.

        Parameters:
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
    if isinstance(recv, int):
      self._recv = CornuCopyBuffer.from_fd(recv)
    elif isinstance(recv, CornuCopyBuffer):
      self._recv = recv
    else:
      self._recv = CornuCopyBuffer.from_file(recv)
    if isinstance(send, int):
      self._send = os.fdopen(os.dup(send), 'wb')
    else:
      self._send = send
    if packet_grace is None:
      packet_grace = DEFAULT_PACKET_GRACE
    if tick is None:
      tick = lambda bs: None
    elif isinstance(tick, bool):
      if tick:
        tick = tick_fd_2
      else:
        tick = lambda bs: None
    self.packet_grace = packet_grace
    self.request_handler = request_handler
    self.tick = tick
    # tags of requests in play against the local system
    self._channel_request_tags = {0: set()}
    self.notify_recv_eof = set()
    self.notify_send_eof = set()
    # LateFunctions for the requests we are performing for the remote system
    self._running = set()
    # requests we have outstanding against the remote system
    self._pending = {0: {}}
    # sequence of tag numbers
    # TODO: later, reuse old tags to prevent monotonic growth of tag field
    self._tag_seq = Seq(1)
    # work queue for local requests
    self._later = Later(4, name="%s:Later" % (self,))
    self._later.open()
    # dispatch queue of Packets to send
    self._sendQ = IterableQueue(16)
    self._lock = Lock()
    self.closed = False
    # debugging: check for reuse of (channel,tag) etc
    self.__sent = set()
    self.__send_queued = set()
    # dispatch Thread to process received packets
    self._recv_thread = bg_thread(
        self._receive_loop, name="%s[_receive_loop]" % (self.name,)
    )
    # dispatch Thread to send data
    # primary purpose is to bundle output by deferring flushes
    self._send_thread = bg_thread(
        self._send_loop, name="%s[_send]" % (self.name,)
    )

  def __str__(self):
    return "PacketConnection[%s]" % (self.name,)

  @pfx_method
  def shutdown(self, block=False):
    ''' Shut down the PacketConnection, optionally blocking for outstanding requests.

        Parameters:
        `block`: block for outstanding requests, default False.
    '''
    with self._lock:
      if self.closed:
        # shutdown already called from another thread
        return
      # prevent further request submission either local or remote
      self.closed = True
    ps = self._pending_states()
    if ps:
      warning("PENDING STATES AT SHUTDOWN: %r", ps)
    # wait for completion of requests we're performing
    for LF in list(self._running):
      LF.join()
    # shut down sender, should trigger shutdown of remote receiver
    self._sendQ.close(enforce_final_close=True)
    self._send_thread.join()
    # we do not wait for the receiver - anyone hanging on outstaning
    # requests will get them as they come in, and in theory a network
    # disconnect might leave the receiver hanging anyway
    self._later.close()
    if block:
      self._later.wait()

  def join(self):
    ''' Wait for the receive side of the connection to terminate.
    '''
    self._recv_thread.join()

  def _new_tag(self):
    return next(self._tag_seq)

  def _pending_states(self):
    ''' Return a list of ( (channel, tag), Request_State ) for the currently pending requests.
    '''
    states = []
    pending = self._pending
    for channel, channel_states in sorted(pending.items()):
      for tag, channel_state in sorted(channel_states.items()):
        states.append(((channel, tag), channel_state))
    return states

  @locked
  def _pending_add(self, channel, tag, state):
    ''' Record some state against a (channel, tag).
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
    ''' Retrieve and remove the state associated with (channel, tag).
    '''
    pending = self._pending
    if channel not in pending:
      raise ValueError("unknown channel %d" % (channel,))
    channel_info = pending[channel]
    if tag not in channel_info:
      raise ValueError("tag %d unknown in channel %d" % (tag, channel))
    if False and tag == 15:
      raise RuntimeError("BANG")
    return channel_info.pop(tag)

  def _pending_cancel(self):
    ''' Cancel all the pending requests.
    '''
    for chtag, _ in self._pending_states():
      channel, tag = chtag
      warning("%s: cancel pending request %d:%s", self, channel, tag)
      _, result = self._pending_pop(channel, tag)
      result.cancel()

  def _queue_packet(self, P):
    sig = (P.channel, P.tag, P.is_request)
    if sig in self.__send_queued:
      raise RuntimeError("requeue of %s: %s" % (sig, P))
    self.__send_queued.add(sig)
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
    self._queue_packet(
        Packet(
            is_request=False,
            channel=channel,
            tag=tag,
            flags=0,
            payload=payload
        )
    )

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
      self, rq_type, flags=0, payload=b'', decode_response=None, channel=0
  ):
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
    if rq_type < 0:
      raise ValueError("rq_type may not be negative (%s)" % (rq_type,))
    # reserve type 0 for end-of-requests
    rq_type += 1
    tag = self._new_tag()
    R = Result()
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
  def do(self, *a, **kw):
    ''' Synchronous request.
        Submits the request, then calls the `Result` returned from the request.
    '''
    return self.request(*a, **kw)()

  @logexc
  # pylint: disable=too-many-arguments
  def _run_request(self, channel, tag, handler, rq_type, flags, payload):
    ''' Run a request and queue a response packet.
    '''
    with Pfx(
        "_run_request[channel=%d,tag=%d,rq_type=%d,flags=0x%02x,payload=%s",
        channel, tag, rq_type, flags,
        repr(payload) if len(payload) <= 32 else repr(payload[:32]) + '...'):
      result_flags = 0
      result_payload = b''
      try:
        result = handler(rq_type, flags, payload)
        if result is not None:
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
      self._channel_request_tags[channel].remove(tag)

  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
  def _receive_loop(self):
    ''' Receive packets from upstream, decode into requests and responses.
    '''
    XX = self.tick
    with PrePfx("_RECEIVE [%s]", self):
      with post_condition(("_recv is None", lambda: self._recv is None)):
        while True:
          try:
            XX(b'<')
            packet = Packet.parse(self._recv)
          except EOFError:
            break
          if packet == self.EOF_Packet:
            break
          channel = packet.channel
          tag = packet.tag
          flags = packet.flags
          payload = packet.payload
          if packet.is_request:
            # request from upstream client
            with Pfx("request[%d:%d]", channel, tag):
              if self.closed:
                debug("rejecting request: closed")
                # NB: no rejection packet sent since sender also closed
              elif self.request_handler is None:
                self._reject(channel, tag, "no request handler")
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
                      self._run_request, channel, tag, self.request_handler,
                      rq_type, flags, payload
                  )
                  self._running.add(LF)
                  LF.notify(self._running.remove)
          else:
            with Pfx("response[%d:%d]", channel, tag):
              # response: get state of matching pending request, remove state
              try:
                rq_state = self._pending_pop(channel, tag)
              except ValueError as e:
                # no such pending pair - response to unknown request
                error(
                    "%d.%d: response to unknown request: %s", channel, tag, e
                )
              else:
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
        for notify in self.notify_recv_eof:
          notify(self)
        self._recv = None
        self.shutdown()

  # pylint: disable=too-many-branches
  def _send_loop(self):
    ''' Send packets upstream.
        Write every packet directly to self._send.
        Flush whenever the queue is empty.
    '''
    XX = self.tick
    ##with Pfx("%s._send", self):
    with PrePfx("_SEND [%s]", self):
      with post_condition(("_send is None", lambda: self._send is None)):
        fp = self._send
        Q = self._sendQ
        grace = self.packet_grace
        for P in Q:
          sig = (P.channel, P.tag, P.is_request)
          if sig in self.__sent:
            raise RuntimeError("second send of %s" % (P,))
          self.__sent.add(sig)
          try:
            XX(b'>')
            for bs in P.transcribe_flat():
              fp.write(bs)
            if Q.empty():
              # no immediately ready further packets: flush the output buffer
              if grace > 0:
                # allow a little time for further Packets to queue
                XX(b'Sg')
                sleep(grace)
                if Q.empty():
                  # still nothing
                  XX(b'F')
                  fp.flush()
              else:
                XX(b'F')
                fp.flush()
          except OSError as e:
            if e.errno == errno.EPIPE:
              warning("remote end closed")
              break
            raise
        try:
          XX(b'>EOF')
          for bs in self.EOF_Packet.transcribe_flat():
            fp.write(bs)
          fp.close()
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
