#!/usr/bin/python

''' A data store after the style of the Venti scheme:
      http://library.pantek.com/general/plan9.documents/venti/venti.html
    but supporting variable sized blocks and arbitrary sizes.
    Man page:
      http://www.cskk.ezoshosting.com/cs/css/manuals/vt.1.html
    See also the Plan 9 Venti support manual pages:
      http://swtch.com/plan9port/man/man7/venti.html
    and the Wikipedia entry:
      http://en.wikipedia.org/wiki/Venti
    To do list now at:
      http://csbp.backpackit.com/pub/1356606
'''

import threading

class _ventiDefaults(threading.local):
  ''' Per-thread default store stack.
      A Store's __enter__/__exit__ methods push/pop that store
      from the default.
  '''
  def __getattr__(self, attr):
    if attr == 'S':
      return None
    if attr == 'oldS':
      oldS = self.oldS = []
      return oldS
    raise AttributeError("no .%s attribute" % attr)
  def pushStore(self, newS):
    defaults.oldS.append(defaults.S)
    defaults.S = newS
  def popStore(self):
    defaults.S = defaults.oldS.pop()

defaults = _ventiDefaults()

def fromhex(hexstr):
  ''' Return raw byte array from hexadecimal string.
  '''
  return "".join([chr(int(hexstr[i:i+2],16)) for i in range(0,len(hexstr),2)])

def genHex(data):
  for c in data:
    assert type(c) is str and len(c) == 1, "bad c: %s" % (`c`,)
    yield '%02x' % (ord(c),)

def tohex(data):
  ''' Represent a byte sequence as a hex string.
  '''
  return "".join(genHex(data))

def writetohex(fp,data):
  ''' Write data in hex to file.
  '''
  for w in genHex(data):
    fp.write(w)
