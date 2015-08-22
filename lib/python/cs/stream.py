#!/usr/bin/python
#
# Convenience facilities for streams.
#       - Cameron Simpson <cs@zip.com.au> 21aug2015
#

from collections import namedtuple
from threading import Thread
from cs.asynchron import Result
from cs.later import Later
from cs.logutils import Pfx, warning, error, X
from cs.queues import IterableQueue
from cs.resources import not_closed
from cs.seq import seq, Seq
from cs.serialise import Packet, read_Packet, write_Packet

Request_State = namedtuple('RequestState', 'decode_response result')

class PacketConnection(object):
  ''' A bidirectional binary connection for exchanging requests and responses.
  '''

  def __init__(self, recv_fp, send_fp, decode_request=None, name=None):
    ''' Initialise the PacketConnection.
        `recv_fp`: inbound binary stream
        `send_fp`: outbound binary stream
        `decode_request`: if supplied and not None, a callable to
            decode an inbound request packet into a local callable

        The callable returned from decode_request may itself return one of 4 values:
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
    self.decode_request = decode_request
    # requests in play against the local system
    self._channel_requests = {0: set()}
    # requests we have outstanding against the remote system
    self._pending = {0: {}}
    # sequence of tag numbers
    # TODO: later, reuse old tags to prevent montonic growth of tag field
    self._tag_seq = Seq()
    # work queue for local requests
    self._later = Later(4)
    # dispatch queue for packets to send - bytes objects
    self._sendQ = IterableQueue(16)
    # dispatch Thread to process received packets
    self._recv_thread = Thread(target=self._receive)
    self._recv_thread.daemon = True
    self._recv_thread.start()
    # dispatch Thread to send data
    # primary purpose is to bundle output by deferring flushes
    # otherwise we might just send synchronously
    self._send_thread = Thread(target=self._send)
    self._send_thread.daemon = True
    self._send_thread.start()

  def _new_tag(self):
    return next(self._tag_seq)

  def _reject(self, channel, tag):
    ''' Issue a rejection of the specified request.
    '''
    P = Packet(channel, tag, False, 0, bytes(()))
    self._sendQ.put(P)

  def _respond(self, channel, tag, flags, payload):
    ''' Issue a valid response.
        Tack a 1 (ok) flag onto the flags and dispatch.
    '''
    flags = (flags<<1) | 1
    P = Packet(channel, tag, False, flags, payload)
    self._sendQ.put(P)

  def request(self, flags, payload, decode_response_payload, channel=0):
    ''' Compose and dispatch a new request.
        Allocates a new tag, a Result to deliver the response, and
        records the response decode function for use when the
        response arrives.
    '''
    tag = self._new_tag()
    R = Result()
    pending = self._pending
    if channel not in pending:
      raise ValueError("invalid channel %d", channel)
    pending[channel][tag] = Request_State(decode_response_payload, R)
    self._send_request(channel, tag, flags, payload)
    return R

  def _send_request(self, channel, tag, flags, payload):
    ''' Issue a request.
    '''
    P = Packet(channel, tag, True, flags, payload)
    self._sendQ.put(P)

  def _receive(self):
    ''' Receive packets from upstream, decode into requests and responses.
    '''
    fp = self._recv_fp
    while True:
      packet = read_Packet(fp)
      channel = packet.channel
      tag = packet.tag
      flags = packet.flags
      if packet.is_request:
        with Pfx("request[%d:%d]", channel, tag):
          if self._decode_request is None:
            error("rejecting request: no self._decode_request")
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
              # payload for requests are the request enum and data
              try:
                run_rq = self.decode_request(flags, payload)
              except ValueError as e:
                error("invalid request received: %s", e)
                self._reject(channel, tag)
              else:
                requests[channel].add(tag)
                def _run_request():
                  try:
                    result_flags = 0
                    result_payload = bytes(())
                    result = run_rq()
                    if result is not None:
                      if isinstance(result, int):
                        result_flags = result
                      elif isinstance(result, bytes):
                        result_payload = result
                      else:
                        result_flags, result_payload = result
                    flags, payload = run_rq()
                  except Exception as e:
                    warning("%s: exception: %s", run, e)
                    self._reject(channel, tag)
                  else:
                    self._respond(channel, tag, result_flags, result_payload)
                  requests[channel].remove(tag)
                self._later.defer(self._run_request)
          raise RuntimeError("NOT REACHED")
      else:
        with Pfx("response[%d:%d]", channel, tag):
          # response: get state of matching pending request, remove from _pending
          rq_state = self._pending.get(channel, {}).pop(tag, None)
          if rq_state is None:
            # no such pending pair - response to unknown request
            error("%d.%d: response to unknown request", channel, tag)
          else:
            decode_response, R = rq_state
            # first flag is "ok"
            ok = (flags & 0x01) != 0
            flags >>= 1
            if not ok:
              R.raise_(ValueError("respond not ok: ok=%s, flags=%s, payload=%r",
                                  ok, flags, payload))
            else:
              try:
                result = decode_response(flags, payload)
              except Exception as e:
                R.exc_info = sys.exc_info()
              else:
                R.result = result

  def _send(self):
    ''' Send packets upstream.
        Write every packet directly to self._send_fp.
        Flush whenever the queue is empty.
    '''
    fp = self._send_fp
    Q = self._sendQ
    for P in Q:
      write_Packet(fp, P)
      if Q.empty():
        fp.flush()
