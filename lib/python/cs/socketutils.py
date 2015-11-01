#!/usr/bin/python
#
# Miscellaneous things to do with sockets.
#   - Cameron Simpson <cs@zip.com.au> 28oct2015
#

import os
import sys
import errno
import socket
from cs.logutils import X, XP, Pfx, warning

def bind_next_port(sock, host, base_port):
  ''' Bind a the socket `sock` to the first free (`host`, port); return the port.
      `base_port`: the first port number to try.
  '''
  while True:
    try:
      sock.bind( (host, base_port) )
    except OSError as e:
      if e.errno == errno.EADDRINUSE:
        base_port += 1
      else:
        raise
    else:
      return base_port

class OpenSocket(object):
  ''' A file-like object for stream sockets, which uses os.shutdown on close.
  '''

  def __init__(self, sock, for_write):
    self._for_write = for_write
    self._sock = sock
    self._fd0 = self._sock.fileno()
    self._fd = os.dup(self._fd0)
    self._fp = os.fdopen(self._fd, 'wb' if for_write else 'rb')

  def write(self, data):
    return self._fp.write(data)

  def read(self, size=None):
    return self._fp.read(size)

  def flush(self):
    return self._fp.flush()

  def close(self):
    with Pfx("OpenSocket.close[fd0=%d,fd=%d]", self._fd0, self._fd):
      if self._sock is not None:
        try:
          if self._for_write:
            XP("_sock.shutdown(SHUT_WR)")
            self._sock.shutdown(socket.SHUT_WR)
          else:
            XP("_sock.shutdown(SHUT_RD)")
            self._sock.shutdown(socket.SHUT_RD)
        except OSError as e:
          if e.errno == errno.EBADF:
            warning("socket on fd %d already closed: %s", self._fd, e)
          elif e.errno != errno.ENOTCONN:
            raise
        self._close()

  def __del__(self):
    self._close()

  def _close(self):
    if self._fp:
      self._fp.close()
      self._fp = None
      self._sock = None

  def selfcheck(self):
    st1 = os.fstat(self._fd)
    st2 = os.fstat(self._fd0)
    st3 = os.fstat(self._sock)
    if s1 != s2:
      raise ValueError("fstat mismatch s1!=s2 (%s, %s)" % (s1, s2))
    if s1 != s3:
      raise ValueError("fstat mismatch s1!=s3 (%s, %s)" % (s1, s3))

if __name__ == '__main__':
  import cs.socketutils_tests
  cs.socketutils_tests.selftest(sys.argv)
