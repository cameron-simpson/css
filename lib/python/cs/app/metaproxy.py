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
from email.parser import BytesFeedParser
from itertools import takewhile
try:
  import socketserver
except ImportError:
  import SocketServer as socketserver
from cs.excutils import LogExceptions
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
  #P.handle_request()
  P.serve_forever()
  return 0

class MetaProxy(socketserver.TCPServer):

  def __init__(self, addr, port=DEFAULT_PORT, parallel=DEFAULT_PARALLEL):
    self.allow_reuse_address = True
    socketserver.TCPServer.__init__(
            self,
            (addr, port),
            MetaProxyHandler
          )
    self.later = Later(parallel)
    self.later.open()
    self.name = "MetaProxy(addr=%s,port=%s)" % (addr, port)

  def __str__(self):
    return self.name

  def shutdown(self):
    self.later.close()
    socketserver.TCPServer.shutdown(self)

  def process_request(self, rqf, client_addr):
    X("process_request(...,client_addr=%s", client_addr)
    handler = self.RequestHandlerClass(rqf, client_addr, self)
    X("process_request: handler = %s", handler)
    self.later.defer(self._process_request, handler, rqf, client_addr)
    X("process_request: self._process_request defered")

  def _process_request(self, handler, rqf, client_addr):
    X("self._process_request...")
    with LogExceptions():
      try:
        handler.handle(rqf, client_addr)
        self.shutdown_request(rqf)
      except:
        self.handle_error(rqf, client_addr)
        self.shutdown_request(rqf)

class MetaProxyHandler(socketserver.BaseRequestHandler):
  ''' Request handler class for MetaProxy TCPServers.
  '''

  def __init__(self, sockobj, localaddr, proxy):
    socketserver.BaseRequestHandler(sockobj, localaddr, proxy)
    self.proxy = proxy
    X("self = %r %r", self, self.__dict__)
    X("%r", dir(self))

  def handle(self, rqf, client_addr):
    X("MetaProxyHandler.handle: rqf = %r", rqf)
    fpin = os.fdopen(rqf.fileno(), "rb")
    fpout = os.fdopen(rqf.fileno(), "wb")
    httprq = fpin.readline().decode('iso8859-1')
    X("httprq = %r", httprq)
    method, uri, version = httprq.split()
    X("uri = %r <%s>", uri, type(uri))
    uri_scheme, uri_tail = uri.split(':', 1)
    parser = BytesFeedParser()
    is_header_line = lambda line: line.startswith(b' ') or line.startswith(b'\t') or line.rstrip()
    parser.feed( b''.join( takewhile( is_header_line, fpin ) ) )
    M = parser.close()
    X("M = %r", M)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
