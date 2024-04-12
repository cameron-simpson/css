#!/usr/bin/env python3 -tt
#
# TCP and UNIX socket client/server code.
# - Cameron Simpson <cs@cskk.id.au> 07dec2007
#

''' Support for connections over TCP and UNIX domain sockets.
'''

from contextlib import contextmanager
import os
from os.path import exists as existspath
from socket import socket, AF_INET, AF_UNIX
from socketserver import (
    TCPServer, UnixStreamServer, ThreadingMixIn, StreamRequestHandler
)

from icontract import require

from cs.context import stackattrs
from cs.excutils import logexc
from cs.pfx import Pfx
from cs.py.func import prop
from cs.queues import MultiOpenMixin
from cs.resources import RunStateMixin
from cs.socketutils import OpenSocket
from cs.threads import bg as bg_thread, joinif

from . import Store, uses_Store
from .stream import StreamStore

class _SocketStoreServer(MultiOpenMixin, RunStateMixin):
  ''' The basis for TCPStoreServer and UNIXSocketStoreServer.
  '''

  # if exports supplied, may not contain '' if local_store supplied
  @uses_Store
  @require(
      lambda exports, local_store:
      (local_store is None or not exports or '' not in exports)
  )
  def __init__(
      self, *, S: Store, exports=None, runstate=None, local_store=None
  ):
    ''' Initialise the server.

        Parameters:
        * `exports`: optional mapping of str=>Store
        * `runstate`: option control RunState
        * `local_store`: optional default Store

        `exports` is a mapping of Stores which this server may present;
        the default Store has key `''`.

        If `local_store` is not None, `exports['']` is set to
        `local_store` otherwise to `Store.defaults()`.
        It is an error to provide both `local_store` and a prefilled
        `exports['']`.
    '''
    if exports is None:
      exports = {}
    if local_store is not None:
      exports[''] = local_store
    if '' not in exports:
      exports[''] = S
    RunStateMixin.__init__(self, runstate=runstate)
    self.exports = exports
    self.S = exports['']
    self.socket_server = None
    self.socket_server_thread = None
    self.runstate.notify_start.add(lambda rs: self.open())
    self.runstate.notify_end.add(lambda rs: self.close(unopened_ok=True))
    self.runstate.notify_cancel.add(lambda rs: self.shutdown_now())

  def __str__(self):
    return "%s[%s](S=%s)" % (type(self).__name__, self.runstate.state, self.S)

  @contextmanager
  def startup_shutdown(self):
    ''' Start up the server.
        Subclasses' `startup_shutdown` must setup and clear `self.socker_server`
        around a call to this method.
    '''
    with super().startup_shutdown():
      with stackattrs(
          self,
          socket_server_thread=bg_thread(
              self.socket_server.serve_forever,
              kwargs={'poll_interval': 0.5},
              name="%s(%s)[server-thread]" % (type(self), self.S),
              daemon=False,
          ),
      ):
        try:
          yield
        finally:
          if self.socket_server:
            self.socket_server.shutdown()
          joinif(self.socket_server_thread)
          if self.socket_server and self.socket_server.socket is not None:
            self.socket_server.socket.close()

  def shutdown_now(self):
    ''' Issue closes until all current opens have been consumed.
    '''
    if self.socket_server:
      self.socket_server.shutdown()
      if self.socket_server.socket is not None:
        self.socket_server.socket.close()

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
        (
            OpenSocket(self.request, False),
            OpenSocket(self.request, True),
        ),
        local_store=self.S,
        exports=self.exports,
    )
    remoteS.open()
    remoteS.join()
    remoteS.close()

class _TCPServer(ThreadingMixIn, TCPServer):

  def __init__(self, store_server, bind_addr):
    with Pfx("%s.__init__(store_server=%s, bind_addr=%r)", type(self).__name__,
             store_server, bind_addr):
      TCPServer.__init__(self, bind_addr, _ClientConnectionHandler)
      self.bind_addr = bind_addr
      self.store_server = store_server

  def __str__(self):
    return "%s(%r,%s)" % (
        type(self).__name__,
        self.bind_addr,
        self.store_server,
    )

class TCPStoreServer(_SocketStoreServer):
  ''' A threading TCPServer that accepts connections from TCPClientStores.
  '''

  def __init__(self, bind_addr, **kw):
    super().__init__(**kw)
    self.bind_addr = bind_addr
    self.socket_server = None

  @contextmanager
  def startup_shutdown(self):
    with stackattrs(
        self,
        socket_server=_TCPServer(self, self.bind_addr),
    ):
      try:
        with super().startup_shutdown():
          yield
      finally:
        self.socket_server.shutdown()

class TCPClientStore(StreamStore):
  ''' A Store attached to a remote Store at `bind_addr`.
  '''

  def __init__(self, name, bind_addr, addif=False, **kw):
    if name is None:
      name = "%s(bind_addr=%r)" % (self.__class__.__name__, bind_addr)
    self.sock_bind_addr = bind_addr
    self.sock = None
    StreamStore.__init__(self, name, self._tcp_connect, addif=addif, **kw)

  @contextmanager
  def startup_shutdown(self):
    try:
      with super().startup_shutdown():
        yield
    finally:
      if self.sock is not None:
        self.sock.close()
        self.sock = None

  @contextmanager
  @require(lambda self: not self.sock)
  def _tcp_connect(self):
    # TODO: IPv6 support
    self.sock = socket(AF_INET)
    try:
      with Pfx("%s.sock.connect(%r)", self, self.sock_bind_addr):
        self.sock.connect(self.sock_bind_addr)
      yield OpenSocket(self.sock, False), OpenSocket(self.sock, True)
    finally:
      self.sock.close()
      self.sock = None

class _UNIXSocketServer(ThreadingMixIn, UnixStreamServer):

  def __init__(self, store_server, socket_path, exports=None):
    with Pfx("%s.__init__(store_server=%s, socket_path=%r)", type(self),
             store_server, socket_path):
      UnixStreamServer.__init__(self, socket_path, _ClientConnectionHandler)
      self.store_server = store_server
      self.socket_path = socket_path
      self.exports = exports

  def __str__(self):
    return "%s(store_server=%s,socket_path=%r,exports=%r)" \
        % (type(self).__name__, self.store_server, self.socket_path, self.exports)

class UNIXSocketStoreServer(_SocketStoreServer):
  ''' A threading `UnixStreamServer` that accepts connections from `UNIXSocketClientStore`s.
  '''

  def __init__(self, socket_path, **kw):
    super().__init__(**kw)
    self.socket_path = socket_path
    self.socket_server = None

  @contextmanager
  def startup_shutdown(self):
    if existspath(self.socket_path):
      raise RuntimeError("socket already exists: %r" % (self.socket_path,))
    with stackattrs(
        self,
        socket_server=_UNIXSocketServer(self, self.socket_path),
    ):
      try:
        with super().startup_shutdown():
          yield
      finally:
        self.socket_server.shutdown()
        os.remove(self.socket_path)

class UNIXSocketClientStore(StreamStore):
  ''' A Store attached to a remote Store at `socket_path`.
  '''

  def __init__(self, name, socket_path, addif=False, **kw):
    if name is None:
      name = "%s(socket_path=%r)" % (self.__class__.__name__, socket_path)
    self.socket_path = socket_path
    self.sock = None
    StreamStore.__init__(self, name, self._unixsock_connect, addif=addif, **kw)

  @contextmanager
  def startup_shutdown(self):
    try:
      with super().startup_shutdown():
        yield
    finally:
      if self.sock is not None:
        self.sock.close()
        self.sock = None

  @contextmanager
  @require(lambda self: not self.sock)
  def _unixsock_connect(self):
    self.sock = socket(AF_UNIX)
    try:
      with Pfx("%s.sock.connect(%r)", self, self.socket_path):
        self.sock.connect(self.socket_path)
        yield OpenSocket(self.sock, False), OpenSocket(self.sock, True)
    finally:
      if self.sock is not None:
        self.sock.close()
        self.sock = None
