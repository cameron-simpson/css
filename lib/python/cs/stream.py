#!/usr/bin/python
#
# Convenience facilities for streams.
#       - Cameron Simpson <cs@zip.com.au> 21aug2015
#

import sys
from collections import namedtuple
from threading import Thread, Lock
from cs.asynchron import Result
from cs.excutils import logexc
from cs.later import Later
from cs.logutils import Pfx, warning, error, X, XP
from cs.queues import IterableQueue
from cs.resources import not_closed
from cs.seq import seq, Seq
from cs.serialise import Packet, read_Packet, write_Packet, put_bs, get_bs
from cs.threads import locked

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
        The request_handler may return one of 4 values:
          None  Respond will be 0 flags and an empty payload.
          int   Flags only. Response will be the flags and an empty payload.
          bytes Payload only. Response with be 0 flags and the payload.
          (int, bytes) Specify flags and payload for response.
    '''
    if name is None:
      name = str(seq())
    self.name = name
    self._recv_fp = recv_fp
    self._send_fp = send_fp
    self.request_handler = request_handler
    # requests in play against the local system
    self._channel_requests = {0: set()}
    # requests we have outstanding against the remote system
    self._pending = {0: {}}
    # sequence of tag numbers
    # TODO: later, reuse old tags to prevent montonic growth of tag field
    self._tag_seq = Seq()
    # work queue for local requests
    self._later = Later(4)
    self._later.open()
    # dispatch queue for packets to send - bytes objects
    self._sendQ = IterableQueue(16)
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
    self._lock = Lock()
    self.__sent = set()
    self.__send_queued = set()

  def __str__(self):
    return "PacketConnection[%s,closed=%s]" % (self.name, self.closed)

  def shutdown(self):
    self.closed = True
    if not self._sendQ.closed:
      self._sendQ.close()
    self._send_thread.join()
    self._send_fp = None
    self._recv_thread.join()
    self._recv_fp = None
    self._later.close()

  def join(self):
    ''' Wait for the receive side of the connection to terminate.
    '''
    self._recv_thread.join()

  def _new_tag(self):
    return next(self._tag_seq)

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

  def _queue_packet(self, P):
    sig = (P.channel, P.tag, P.is_request)
    if sig in self.__send_queued:
      raise RuntimeError("requeue of %s: %s" % (sig, P))
    self.__send_queued.add(sig)
    self._sendQ.put(P)

  def _reject(self, channel, tag):
    ''' Issue a rejection of the specified request.
    '''
    P = Packet(channel, tag, False, 0, bytes(()))
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
      warning("exception: %s", e)
      self._reject(channel, tag)
    else:
      self._respond(channel, tag, result_flags, result_payload)
    self._channel_requests[channel].remove(tag)

  @logexc
  def _receive(self):
    ''' Receive packets from upstream, decode into requests and responses.
    '''
    with Pfx("%s._receive", self):
      fp = self._recv_fp
      while True:
        try:
          packet = read_Packet(fp)
        except EOFError:
          break
        channel = packet.channel
        tag = packet.tag
        flags = packet.flags
        payload = packet.payload
        if packet.is_request:
          with Pfx("request[%d:%d]", channel, tag):
            if self.closed:
              error("rejecting request: closed")
              self._reject(channel, tag)
            elif self.request_handler is None:
              error("rejecting request: no self.request_handler")
              self._reject(channel, tag)
            else:
              # request from upstream client
              requests = self._channel_requests
              if channel not in requests:
                # unknown channel
                error("rejecting request: unknown channel %d", channel)
                self._reject(channel, tag)
              elif tag in self._channel_requests[channel]:
                error("rejecting request: channel %d: tag already in use: %d",
                      channel, tag)
                self._reject(channel, tag)
              else:
                # payload for requests is the request enum and data
                try:
                  rq_type, offset = get_bs(payload)
                except IndexError as e:
                  error("invalid request: truncated request type, payload=%r", payload)
                  self._reject(channel, tag)
                else:
                  requests[channel].add(tag)
                  self._later.defer(self._run_request,
                                    channel, tag, self.request_handler,
                                    rq_type, flags, payload[offset:])
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
      if not self._sendQ.closed:
        self._sendQ.close()
      self._recv_fp.close()

  @logexc
  def _send(self):
    ''' Send packets upstream.
        Write every packet directly to self._send_fp.
        Flush whenever the queue is empty.
    '''
    fp = self._send_fp
    Q = self._sendQ
    for P in Q:
      sig = (P.channel, P.tag, P.is_request)
      if sig in self.__sent:
        raise RuntimeError("second send of %s" % (P,))
      self.__sent.add(sig)
      write_Packet(fp, P)
      if Q.empty():
        fp.flush()
    fp.close()

if __name__ == '__main__':
  import sys
  import cs.stream_tests
  cs.stream_tests.selftest(sys.argv)
