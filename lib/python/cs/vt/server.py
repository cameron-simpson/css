#!/usr/bin/env python3
#
# Shim functions for running a server.
# - Cameron Simpson <cs@cskk.id.au> 30may2018
#

''' Server shim functions.
'''

from .socket import TCPStoreServer, UNIXSocketStoreServer

def serve_tcp(**kw):
  ''' Return an open `TCPStoreServer`.
  '''
  srv = TCPStoreServer(**kw)
  srv.open()
  return srv

def serve_socket(**kw):
  ''' Return an open `UNIXSocketStoreServer`.
  '''
  srv = UNIXSocketStoreServer(**kw)
  srv.open()
  return srv
