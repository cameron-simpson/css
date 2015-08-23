#!/usr/bin/python -tt
#
# TCP client/server code.
#       - Cameron Simpson <cs@zip.com.au> 07dec2007
#

import os
from socket import socket, SHUT_WR, SHUT_RD
from socketserver import TCPServer, ThreadingMixIn, StreamRequestHandler
from .stream import StreamStore
from cs.logutils import debug
from cs.queues import NestingOpenCloseMixin

class OpenSock(object):
  ''' A file-like object for stream sockets, which uses os.shutdown on close.
  '''

  def __init__(self, sock, for_write):
    self._for_write = for_write
    self._sock = sock
    self._fd = os.dup(sock.fileno)
    self._fp = os.fdopen(self._fd, 'wb' if for_write else 'rb')

  def write(self, data):
    return self._fp.write(data)

  def read(self, size=None):
    return self._fp.read(size)

  def flush(self):
    self._fp.flush()

  def close(self):
    if self._for_write:
      os.shutdown(self._sock, os.SHUT_WR)
    else:
      os.shutdown(self._sock, os.SHUT_RD)

class TCPStoreServer(ThreadingMixIn, TCPServer, NestingOpenCloseMixin):
  ''' A threading TCPServer that accepts connections by TCPStoreClients.
  '''

  def __init__(self, bind_addr, S):
    ThreadingTCPServer.__init__(self, bind_addr, _RequestHandler)
    S.open()
    self.S = S

  def shutdown(self):
    self.S.close()

class _RequestHandler(StreamRequestHandler):

  def __init__(self, request, client_address, server):
    self.S = server.S
    StreamRequestHandler.__init__(self, request, client_address, server)

  def handle(self):
    RS = StreamStore(str(self.S),
                     OpenSock(self.request, False),
                     OpenSock(self.request, True),
                     local_store=self.S,
                    )
    RS.join()
    RS.shutdown()

class TCPStoreClient(StreamStore):
  ''' A Store attached to a remote Store at `bind_addr`.
  '''

  def __init__(self, bind_addr):
    self.sock = socket()
    self.sock.connect(bind_addr)
    StreamStore.__init__(self,
                         "TCPStore(%s)" % (bind_addr,),
                         OpenSock(self.sock, False),
                         OpenSock(self.sock, True),
                        )

  def shutdown(self):
    StreamStore.shutdown(self)
    self.sock.close()
