#!/usr/bin/python -tt
#
# TCP client/server code.
#       - Cameron Simpson <cs@cskk.id.au> 07dec2007
#

from socket import socket
from socketserver import TCPServer, ThreadingMixIn, StreamRequestHandler
import sys
from threading import Thread
from .stream import StreamStore
from cs.excutils import logexc
from cs.logutils import info
from cs.pfx import Pfx
from cs.queues import MultiOpenMixin
from cs.socketutils import OpenSocket

class _Server(ThreadingMixIn, TCPServer):

  def __init__(self, bind_addr, S):
    with Pfx("%s.__init__(bind_addr=%r, S=%s)", self.__class__, bind_addr, S):
      TCPServer.__init__(self, bind_addr, _RequestHandler)
      self.bind_addr = bind_addr
      self.S = S
      self.handlers = set()

  def __str__(self):
    return "TCPStoreServer:_Server(%s,%s)" % (self.bind_addr, self.S,)

class TCPStoreServer(MultiOpenMixin):
  ''' A threading TCPServer that accepts connections from TCPStoreClients.
  '''

  def __init__(self, bind_addr, S):
    self.bind_addr = bind_addr
    self.S = S
    self.server = _Server(bind_addr, S)
    MultiOpenMixin.__init__(self)

  def __str__(self):
    return "TCPStoreServer(%s,S=%s)" % (self.bind_addr, self.S)

  def startup(self):
    self.S.open()
    self.T = Thread(
        name="%s[server-thread]" % (self,),
        target=self.server.serve_forever,
        kwargs={'poll_interval': 0.5})
    self.T.daemon = True
    self.T.start()

  def shutdown(self):
    self.server.shutdown()
    self.T.join()
    self.S.close()

  def flush(self):
    self.S.flush()

  def join(self):
    ''' Wait for the server thread to exit.
    '''
    self.T.join()

  def cancel(self):
    ''' Shut down the server thread.
        TODO: shutdown handler threads.
    '''
    self.server.shutdown()

class _RequestHandler(StreamRequestHandler):

  @logexc
  def __init__(self, request, client_address, server):
    self.server = server
    self.S = server.S
    StreamRequestHandler.__init__(self, request, client_address, server)

  @logexc
  def handle(self):
    RS = StreamStore("server-StreamStore(local=%s)" % self.S,
                     OpenSocket(self.request, False),
                     OpenSocket(self.request, True),
                     local_store=self.S,
                    )
    RS.startup()
    self.server.handlers.add(RS)
    RS.join()
    RS.shutdown()
    self.server.handlers.remove(RS)

class TCPStoreClient(StreamStore):
  ''' A Store attached to a remote Store at `bind_addr`.
  '''

  def __init__(self, name, bind_addr, addif=False):
    if name is None:
      name = "%s(bind_addr=%r)" % (self.__class__.__name__, bind_addr)
    self.sock_bind_addr = bind_addr
    self.sock = None
    StreamStore.__init__(
        self, name, None, None,
        addif=addif, connect=self._tcp_connect
    )

  def shutdown(self):
    StreamStore.shutdown(self)
    if self.sock:
      self.sock.close()

  def _tcp_connect(self):
    info("TCP CONNECT to %r", self.sock_bind_addr)
    assert not self.sock, "self.sock=%s" % (self.sock,)
    self.sock = socket()
    try:
      self.sock.connect(self.sock_bind_addr)
    except OSError:
      self.sock.close()
      self.sock = None
      raise
    return OpenSocket(self.sock, False), OpenSocket(self.sock, True)

if __name__ == '__main__':
  from .tcp_tests import selftest
  selftest(sys.argv)
