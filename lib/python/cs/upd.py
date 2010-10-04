from __future__ import with_statement
import threading
from contextlib import contextmanager
import atexit
import logging
from logging import StreamHandler
from cs.logutils import Pfx
from cs.lex import unctrl       ##, tabpadding

active=False

instances=[]

def cleanupAtExit():
  global instances
  for i in instances:
    i.close()
  instances=()

atexit.register(cleanupAtExit)

class UpdHandler(StreamHandler):

  def __init__(self, strm=None, nlLevel=None):
    if strm is None:
      strm = sys.stderr
    if nlLevel is None:
      nlLevel = logging.WARNING
    StreamHandler.__init__(self, strm)
    self.__upd = Upd(strm)
    self.__nlLevel = nlLevel

  def emit(self, logrec):
    if logrec.lvl >= self.__nlLevel:
      with self.__upd._withoutContext():
        StreamHandler.emit(self, logrec)
    else:
      self.__upd.out(logrec.msg % args)

  def flush(self):
    self.__upd._backend.flush()

class Upd(object):

  def __init__(self, backend, mode=None):
    assert backend is not None
    self._lock=threading.RLock()
    self._backend=backend
    self._state=''
    global active, instances
    instances.append(self)
    active=True

  @property
  def state(self):
    return self._state

  def out(self, txt, noStrip=False):
    with self._lock:
      old=self._state
      if not noStrip:
        txt=txt.rstrip()
      txt=unctrl(txt)

      txtlen=len(txt)
      buflen=len(self._state)
      pfxlen=min(txtlen, buflen)
      for i in range(pfxlen):
        if txt[i] != self._state[i]:
          pfxlen=i
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
        ##patch+=tabpadding(extlen,offset=txtlen)
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
      self._backend=None

  def closed(self):
    return self._backend == None

  def without(self, func, *args, **kw):
    if 'noStrip' in kw:
      noStrip=kw['noStrip']
      del kw['noStrip']
    else:
      noStrip=False
    with self._withoutContext(noStrip):
      ret=func(*args, **kw)
    return ret

  @contextmanager
  def _withoutContext(self,noStrip=False):
    with self._lock:
      old=self.out('', noStrip=noStrip)
      yield
      self.out(old, noStrip=True)

@contextmanager
def __dummyNoUpd():
  yield

def NoUpd(U=None):
  ''' Return a context manager to disable the current Upd for the duration.
  '''
  if U is None:
    if _defaultUpd is None:
      return __dummyNoUpd()
    U=_defaultUpd
  return U._withoutContext()

def _summarise_exception(exc_value):
  summary = str(exc_value)
  if len(summary) == 0:
    summary = repr(exc_value)
  return summary

class ShortExceptions(object):
  ''' Wrapper to catch exceptions and abort with a short error message.
      This is really only intended for use as an outermost wrapper for a main
      program to produce more user friendly messages.
      Setting cs.misc.debug passes exceptions out unharmed, eg:
        DEBUG=1 the-program ...
  '''
  def __enter__(self):
    pass
  def __exit__(self, exc_type, exc_value, tb):
    if exc_type is None \
    or exc_type is SystemExit \
    or exc_type is AssertionError:
      return False
    import cs.misc
    if cs.misc.debug:
      return False
    import sys
    print >>sys.stderr, _summarise_exception(exc_value)
    sys.exit(1)
