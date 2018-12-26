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
from cs.logutils import info, warning
from cs.pfx import Pfx
from cs.py.func import prop
from cs.queues import MultiOpenMixin
from cs.resources import RunStateMixin
from cs.socketutils import OpenSocket
from cs.x import X
from . import defaults
from .stream import StreamStore

class _SocketStoreServer(MultiOpenMixin, RunStateMixin):
  ''' The basis for TCPStoreServer and UNIXSocketStoreServer.
  '''

  def __init__(self, *, exports=None, runstate=None, local_store=None):
    ''' Initialise the server.

        Parameters:
        * `exports`: optional mapping of str=>Store
        * `runstate`: option control RunState
        * `local_store`: optional default Store

        `exports` is a mapping of Stores which this server may present;
        the default Store has key `''`.

        If `local_store` is not None, `exports['']` is set to
        `local_store` otherwise to `defaults.S`.
        It is an error to provide both `local_store` and a prefilled
        `exports['']`.
    '''
    if exports is None:
      exports = {}
    if local_store is not None:
      if '' in exports:
        raise ValueError(
            "both local_store=%s and exports['']=%s provided"
            % (local_store, exports['']))
      exports[''] = local_store
    if '' not in exports:
      exports[''] = defaults.S
    MultiOpenMixin.__init__(self)
    RunStateMixin.__init__(self, runstate=runstate)
    self.exports = exports
    self.S = exports['']
    self.socket_server = None
    self.socket_server_thread = None
    self.runstate.notify_start.add(lambda rs: self.open())
    self.runstate.notify_end.add(lambda rs: self.close())
    self.runstate.notify_cancel.add(lambda rs: self.shutdown_now())

  def __str__(self):
    return "%s[%s](S=%s)" % (type(self), self.runstate.state, self.S)

  def startup(self):
    ''' Start up the server.
    '''
    self.socket_server_thread = Thread(
        name="%s(%s)[server-thread]" % (type(self), self.S),
        target=self.socket_server.serve_forever,
        kwargs={'poll_interval': 0.5})
    self.socket_server_thread.daemon = False
    self.socket_server_thread.start()

  def shutdown(self):
    ''' Shut down the server.
    '''
    if self.socket_server:
      self.socket_server.shutdown()
    self.socket_server_thread.join()
    self.socket_server = None

  def shutdown_now(self):
    ''' Issue closes until all current opens have been consumed.
    '''
    if self.socket_server:
      self.socket_server.shutdown()
      self.socket_server = None

  def flush(self):
    ''' Flush the backing Store.
    '''
    self.S.flush()

  def join(self):
    ''' Wait for the server thread to exit.
    '''
    self.socket_server_thread.join()

class _ClientConnectionHandler(StreamRequestHandler):
  ''' Handler for a connection to the server.
  '''

  def setup(self):
    super().setup()
    self.store_server = self.server.store_server

  @prop
  def exports(self):
    ''' Return the exports mapping.
    '''
    return self.store_server.exports

  @logexc
  def handle(self):
    # the local Store starts as the current default Store
    self.S = self.store_server.S
    remoteS = StreamStore(
        "server-StreamStore(local=%s)" % self.S,
        OpenSocket(self.request, False),
        OpenSocket(self.request, True),
        local_store=self.S,
        exports=self.exports,
    )
    remoteS.startup()
    remoteS.join()
    remoteS.shutdown()

class _TCPServer(ThreadingMixIn, TCPServer):

  def __init__(self, store_server, bind_addr):
    with Pfx(
        "%s.__init__(store_server=%s, bind_addr=%r)",
        type(self).__name__, store_server, bind_addr
    ):
      TCPServer.__init__(self, bind_addr, _ClientConnectionHandler)
      self.bind_addr = bind_addr
      self.store_server = store_server

  def __str__(self):
    return "%s(%s,%s)" % (type(self), self.bind_addr, self.store_server,)

class TCPStoreServer(_SocketStoreServer):
  ''' A threading TCPServer that accepts connections from TCPClientStores.
  '''

  def __init__(self, bind_addr, **kw):
    super().__init__(**kw)
    self.bind_addr = bind_addr
    self.socket_server = _TCPServer(self, bind_addr)

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
      self.sock = None

  def _tcp_connect(self):
    with Pfx("connect %r", self.sock_bind_addr):
      X("TCP CONNECT to %r ...", self.sock_bind_addr)
      if self.sock:
        warning("self.sock already set, leaking: %s", self.sock)
      # TODO: IPv6 support
      self.sock = socket(AF_INET)
      try:
        self.sock.connect(self.sock_bind_addr)
      except:
        self.sock.close()
        self.sock = None
        raise
      X("TCP CONNECT to %r: CONNECTED", self.sock_bind_addr)
      return OpenSocket(self.sock, False), OpenSocket(self.sock, True)

  @logexc
  def _packet_disconnect(self, conn):
    ''' On disconnect, close the socket as well.
    '''
    super()._packet_disconnect(conn)
    sock = self.sock
    if sock:
      self.sock = None
      sock.close()

class _UNIXSocketServer(ThreadingMixIn, UnixStreamServer):

  def __init__(self, store_server, socket_path, exports=None):
    with Pfx(
        "%s.__init__(store_server=%s, socket_path=%r)",
        type(self), store_server, socket_path
    ):
      UnixStreamServer.__init__(self, socket_path, _ClientConnectionHandler)
      self.store_server = store_server
      self.socket_path = socket_path
      self.exports = exports

  def __str__(self):
    return "%s(store_server=%s,socket_path=%s,exports=%r)" \
        % (type(self), self.store_server, self.socket_path, self.exports)

class UNIXSocketStoreServer(_SocketStoreServer):
  ''' A threading UnixStreamServer that accepts connections from UNIXSocketClientStores.
  '''

  def __init__(self, socket_path, **kw):
    super().__init__(**kw)
    self.socket_path = socket_path
    self.socket_server = _UNIXSocketServer(self, socket_path)

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
    with Pfx("connect %r", self.socket_path):
      info("UNIX SOCKET CONNECT to %r", self.socket_path)
      assert not self.sock, "self.sock=%s" % (self.sock,)
      self.sock = socket(AF_UNIX)
      with Pfx("connect(%r)", self.socket_path):
        try:
          self.sock.connect(self.socket_path)
        except OSError:
          self.sock.close()
          self.sock = None
          raise
      return OpenSocket(self.sock, False), OpenSocket(self.sock, True)

if __name__ == '__main__':
  from .socket_tests import selftest
  selftest(sys.argv)
