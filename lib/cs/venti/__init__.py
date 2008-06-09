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

from cs.venti.hash import hash, HASH_T, MIN_BLOCKSIZE, MAX_BLOCKSIZE, MAX_SUBBLOCKS

def fromhex(hexstr):
  ''' Return raw byte array from hexadecimal string.
  '''
  return "".join([chr(int(hexstr[i:i+2],16)) for i in range(0,len(hexstr),2)])

def genHex(data):
  for c in data:
    yield '%02x'%ord(c)

def tohex(data):
  ''' Represent a byte sequence as a hex string.
  '''
  return "".join(genHex(data))

def writetohex(fp,data):
  ''' Write data in hex to file.
  '''
  for w in genHex(data):
    fp.write(w)
