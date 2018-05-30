#!/usr/bin/env python3
#
# Functions for running a server.
# - Cameron Simpson <cs@cskk.id.au> 30may2018
#

from .socket import TCPStoreServer, UNIXSocketStoreServer

def serve_tcp(S, bind_addr, runstate=None):
  ''' Return an open TCPStoreServer.
  '''
  srv = TCPStoreServer(S, bind_addr, runstate=runstate)
  srv.open()
  return srv

def serve_socket(S, socket_path, runstate=None):
  ''' Return an open UNIXSocketStoreServer.
  '''
  srv = UNIXSocketStoreServer(S, socket_path, runstate=runstate)
  srv.open()
  return srv
