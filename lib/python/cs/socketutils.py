#!/usr/bin/python
#
# Miscellaneous things to do with sockets.
#   - Cameron Simpson <cs@zip.com.au> 28oct2015
#

import os
import errno
import socket
from cs.logutils import warning, info
from cs.pfx import Pfx
from cs.x import X

def bind_next_port(sock, host, base_port):
  ''' Bind a the socket `sock` to the first free (`host`, port); return the port.
      `base_port`: the first port number to try.
  '''
  while True:
    try:
      sock.bind( (host, base_port) )
    except socket.error as e:
      if e.errno == errno.EADDRINUSE:
        base_port += 1
      else:
        raise
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
    X("OpenSocket: sock=%s, for_write=%s", sock, for_write)
    self._for_write = for_write
    self._sock = sock
    self._fd0 = self._sock.fileno()
    self._fd = os.dup(self._fd0)
    self._fp = os.fdopen(self._fd, 'wb' if for_write else 'rb')
    X("OpenSocket init done")

  def __str__(self):
    return "OpenSocket[fd=%d,fd0=%d]" % (self._fd, self._fd0)

  def write(self, data):
    return self._fp.write(data)

  def read(self, size=None):
    return self._fp.read(size)

  def flush(self):
    return self._fp.flush()

  def close(self):
    with Pfx("%s.close", self):
      if self._sock is not None:
        if self._for_write:
          shut_mode = socket.SHUT_WR
          shut_mode_s = 'SHUT_WR'
        else:
          shut_mode = socket.SHUT_RD
          shut_mode_s = 'SHUT_RD'
        with Pfx("_sock.shutdown(%s)", shut_mode_s):
          try:
            self._sock.shutdown(shut_mode)
          except socket.error as e:
            if e.errno == errno.ENOTCONN:
              info("%s", e)
            elif e.errno == errno.EBADF:
              warning("closed: %s", e)
            else:
              warning("%s", e)
              raise
          except OSError as e:
            if e.errno == errno.EBADF:
              warning("already closed: %s", e)
            elif e.errno == errno.ENOTCONN:
              warning("not connected: %s", e)
            else:
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
    if st1 != st2:
      raise ValueError("fstat mismatch st1!=st2 (%s, %s)" % (st1, st2))
    if st1 != st3:
      raise ValueError("fstat mismatch st1!=st3 (%s, %s)" % (st1, st3))

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('cs.socketutils_tests')
