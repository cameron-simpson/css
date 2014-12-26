#!/usr/bin/python
#
# MetaProxy: content rewriting agressive cache web proxy toolkit.
#   - Cameron Simpson <cs@zip.com.au> 26dec2014
#
# Design:
#  local squid:
#       optional: redirect or rewrite specific domains to domain.metaproxy,
#         often http: for local caching and speed
#       divert *.metaproxy directly to trusted remote squid
#
#  remote-personal-squid
#       divert (.metaproxy to local remote-personal-metaproxy
#
#  remote-personal-metaproxy
#       rewrite rq URL to target URL, often https:
#       fetch, parse, return (usually cachable) content
#
# TODO:
#   cookie translation: domain<->domain.metaproxy
#

from __future__ import print_function
import sys
import os
import os.path
try:
  import socketserver
except ImportError:
  import SocketServer
from cs.logutils import setup_logging, warning, D, X
from cs.later import Later

USAGE = '''Usage: %s address:port'''

DEFAULT_PORT = 3128
DEFAULT_PARALLEL = 4

def main(argv):
  cmd = os.path.basename(argv.pop(0))
  usage = USAGE % (cmd,)
  setup_logging(cmd)
  badopts = False
  if not argv:
    warning("missing address:port")
    badopts = True
  else:
    addrport = argv.pop(0)
    try:
      addr, port = addrport.rsplit(':', 1)
    except ValueError:
      warning("invalid address:port, no colon: %r", addrport)
      badopts = True
    else:
      try:
        port = int(port)
      except ValueError:
        warning("invalid address:port, port must be an integer: %r", addrport)
        badopts = True
  if argv:
    warning("extra arguments after address:port: %r", argv)
    badopts = True
  if badopts:
    print(usage, file=sys.stderr)
    return 2
  P = MetaProxy(addr, port)
  X("P = %s", P)
  return 0

class MetaProxy(SocketServer.TCPServer):

  def __init__(self, addr, port=DEFAULT_PORT, parallel=DEFAULT_PARALLEL):
    SocketServer.TCPServer.__init__(
            self,
            (addr, port),
            lambda: MetaProxyHandler(self))
    self.later = Later(parallel)

class MetaProxyHandler(SocketServer.BaseRequestHandler):
  ''' Request handler class for MetaProxy TCPServers.
  '''

  def __init__(self, proxy):
    self.proxy = proxy

  def handle(self):
    rqf = self.request

  def _handle(self, rqf):
    ''' Handle the request coming in on the TCP socket `rqf`.
    '''

if __name__ == '__main__':
  sys.exit(main(sys.argv))
