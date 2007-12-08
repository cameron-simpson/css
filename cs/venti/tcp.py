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
    SD.resultsThread.join()

class TCPStore(StreamStore):
  def __init__(self,bindaddr):
    self.sock=socket()
    self.sock.connect(bindaddr)
    fd=self.sock.fileno()
    StreamStore.__init__(self,os.fdopen(fd,'wb'),os.fdopen(fd,'rb'))
