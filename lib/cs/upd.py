from __future__ import with_statement
from threading import RLock
from cs.lex import unctrl, tabpadding

active=False

_defaultUpd=None

def default():
  global _defaultUpd
  if _defaultUpd is None:
    import sys
    _defaultUpd=Upd(sys.stderr)
  return _defaultUpd

def nl(line):    return default().nl(line)
def out(line):   return default().out(line)
def close(line): return default().close(line)
def state():     return default().state()
def without(func,*args,**kw):
                 return default().without(func,*args,**kw)

instances=[]

def cleanupAtExit():
  global instances
  for i in instances:
    i.close()
  instances=()

import atexit
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

  def without(self,func,*args,**kw):
    if 'noStrip' in kw:
      noStrip=kw['noStrip']
      del kw['noStrip']
    else:
      noStrip=False
    with self.__lock:
      old=self.out('',noStrip=noStrip)
      ret=func(*args,**kw)
      self.out(old,noStrip=True)
    return ret

  def close(self):
    if self.__backend is not None:
      self.out('')
      self.__backend=None

  def closed(self):
    return self.__backend == None
