#!/usr/bin/python -tt
#
# TCP client/server code.
#       - Cameron Simpson <cs@zip.com.au> 07dec2007
#

import os
from socket import socket, SHUT_WR, SHUT_RD
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
    SD.start()
    debug("tcp.handle: waiting for StreamDaemon.resultsThread")
    SD.join()
    debug("tcp.handle: waited for StreamDaemon.resultsThread")
    self.rfile.close()
    self.wfile.close()
    debug("tcp.handle: closed connections to client")

class TCPStore(StreamStore):
  def __init__(self,bindaddr):
    self.sock=socket()
    self.sock.connect(bindaddr)
    self.fd=self.sock.fileno()
    self.fd2=os.dup(self.fd)
    StreamStore.__init__(self,"TCPStore(%s)"%(bindaddr,),os.fdopen(self.fd,'wb'),os.fdopen(self.fd2,'rb'))
