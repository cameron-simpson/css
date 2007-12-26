#!/usr/bin/python -tt
#
# TCP client/server code.
#       - Cameron Simpson <cs@zip.com.au> 07dec2007
#

import os
from socket import socket
from SocketServer import ThreadingTCPServer, StreamRequestHandler
from cs.venti.stream import StreamDaemon, StreamStore
from cs.misc import debug

class Server(ThreadingTCPServer):
  def __init__(self,bindaddr,S):
    ThreadingTCPServer.__init__(self,bindaddr,_RequestHandler)
    self.S=S

class _RequestHandler(StreamRequestHandler):
  def __init__(self, request, client_address, server):
    self.S=server.S
    StreamRequestHandler.__init__(self, request, client_address, server)

  def handle(self):
    SD=StreamDaemon(self.S,self.rfile,self.wfile)
    debug("tcp.handle: waiting for StreamDaemon.resultsThread")
    SD.resultsThread.join()
    debug("tcp.handle: waited for StreamDaemon.resultsThread")

class TCPStore(StreamStore):
  def __init__(self,bindaddr):
    self.sock=socket()
    self.sock.connect(bindaddr)
    self.fd=self.sock.fileno()
    fd2=os.dup(fd)
    StreamStore.__init__(self,os.fdopen(fd,'wb'),os.fdopen(fd2,'rb'))

  def close(self):
