from __future__ import with_statement
from threading import RLock
from contextlib import contextmanager
import atexit
from cs.lex import unctrl       ##, tabpadding

active=False

_defaultUpd=None

def default():
  global _defaultUpd
  if _defaultUpd is None:
    import sys
    _defaultUpd=Upd(sys.stderr)
    import cs.misc
    cs.misc._defaultUpd=_defaultUpd
  return _defaultUpd

def nl(line):    return default().nl(line)
def out(line):   return default().out(line)
def close(line): return default().close(line)
def state():     return default().state()
def without(func,*args,**kw):
                 if _defaultUpd is None:
                   return func(*args,**kw)
                 return _defaultUpd.without(func,*args,**kw)

instances=[]

def cleanupAtExit():
  global instances
  for i in instances:
    i.close()
  instances=()

atexit.register(cleanupAtExit)

class Upd:
  def __init__(self,backend,mode=None):
    assert backend is not None
    self.__lock=RLock()
    self.__backend=backend
    self.__buf=''
    global active, instances
    instances.append(self)
    active=True

  def state(self):
    return self.__buf

  def out(self,txt,noStrip=False):
    with self.__lock:
      old=self.__buf
      if not noStrip:
        txt=txt.rstrip()
      txt=unctrl(txt)

      txtlen=len(txt)
      buflen=len(self.__buf)
      pfxlen=min(txtlen,buflen)
      for i in range(pfxlen):
        if txt[i] != self.__buf[i]:
          pfxlen=i
          break

      # Rewrites take one of two forms:
      #   Backspace to end of common prefix, overwrite with the differing tail
      #     of the new string, erase trailing extent if any.
      #   Return to start of line with carriage return, overwrite with new
      #    string, erase trailing extent if any.
      # Therefore compare backspaces against cr+pfxlen.
      #
      patch=''
      if buflen-pfxlen < 1+pfxlen:
        for i in range(buflen-pfxlen):
          patch+='\b'
        patch+=txt[pfxlen:]
      else:
        patch='\r'+txt

      extlen=buflen-txtlen
      if extlen > 0:
        ##patch+=tabpadding(extlen,offset=txtlen)
        patch+="%*s" % (extlen, ' ')
        for i in range(extlen):
          patch+='\b'

      self.__backend.write(patch)
      self.__backend.flush()
      self.__buf=txt

    return old

  def nl(self,txt,noStrip=False):
    self.without(self.__backend.write,txt+'\n',noStrip=noStrip)

  def close(self):
    if self.__backend is not None:
      self.out('')
      self.__backend=None

  def closed(self):
    return self.__backend == None

  def without(self,func,*args,**kw):
    if 'noStrip' in kw:
      noStrip=kw['noStrip']
      del kw['noStrip']
    else:
      noStrip=False
    with self._withoutContext(noStrip):
      ret=func(*args,**kw)
    return ret

  @contextmanager
  def _withoutContext(self,noStrip=False):
    with self.__lock:
      old=self.out('',noStrip=noStrip)
      yield
      self.out(old,noStrip=True)

@contextmanager
def __dummyNoUpd():
  yield

def noupd(U=None):
  if U is None:
    if _defaultUpd is None:
      return __dummyNoUpd()
    U=_defaultUpd
  return U._withoutContext()

class ExceptionPrefix(object):
  ''' A context manager to prefix exception complaints.
  '''

  def __init__(self, prefix):
    self.__prefix = prefix

  def __enter__(self):
    pass

  def __exit__(self, exc_type, exc_value, tb):
    if exc_type is not None:
      upd_state = state()
      if len(upd_state) > 0:
        pfx = upd_state + ": " + self.__prefix
      else:
        pfx = self.__prefix
      raise exc_type, pfx + ": " + str(exc_value), tb
    return False
