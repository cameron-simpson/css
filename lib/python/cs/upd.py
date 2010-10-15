from __future__ import with_statement
from thread import allocate_lock
import threading
from contextlib import contextmanager
import atexit
import logging
from logging import StreamHandler
from subprocess import Popen, PIPE
import cs.misc
from cs.logutils import Pfx
from cs.lex import unctrl

instances=[]

def cleanupAtExit():
  global instances
  for i in instances:
    i.close()
  instances=()

atexit.register(cleanupAtExit)

class UpdHandler(StreamHandler):

  def __init__(self, strm=None, nlLevel=None):
    ''' Initialise the UpdHandler.
        `strm` is the output stream, default sys.stderr.
        `nlLevel` is the logging level at which conventional line-of-text
        output is written; log messages of a lower level go via the
        update-the-current-line method. Default is logging.WARNING.
    '''
    if strm is None:
      strm = sys.stderr
    if nlLevel is None:
      nlLevel = logging.WARNING
    StreamHandler.__init__(self, strm)
    self.__upd = Upd(strm)
    self.__nlLevel = nlLevel
    self.__lock = allocate_lock()

  def emit(self, logrec):
    with self.__lock:
      if logrec.levelno >= self.__nlLevel:
        with self.__upd._withoutContext():
          StreamHandler.emit(self, logrec)
      else:
        self.__upd.out(logrec.getMessage())

  def flush(self):
    if self.__upd._backend:
      self.__upd._backend.flush()

class Upd(object):

  def __init__(self, backend, columns=None):
    assert backend is not None
    if columns is None:
      columns = 78
      if backend.isatty():
        P=Popen(['stty', '-a'], stdin=backend, stdout=PIPE)
        stty=P.stdout.read()
        P.wait()
        P = None
        fields = [ _.strip() for _ in stty.split('\n')[0].split(';') ]
        for f in fields:
          if f.endswith(' columns'):
            columns = int(f[:-8])
    self._backend=backend
    self.columns = columns
    self._state=''
    self._lock=threading.RLock()
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
      self._backend=None

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
