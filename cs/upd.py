from cs.lex import unctrl, tabpadding

active=False

_defaultUpd=None

def default():
  global _defaultUpd
  if _defaultUpd is None:
    import sys
    _defaultUpd=Upd(sys.stderr)
  return _defaultUpd

def nl(line):    default().nl(line)
def out(line):   default().out(line)
def close(line): default().close(line)
def state():     return default().state()

class Upd:
  def __init__(self,backend,mode=None):
    self.__backend=backend
    self.__buf=''
    global active
    active=True

  def state(self):
    return self.__buf

  def out(self,txt,noStrip=False):
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

  def nl(self,txt,noStrip=False):
    old=self.__buf
    self.out('',noStrip=noStrip)
    self.__backend.write(txt)
    self.__backend.write('\n')
    self.out(old,noStrip=True)

  def close(self):
    self.out('')
    self.__backend=None
