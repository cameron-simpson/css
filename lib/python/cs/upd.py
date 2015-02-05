#!/usr/bin/python
#
# Single line status updates.
#   - Cameron Simpson <cs@zip.com.au>
#

from __future__ import with_statement

DISTINFO = {
    'description': "single line status updates with minimal update sequences",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'requires': ['cs.lex', 'cs.tty'],
}

from threading import Lock
import threading
from contextlib import contextmanager
import atexit
import logging
from logging import StreamHandler
from cs.lex import unctrl
from cs.tty import ttysize

instances = []

def cleanupAtExit():
  global instances
  for i in instances:
    i.close()
  instances = ()

atexit.register(cleanupAtExit)

class Upd(object):

  def __init__(self, backend, columns=None):
    assert backend is not None
    if columns is None:
      columns = 80
      if backend.isatty():
        rc = ttysize(backend)
        if rc.columns is not None:
          columns = rc.columns
    self._backend=backend
    self.columns = columns
    self._state = ''
    self._lock = threading.RLock()
    global instances
    instances.append(self)

  @property
  def state(self):
    return self._state

  def out(self, txt, noStrip=False):
    if not noStrip:
      txt = txt.rstrip()
    txt = unctrl(txt)
    if self.columns is not None:
      txt = txt[:self.columns-1]
    txtlen = len(txt)
    with self._lock:
      old = self._state
      buflen = len(old)
      pfxlen = min(txtlen, buflen)
      for i in range(pfxlen):
        if txt[i] != old[i]:
          pfxlen = i
          break

      # Rewrites take one of two forms:
      #   Backspace to end of common prefix, overwrite with the differing tail
      #     of the new string, erase trailing extent if any.
      #   Return to start of line with carriage return, overwrite with new
      #    string, erase trailing extent if any.
      # Therefore compare backspaces against cr+pfxlen.
      #
      if buflen-pfxlen < 1+pfxlen:
        # backspace and partial overwrite
        self._backend.write( '\b' * (buflen-pfxlen) )
        self._backend.write( txt[pfxlen:] )
      else:
        # carriage return and complete overwrite
        self._backend.write('\r')
        self._backend.write(txt)
        extlen = buflen-txtlen
        if extlen > 0:
          # old line was longer - write spaces over the old tail
          self._backend.write( ' ' * extlen )
          self._backend.write( '\b' * extlen )

      self._backend.flush()
      self._state = txt

    return old

  def nl(self, txt, noStrip=False):
    self.without(self._backend.write, txt+'\n', noStrip=noStrip)

  def close(self):
    if self._backend is not None:
      self.out('')
      self._backend = None

  def closed(self):
    return self._backend == None

  def without(self, func, *args, **kw):
    if 'noStrip' in kw:
      noStrip = kw['noStrip']
      del kw['noStrip']
    else:
      noStrip = False
    with self._withoutContext(noStrip):
      ret = func(*args, **kw)
    return ret

  @contextmanager
  def _withoutContext(self,noStrip=False):
    with self._lock:
      old = self.out('', noStrip=noStrip)
      yield
      self.out(old, noStrip=True)
