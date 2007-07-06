#!/usr/bin/python

''' A data store after the style of the Venti scheme:
      http://library.pantek.com/general/plan9.documents/venti/venti.html
    but supporting variable sized blocks and arbitrary sizes.
    See also the Plan 9 Venti support manual pages:
      http://swtch.com/plan9port/man/man7/venti.html
    and the Wikipedia entry:
      http://en.wikipedia.org/wiki/Venti

    TODO: rolling hash
            augment with assorted recognition strings by hash
            pushable parser for nested data
          optional compression in store?
          metadata O:owner u:user[+-=]srwx* g:group[+-=]srwx*
          don't compress metadata
          cache seek()ed block in readOpen class
          extend directory blockref:
            flags:
              1: indirect
              2: inode ref
          inode chunk:
            flags
            [meta] (if flags&0x01)
            blockref
          caching store - fetch&store locally
          store priority queue - tuples=pool
          remote store: http? multifetch? udp?
          proto:
            fetch hash -> block or None
            offer hash -> "got it" boolean
            send block -> hash
'''

## NOTE: migrate to hashlib sometime when python 2.5 more common
import sha

HASH_SIZE=20                                    # size of SHA-1 hash
MAX_BLOCKSIZE=16383                             # fits in 2 octets BS-encoded
MAX_SUBBLOCKS=int(MAX_BLOCKSIZE/(HASH_SIZE+4))  # flags(1)+span(2)+hlen(1)+hash

def hash_sha(block):
  ''' Returns the SHA-1 checksum for the supplied block.
  '''
  hash=sha.new(block)
  return hash.digest()

def fromhex(hexstr):
  ''' Return raw byte array from hexadecimal string.
  '''
  return "".join([chr(int(hexstr[i:i+2],16)) for i in range(0,len(hexstr),2)])

def genHex(data):
  for c in data:
    yield '%02x'%ord(c)

def tohex(data):
  return "".join(genHex(data))

def writetohex(fp,data):
  ''' Write data in hex to file.
  '''
  for w in genHex(data):
    fp.write(w)
