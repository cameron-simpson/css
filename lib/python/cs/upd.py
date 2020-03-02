#!/usr/bin/python
#
# Single line status updates.
#   - Cameron Simpson <cs@cskk.id.au>

r'''
Single line status updates with minimal update sequences.

This is available as an output mode in `cs.logutils`.

Example:

    with Upd() as U:
        for filename in filenames:
            U.out(filename)
            ... process filename ...
            upd.nl('an informational line')
'''

from __future__ import with_statement
import atexit
from contextlib import contextmanager
from threading import RLock
from cs.lex import unctrl
from cs.tty import ttysize

__version__ = '20200229'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.lex', 'cs.tty'],
}

instances = []
instances_by_id = {}

def cleanupAtExit():
  ''' Cleanup function called at programme exit to clear the status line.
  '''
  global instances
  global instances_by_id
  for i in instances:
    i.close()
  instances = ()
  instances_by_id = {}

atexit.register(cleanupAtExit)

class Upd(object):
  ''' A class for maintaining a regularly updated status line.
  '''

  def __init__(self, backend, columns=None):
    assert backend is not None
    if columns is None:
      columns = 80
      if backend.isatty():
        rc = ttysize(backend)
        if rc.columns is not None:
          columns = rc.columns
    self._backend = backend
    self.columns = columns
    self._state = ''
    self._above = None
    self._lock = RLock()
    global instances
    instances.append(self)

  def __enter__(self):
    return self

  def __exit__(self, exc_type, *_):
    ''' Tidy up on exiting the context.

        If we are exiting because of an exception and the status
        line is not empty, output a newline to preserve the status
        line on the screen.  Otherwise just clear the status line.
    '''
    if self._state:
      if exc_type:
        self._backend.write('\n')
      else:
        self.out('')

  def close(self):
    ''' Close this Upd.
    '''
    if self._backend is not None:
      self.out('')
      self._backend = None

  def closed(self):
    ''' Test whether this Upd is closed.
    '''
    return self._backend is None

  @property
  def state(self):
    ''' The current status line text value.
    '''
    return self._state

  def out(self, txt, *a):
    ''' Update the status line to `txt`.
        Return the previous status line content.

        Parameters:
        * `txt`: the status line text.
        * `a`: optional positional parameters;
          if not empty, `txt` is percent formatted against this list.
    '''
    if a:
      txt = txt % a
    # normalise text
    txt = txt.rstrip()
    txt = unctrl(txt)
    # crop for terminal width
    txtlen = len(txt)
    if txtlen >= self.columns:
      txt = txt[:self.columns - 1]
      txtlen = len(txt)
    with self._lock:
      old = self._state
      buflen = len(old)
      pfxlen = min(txtlen, buflen)
      # compute length of common prefix
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
      if buflen - pfxlen < 1 + pfxlen:
        # backspace and partial overwrite
        self._backend.write('\b' * (buflen - pfxlen))
        self._backend.write(txt[pfxlen:])
      else:
        # carriage return and complete overwrite
        self._backend.write('\r')
        self._backend.write(txt)
      # trailing text to overwrite with spaces?
      extlen = buflen - txtlen
      if extlen > 0:
        # old line was longer - write spaces over the old tail
        self._backend.write(' ' * extlen)
        self._backend.write('\b' * extlen)

      self._backend.flush()
      self._state = txt

    return old

  def nl(self, txt, *a, raw=False):
    ''' Write `txt` to the backend followed by a newline.

        Parameters:
        * `txt`: the message to write.
        * `a`: optional positional parameters;
          if not empty, `txt` is percent formatted against this list.
        * `raw`: if true (default `False`) use the "clear, newline,
          restore" method.

        This uses one of two methods:
        * insert above:
          insert a line above the status line and write the message there.
        * clear, newline, restore:
          clears the status line, writes the text line, restores
          the status line.

        The former method is used if the terminal supports the
        `il1` (insert one line) capability;
        this are probed for on the first use and remembered.
    '''
    if a:
      txt = txt % a
    if raw or len(txt) >= self.columns:
      # force a clear-newline-restore method
      above = False
    else:
      # try to insert the output above the status line
      above = self._above
      if above is None:
        try:
          import curses
        except ImportError:
          above = False
        else:
          curses.setupterm()
          il1 = curses.tigetstr('il1')
          if il1:
            above = ((il1 + b'\r').decode(), '\n')
          else:
            above = False
        self._above = above
    if above:
      with self._lock:
        self._backend.write(above[0] + txt + above[1] + self._state)
        self._backend.flush()
    else:
      with self.without():
        with self._lock:
          self._backend.write(txt + '\n')

  def flush(self):
    ''' Flush the output stream.
    '''
    if self._backend:
      self._backend.flush()

  @contextmanager
  def without(self, temp_state=''):
    ''' Context manager to clear the status line around a suite.
        Returns the status line text as it was outside the suite.

        The `temp_state` parameter may be used to set the inner status line
        content if a value other than `''` is desired.
    '''
    with self._lock:
      old = self.out(temp_state)
      try:
        yield old
      finally:
        self.out(old)

# make Upd the singleton factory
_Upd = Upd

def Upd(stream):
  ''' Factory for `Upd` singletons keyed by the id of their backend.
  '''
  global instances_by_id
  U = instances_by_id.get(id(stream))
  if not U:
    U = _Upd(stream)
    instances_by_id[id(stream)] = U
  return U

upd_for = Upd
