#!/usr/bin/python -tt
#
# TCP and UNIX socket client/server code.
# - Cameron Simpson <cs@cskk.id.au> 07dec2007
#

''' Support for connections over TCP and UNIX domain sockets.
'''

import os
from socket import socket, AF_INET, AF_UNIX
from socketserver import TCPServer, UnixStreamServer, \
    ThreadingMixIn, StreamRequestHandler
import sys
from threading import Thread
from cs.excutils import logexc
from cs.logutils import info, exception
from cs.pfx import Pfx
from cs.py.func import prop
from cs.queues import MultiOpenMixin
from cs.resources import RunStateMixin
from cs.socketutils import OpenSocket
from cs.x import X
from . import defaults
from .stream import StreamStore

class _SocketStoreServer(MultiOpenMixin, RunStateMixin):
  ''' A threading TCPServer that accepts connections from TCPClientStores.
  '''

  def __init__(self, *, exports=None, runstate=None):
    ''' Initialise the server.
    '''
    if exports is None:
      exports = {}
    elif not exports:
      raise ValueError("empty exports: %r" % (exports,))
    if '' not in exports:
      exports[''] = defaults.S
    MultiOpenMixin.__init__(self)
    RunStateMixin.__init__(self, runstate=runstate)
    self.exports = exports
    self.S = exports['']
    self.server = None
    self.server_thread = None
    self.runstate.notify_start.add(lambda rs: self.open())
    self.runstate.notify_end.add(lambda rs: self.close())
    self.runstate.notify_cancel.add(lambda rs: self.shutdown_now())

  def __str__(self):
    return "%s[%s](S=%s)" % (type(self), self.runstate.state, self.S)

  def startup(self):
    ''' Start up the server.
    '''
    self.S.open()
    self.server_thread = Thread(
        name="%s(%s)[server-thread]" % (type(self), self.S),
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

  def shutdown_now(self):
    ''' Issue closes until all current opens have been consumed.
    '''
    while not self.closed:
      self.close()

  def flush(self):
    ''' Flush the backing Store.
    '''
    self.S.flush()

  def join(self):
    ''' Wait for the server thread to exit.
    '''
    self.server_thread.join()

  def switch_to(self, export_name):
    ''' Switch the backend Store to one of the exports.
    '''
    newS = self.exports[export_name]
    if newS is not self.S:
      oldS = self.S
      newS.open()
      self.S = newS
      oldS.close()

class _ClientConnectionHandler(StreamRequestHandler):
  ''' Handler for a connection the the server.
  '''

  @logexc
  def __init__(self, request, client_address, server):
    ''' Initialise the handler for a stream from a connection.
        `request`: the connected stream file descriptor
        `client_address`: the peer address
        `server`: the controlling server, a _TCPServer or _UNIXSocketServer
    '''
    X("CONNECTION on %s from %s, server=%s", request, client_address, server)
    super().__init__(request, client_address, server)
    self.server = server

  @prop
  def S(self):
    ''' Return the current Store.
    '''
    return self.server.srv.S

  @prop
  def exports(self):
    ''' Return the exports mapping.
    '''
    return self.server.srv.exports

  @logexc
  def handle(self):
    remoteS = StreamStore(
        "server-StreamStore(local=%s)" % self.S,
        OpenSocket(self.request, False),
        OpenSocket(self.request, True),
        local_store=self.S,
        exports=self.exports,
    )
    remoteS.startup()
    self.server.handlers.add(remoteS)
    remoteS.join()
    remoteS.shutdown()
    self.server.handlers.remove(remoteS)

class _TCPServer(ThreadingMixIn, TCPServer):

  def __init__(self, srv, bind_addr):
    with Pfx("%s.__init__(srv=%s, bind_addr=%r)", type(self), srv, bind_addr):
      TCPServer.__init__(self, bind_addr, _ClientConnectionHandler)
      self.bind_addr = bind_addr
      self.srv = srv
      self.handlers = set()

  def __str__(self):
    return "%s(%s,%s)" % (type(self), self.bind_addr, self.srv,)

class TCPStoreServer(_SocketStoreServer):
  ''' A threading TCPServer that accepts connections from TCPClientStores.
  '''

  def __init__(self, bind_addr, **kw):
    super().__init__(**kw)
    self.bind_addr = bind_addr
    self.server = _TCPServer(self, bind_addr)

class TCPClientStore(StreamStore):
  ''' A Store attached to a remote Store at `bind_addr`.
  '''

  def __init__(self, name, bind_addr, addif=False, **kw):
    if name is None:
      name = "%s(bind_addr=%r)" % (self.__class__.__name__, bind_addr)
    self.sock_bind_addr = bind_addr
    self.sock = None
    StreamStore.__init__(
        self, name, None, None,
        addif=addif, connect=self._tcp_connect,
        **kw
    )

  def shutdown(self):
    StreamStore.shutdown(self)
    if self.sock:
      self.sock.close()

  def _tcp_connect(self):
    info("TCP CONNECT to %r", self.sock_bind_addr)
    assert not self.sock, "self.sock=%s" % (self.sock,)
    # TODO: IPv6 support
    self.sock = socket(AF_INET)
    try:
      self.sock.connect(self.sock_bind_addr)
    except OSError:
      self.sock.close()
      self.sock = None
      raise
    return OpenSocket(self.sock, False), OpenSocket(self.sock, True)

class _UNIXSocketServer(ThreadingMixIn, UnixStreamServer):

  def __init__(self, srv, socket_path, exports=None):
    with Pfx("%s.__init__(srv, socket_path=%r)", type(self), srv, socket_path):
      UnixStreamServer.__init__(self, socket_path, _ClientConnectionHandler)
      self.srv = srv
      self.socket_path = socket_path
      self.exports = exports
      self.handlers = set()

  def __str__(self):
    return "%s(srv=%s,socket_path=%s,exports=%r)" % (type(self), self.srv, self.socket_path, self.exports)

class UNIXSocketStoreServer(_SocketStoreServer):
  ''' A threading UnixStreamServer that accepts connections from UNIXSocketClientStores.
  '''

  def __init__(self, socket_path, **kw):
    super().__init__(**kw)
    self.socket_path = socket_path
    self.server = _UNIXSocketServer(self, socket_path)

  def shutdown(self):
    super().shutdown()
    os.remove(self.socket_path)

class UNIXSocketClientStore(StreamStore):
  ''' A Store attached to a remote Store at `socket_path`.
  '''

  def __init__(self, name, socket_path, addif=False, **kw):
    if name is None:
      name = "%s(socket_path=%r)" % (self.__class__.__name__, socket_path)
    self.socket_path = socket_path
    self.sock = None
    StreamStore.__init__(
        self, name, None, None,
        addif=addif, connect=self._unixsock_connect,
        **kw
    )

  def shutdown(self):
    StreamStore.shutdown(self)
    if self.sock:
      self.sock.close()

  def _unixsock_connect(self):
    info("UNIX SOCKET CONNECT to %r", self.socket_path)
    assert not self.sock, "self.sock=%s" % (self.sock,)
    self.sock = socket(AF_UNIX)
    with Pfx("connect(%r)", self.socket_path):
      try:
        self.sock.connect(self.socket_path)
      except OSError as e:
        exception("%s.connect(%r): %s", self.sock, self.socket_path, e)
        self.sock.close()
        self.sock = None
        raise
    return OpenSocket(self.sock, False), OpenSocket(self.sock, True)

if __name__ == '__main__':
  from .socket_tests import selftest
  selftest(sys.argv)
