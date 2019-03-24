#!/usr/bin/env python3
#
# Functions for running a server.
# - Cameron Simpson <cs@cskk.id.au> 30may2018
#

''' Server stub functions.
'''

from .socket import TCPStoreServer, UNIXSocketStoreServer

def serve_tcp(bind_addr, exports=None, runstate=None):
  ''' Return an open TCPStoreServer.
  '''
  srv = TCPStoreServer(bind_addr, exports=exports, runstate=runstate)
  srv.open()
  return srv

def serve_socket(socket_path, exports=None, runstate=None):
  ''' Return an open UNIXSocketStoreServer.
  '''
  srv = UNIXSocketStoreServer(socket_path, exports=exports, runstate=runstate)
  srv.open()
  return srv
