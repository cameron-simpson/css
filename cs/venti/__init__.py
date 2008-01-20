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

    TODO:
          Prefetch multiple blocks, eg from an iblock.
            Scatter/gather wrapper?
            With progressive yeild of blocks in order
          "vt push targetStore"
          "vt pull sourceStore"
          "vt scan" - don't open -S/-C stores
          sync() on stream/tcp close
          rolling hash
            augment with assorted recognition strings by hash
            pushable parser for nested data
          flat file cache for blocks: make temp unlinked, trunc, append,
                  rewind at thrashold
          optional compression in store?
            test space cost of compress of compressed data
          metadata O:owner u:user:[srwx]-[srwx] g:group:[srwx]-[srwx] o:[srwx]-[srwx] mtime:unixtime
          metadata: inline text in dir, with i: field to indirect to a blockref
          sync() operation for blocklists
            close() collapses and sync()s ?
            sync collapses?
          BlockSink take ibref initialiser?
          don't compress metadata
          cache seek()ed block in readOpen class
          inode chunk:
            flags
            [meta] (if flags&0x01)
            blockref
          store priority queue - tuples=pool
'''

## NOTE: migrate to hashlib sometime when python 2.5 more common
import sha

HASH_SIZE=20                                    # size of SHA-1 hash
MIN_BLOCKSIZE=80                                # less than this seems silly
MAX_BLOCKSIZE=16383                             # fits in 2 octets BS-encoded
MAX_SUBBLOCKS=int(MAX_BLOCKSIZE/(HASH_SIZE+4))  # flags(1)+span(2)+hlen(1)+hash

def hash_sha(block):
  ''' Returns the SHA-1 checksum for the supplied block.
  '''
  hash=sha.new(block)
  return hash.digest()

hash=hash_sha

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
