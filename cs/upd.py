from cs.lex import unctrl, tabpadding

class Upd:
  def __init__(self,backend,mode=None):
    self.__backend=backend
    self.__buf=''

  def out(self,txt,noStrip=False):
    if not noStrip:
      txt=txt.rstrip()
    txt=unctrl(txt)

    txtlen=len(txt)
    pfxlen=0
    for i in range(min(txtlen,len(self.__buf))):
      if txt[i] != self.__buf[i]:
        pfxlen=i-1
        break

    # Rewrites take one of two forms:
    #   Backspace to end of common prefix, overwrite new string, erase
    #    trailing extent if any
    #   Return to start of line with carriage return, overwrite with new
    #    string, erase trailing extent if any
    #
    if len(self.__buf)-pfxlen + txtlen-pfxlen < txtlen+1:
      for i in range(len(self.__buf)-pfxlen):
        self.__backend.write('\b')
      self.__backend.write(txt[pfxlen:])
    else:
      self.__backend.write('\r')
      self.__backend.write(txt)

    extlen=len(self.__buf)-txtlen
    if extlen > 0:
      self.__backend.write(tabpadding(extlen,offset=txtlen))
      if extlen < txtlen+1:
        for i in range(extlen):
          self.__backend.write('\b')
      else:
        self.__backend.write('\r')
        self.__backend.write(txt)

    self.__buf=txt

  def nl(self,txt,noStrip=False):
    old=self.__buf
    out('',noStrip=noStrip)
    self.__backend.write(txt)
    self.__backend.write('\n')
    out(old,noStrip=True)

  def close(self):
    self.out('')
    self.__backend=None
