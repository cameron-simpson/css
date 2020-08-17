#!/usr/bin/python
#
# Miscellaneous things to do with sockets.
#   - Cameron Simpson <cs@cskk.id.au> 28oct2015
#

''' Utility functions and classes for sockets.
'''

import os
import errno
import socket
from cs.logutils import debug, warning
from cs.pfx import Pfx

DISTINFO = {
    'description': "some utilities for network sockets",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        "Topic :: System :: Networking",
    ],
    'install_requires': ['cs.logutils', 'cs.pfx'],
}

def bind_next_port(sock, host, base_port):
  ''' Bind the socket `sock` to the first free `(host,port)`; return the port.

      Parameters:
      * `sock`: open socket.
      * `host`: target host address.
      * `base_port`: the first port number to try.
  '''
  while True:
    try:
      sock.bind( (host, base_port) )
    except (OSError, socket.error) as e:
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
    try:
      read = self._fp.read1
    except AttributeError:
      read = self._fp.read
    self.read = read

  def __str__(self):
    return "%s[fd=%d,fd0=%d]" % (type(self).__name__, self._fd, self._fd0)

  def write(self, data):
    ''' Write to the socket.
    '''
    return self._fp.write(data)

  def flush(self):
    ''' Flush any buffered data to the socket.
    '''
    return self._fp.flush()

  def close(self):
    ''' Close the socket.
    '''
    with Pfx("%s.close", self):
      if self._sock is None:
        ##warning("close when _sock=None")
        return
      if self._for_write:
        shut_mode = socket.SHUT_WR
        shut_mode_s = 'SHUT_WR'
        self.flush()
      else:
        shut_mode = socket.SHUT_RD
        shut_mode_s = 'SHUT_RD'
      with Pfx("_sock.shutdown(%s)", shut_mode_s):
        try:
          self._sock.shutdown(shut_mode)
        except (socket.error, OSError) as e:
          if e.errno == errno.ENOTCONN:
            # client end went away
            ##info("%s", e)
            pass
          elif e.errno == errno.EBADF:
            debug("%s", e)
          else:
            raise
        finally:
          self._close()

  def __del__(self):
    self._close()

  def _close(self):
    if self._fp:
      try:
        self._fp.close()
      except OSError as e:
        if e.errno == errno.EPIPE:
          warning("%s: %s.close: %s", self, self._fp, e)
        else:
          raise
      self._fp = None
      self._sock = None

  def selfcheck(self):
    ''' Perform an internal self check.
    '''
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
