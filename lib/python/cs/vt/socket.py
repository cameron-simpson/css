#!/usr/bin/python -tt
#
# TCP and UNIX socket client/server code.
# - Cameron Simpson <cs@cskk.id.au> 07dec2007
#

import os
from socket import socket
from socketserver import TCPServer, UnixStreamServer, ThreadingMixIn, StreamRequestHandler
import sys
from threading import Thread
from cs.excutils import logexc
from cs.logutils import info
from cs.pfx import Pfx
from cs.queues import MultiOpenMixin
from cs.socketutils import OpenSocket
from .stream import StreamStore

class _SocketStoreServer(MultiOpenMixin):
  ''' A threading TCPServer that accepts connections from TCPStoreClients.
  '''

  def __init__(self, S):
    ''' Initialise the server.
    '''
    super().__init__(self)
    self.S = S
    self.server = None
    self.server_thread = None

  def startup(self):
    ''' Start up the server.
    '''
    self.S.open()
    self.server_thread = Thread(
        name="%s[server-thread]" % (self,),
        target=self.server.serve_forever,
        kwargs={'poll_interval': 0.5})
    self.server_thread.daemon = False
    self.server_thread.start()

  def shutdown(self):
    ''' Shut down the server.
    '''
    self.server.shutdown()
    self.server_thread.join()
    self.S.close()

  def flush(self):
    ''' Flush the backing Store.
    '''
    self.S.flush()

  def join(self):
    ''' Wait for the server thread to exit.
    '''
    self.server_thread.join()

  def cancel(self):
    ''' Shut down the server thread.
        TODO: shutdown handler threads.
    '''
    self.server.shutdown()

class _RequestHandler(StreamRequestHandler):

  @logexc
  def __init__(self, request, client_address, server):
    super().__init__(self, request, client_address, server)
    self.server = server
    self.S = server.S

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

class _TCPServer(ThreadingMixIn, TCPServer):

  def __init__(self, bind_addr, S):
    with Pfx("%s.__init__(bind_addr=%r, S=%s)", type(self), bind_addr, S):
      TCPServer.__init__(self, bind_addr, _RequestHandler)
      self.bind_addr = bind_addr
      self.S = S
      self.handlers = set()

  def __str__(self):
    return "%s(%s,%s)" % (type(self), self.bind_addr, self.S,)

class TCPStoreServer(_SocketStoreServer):
  ''' A threading TCPServer that accepts connections from TCPStoreClients.
  '''

  def __init__(self, bind_addr, S):
    super().__init__(S)
    self.bind_addr = bind_addr
    self.server = _TCPServer(bind_addr, S)

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

class _UNIXSocketServer(ThreadingMixIn, UnixStreamServer):

  def __init__(self, socket_path, S):
    with Pfx("%s.__init__(socket_path=%r, S=%s)", type(self), socket_path, S):
      UnixStreamServer.__init__(self, socket_path, _RequestHandler)
      self.socket_path = socket_path
      self.S = S
      self.handlers = set()

  def __str__(self):
    return "%s(%s,%s)" % (type(self), self.socket_path, self.S,)

class UNIXSocketStoreServer(_SocketStoreServer):
  ''' A threading UnixStreamServer that accepts connections from UNIXSocketStoreClients.
  '''

  def __init__(self, socket_path, S):
    super().__init__(S)
    self.socket_path = socket_path
    self.server = _UNIXSocketServer(socket_path, S)

  def shutdown(self):
    super().shutdown()
    os.remove(self.socket_path)

class UNIXSocketStoreClient(StreamStore):
  ''' A Store attached to a remote Store at `socket_path`.
  '''

  def __init__(self, name, socket_path, addif=False):
    if name is None:
      name = "%s(socket_path=%r)" % (self.__class__.__name__, socket_path)
    self.socket_path = socket_path
    self.sock = None
    StreamStore.__init__(
        self, name, None, None,
        addif=addif, connect=self._unixsock_connect
    )

  def shutdown(self):
    StreamStore.shutdown(self)
    if self.sock:
      self.sock.close()

  def _unixsock_connect(self):
    info("UNIX SOCKET CONNECT to %r", self.socket_path)
    assert not self.sock, "self.sock=%s" % (self.sock,)
    self.sock = socket()
    try:
      self.sock.connect(self.socket_path)
    except OSError:
      self.sock.close()
      self.sock = None
      raise
    return OpenSocket(self.sock, False), OpenSocket(self.sock, True)

if __name__ == '__main__':
  from .tcp_tests import selftest
  selftest(sys.argv)
