#!/usr/bin/python
#
# Convenience facilities for streams.
#       - Cameron Simpson <cs@zip.com.au> 21aug2015
#

import sys
import errno
from collections import namedtuple
from threading import Thread, Lock
from cs.asynchron import Result
from cs.excutils import logexc
from cs.later import Later
from cs.logutils import debug, warning, error, exception
from cs.pfx import Pfx, XP, PrePfx
from cs.predicate import post_condition
from cs.py3 import BytesFile, unicode
from cs.queues import IterableQueue
from cs.resources import not_closed, ClosedError
from cs.seq import seq, Seq
from cs.serialise import Packet, read_Packet, write_Packet, put_bs, get_bs
from cs.threads import locked
from cs.x import X

Request_State = namedtuple('RequestState', 'decode_response result')

class PacketConnection(object):
  ''' A bidirectional binary connection for exchanging requests and responses.
  '''

  def __init__(self, recv_fp, send_fp, request_handler=None, name=None):
    ''' Initialise the PacketConnection.
        `recv_fp`: inbound binary stream
        `send_fp`: outbound binary stream
        `request_handler`: if supplied and not None, should be a
            callable accepting (request_type, flags, payload)
        The request_handler may return one of 4 values on success:
          None  Respond will be 0 flags and an empty payload.
          int   Flags only. Response will be the flags and an empty payload.
          bytes Payload only. Response will be 0 flags and the payload.
          (int, bytes) Specify flags and payload for response.
        An unsuccessful request should raise an exception, which
        will cause a failure response packet.
    '''
    if name is None:
      name = str(seq())
    self.name = name
    self._recv_fp = BytesFile(recv_fp)
    self._send_fp = BytesFile(send_fp)
    self.request_handler = request_handler
    # tags of requests in play against the local system
    self._channel_request_tags = {0: set()}
    # LateFunctions for the requests we are performing for the remote system
    self._running = set()
    # requests we have outstanding against the remote system
    self._pending = {0: {}}
    # sequence of tag numbers
    # TODO: later, reuse old tags to prevent montonic growth of tag field
    self._tag_seq = Seq(1)
    # work queue for local requests
    self._later = Later(4, name="%s:Later" % (self,))
    self._later.open()
    # dispatch queue of Packets to send
    self._sendQ = IterableQueue(16)
    self._lock = Lock()
    self.closed = False
    # dispatch Thread to process received packets
    self._recv_thread = Thread(target=self._receive, name="%s[_receive]" % (self.name,))
    self._recv_thread.daemon = True
    self._recv_thread.start()
    # dispatch Thread to send data
    # primary purpose is to bundle output by deferring flushes
    # otherwise we might just send synchronously
    self._send_thread = Thread(target=self._send, name="%s[_send]" % (self.name,))
    self._send_thread.daemon = True
    self._send_thread.start()
    # debugging: check for reuse of (channel,tag) etc
    self.__sent = set()
    self.__send_queued = set()

  def __str__(self):
    return "PacketConnection[%s]" % (self.name,)

  def shutdown(self, block=False):
    ''' Shut down the PacketConnection, optionally blocking for outstanding requests.
        `block`: block for outstanding requests, default False.
    '''
    with Pfx("SHUTDOWN %s", self):
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
      # requests will get them as they come in, and in thoery a network
      # disconnect might leave the receiver hanging anyway
      if block:
        self._later.wait_outstanding(until_idle=True)
        self._later.close(enforce_final_close=True)
        if not self._later.closed:
          raise RuntimeError("%s: ._later not closed! %r", self, self._later)
      else:
        self._later.close()

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
    for channel in sorted(pending.keys()):
      channel_states = pending[channel]
      for tag in sorted(channel_states.keys()):
        states.append( ( (channel, tag), channel_states[tag]) )
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
    for chtag, state in self._pending_states():
      channel, tag = chtag
      warning("%s: cancel pending request %d:%s", self, channel, tag)
      decode_response, result = self._pending_pop(channel, tag)
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
    if isinstance(payload, unicode):
      payload = payload.encode('utf-8')
    P = Packet(channel, tag, False, 0, payload)
    self._queue_packet(P)

  def _respond(self, channel, tag, flags, payload):
    ''' Issue a valid response.
        Tack a 1 (ok) flag onto the flags and dispatch.
    '''
    flags = (flags<<1) | 1
    P = Packet(channel, tag, False, flags, payload)
    self._queue_packet(P)

  @not_closed
  def request(self, rq_type, flags, payload, decode_response, channel=0):
    ''' Compose and dispatch a new request.
        Allocates a new tag, a Result to deliver the response, and
        records the response decode function for use when the
        response arrives.
    '''
    if rq_type < 0:
      raise ValueError("rq_type may not be negative (%s)" % (rq_type,))
    # reserve type 0 for end-of-requests
    rq_type += 1
    tag = self._new_tag()
    R = Result()
    self._pending_add(channel, tag, Request_State(decode_response, R))
    self._send_request(channel, tag, rq_type, flags, payload)
    return R

  def _send_request(self, channel, tag, rq_type, flags, payload):
    ''' Issue a request.
    '''
    P = Packet(channel, tag, True, flags, put_bs(rq_type) + payload)
    self._queue_packet(P)

  def _run_request(self, channel, tag, handler, rq_type, flags, payload):
    ''' Run a request and queue a response packet.
    '''
    with Pfx("_run_request[ch=%d,tag=%d, rq_type=%d,flags=0x%02x,payload=%r",
              channel, tag, rq_type, flags, payload):
      try:
        result_flags = 0
        result_payload = bytes(())
        result = handler(rq_type, flags, payload)
        if result is not None:
          if isinstance(result, int):
            result_flags = result
          elif isinstance(result, bytes):
            result_payload = result
          else:
            result_flags, result_payload = result
      except Exception as e:
        exception("exception: %s", e)
        self._reject(channel, tag, "exception during handler")
      else:
        self._respond(channel, tag, result_flags, result_payload)
      self._channel_request_tags[channel].remove(tag)

  @logexc
  def _receive(self):
    ''' Receive packets from upstream, decode into requests and responses.
    '''
    ##with Pfx("%s._receive", self):
    with PrePfx("_RECEIVE [%s]", self):
      with post_condition( ("_recp_fp is None", lambda: self._recv_fp is None) ):
        while True:
          try:
            packet = read_Packet(self._recv_fp)
          except EOFError:
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
                      "channel %d: tag already in use: %d" % (channel, tag))
                else:
                  # payload for requests is the request enum and data
                  try:
                    rq_type, offset = get_bs(payload)
                  except IndexError as e:
                    error("invalid request: truncated request type, payload=%r", payload)
                    self._reject(
                        channel, tag,
                        "truncated request type, payload=%r" % (payload,))
                  else:
                    if rq_type == 0:
                      # catch magic EOF request: rq_type 0
                      break
                    else:
                      # hide magic request type 0
                      rq_type -= 1
                      requests[channel].add(tag)
                      # queue the work function and track it
                      LF = self._later.defer(self._run_request,
                                             channel, tag, self.request_handler,
                                             rq_type, flags, payload[offset:])
                      self._running.add(LF)
                      LF.notify(lambda LF: self._running.remove(LF))
          else:
            with Pfx("response[%d:%d]", channel, tag):
              # response: get state of matching pending request, remove state
              try:
                rq_state = self._pending_pop(channel, tag)
              except ValueError as e:
                # no such pending pair - response to unknown request
                error("%d.%d: response to unknown request: %s", channel, tag, e)
              else:
                decode_response, R = rq_state
                # first flag is "ok"
                ok = (flags & 0x01) != 0
                flags >>= 1
                payload = packet.payload
                if not ok:
                  R.raise_(ValueError("response not ok: ok=%s, flags=%s, payload=%r"
                                      % (ok, flags, payload)))
                else:
                  try:
                    result = decode_response(flags, payload)
                  except Exception as e:
                    R.exc_info = sys.exc_info()
                  else:
                    R.result = result
        self._pending_cancel()
        with Pfx("_recv_fp.close"):
          try:
            self._recv_fp.close()
          except OSError as e:
            warning("%s.close: %s", self._recv_fp, e)
          except Exception as e:
            error("(_RECV) UNEXPECTED EXCEPTION: %s %s", e, e.__class__)
            raise
        self._recv_fp = None
        self.shutdown()

  @logexc
  def _send(self):
    ''' Send packets upstream.
        Write every packet directly to self._send_fp.
        Flush whenever the queue is empty.
    '''
    ##with Pfx("%s._send", self):
    with PrePfx("_SEND [%s]", self):
      with post_condition( ("_send_fp is None", lambda: self._send_fp is None) ):
        fp = self._send_fp
        Q = self._sendQ
        for P in Q:
          sig = (P.channel, P.tag, P.is_request)
          if sig in self.__sent:
            raise RuntimeError("second send of %s" % (P,))
          self.__sent.add(sig)
          try:
            write_Packet(fp, P)
            if Q.empty():
              fp.flush()
          except OSError as e:
            if e.errno == errno.EPIPE:
              warning("remote end closed")
              break
            raise
        eof_packet = Packet(0, 0, True, 0, put_bs(0))
        try:
          write_Packet(fp, eof_packet)
          fp.close()
        except IOError as e:
          if e.errno == errno.EPIPE:
            debug("remote end closed: %s", e)
          elif e.errno == errno.EBADF:
            warning("local end closed: %s", e)
          else:
            raise
        except OSError as e:
          if e.errno == errno.EPIPE:
            debug("remote end closed: %s", e)
          elif e.errno == errno.EBADF:
            warning("local end closed: %s", e)
          else:
            raise
        except Exception as e:
          error("(_SEND) UNEXPECTED EXCEPTION: %s %s", e, e.__class__)
          raise
        self._send_fp = None

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('cs.stream_tests')
