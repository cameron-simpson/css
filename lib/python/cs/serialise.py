#!/usr/bin/python -tt
#
# Common serialisation functions.
#       - Cameron Simpson <cs@zip.com.au>
#

from cs.logutils import D

def fromBS(s):
  ''' Read an extensible value from the front of a string.
      Continuation octets have their high bit set.
      The value is big-endian.
  '''
  o=ord(s[0])
  n=o&0x7f
  used=1
  while o & 0x80:
    o=ord(s[used])
    used+=1
    n=(n<<7)|(o&0x7f)
  return (n, s[used:])

def fromBSfp(fp):
  ''' Read an extensible value from a file.
      Return None at EOF.
  '''
  ##debug("fromBSfp: reading first BS byte...")
  s=c=fp.read(1)
  ##debug("fromBSfp: c=0x%02x" % ord(c))
  if len(s) == 0:
    return None
  while ord(c)&0x80:
    ##debug("fromBSfp: reading another BS byte...")
    c=fp.read(1)
    assert len(c) == 1, "unexpected EOF"
    ##debug("fromBSfp: c=0x%02x" % ord(c))
    s+=c
  n, s = fromBS(s)
  ##debug("fromBSfp: n==%d" % n)
  assert len(s) == 0
  return n

def toBS(n):
  ''' Encode a value as an entensible octet sequence for decode by
      fromBS().
  '''
  s=chr(n&0x7f)
  n>>=7
  while n > 0:
    s=chr(0x80|(n&0x7f))+s
    n>>=7
  return s

def get_bs(bs, offset=0):
  ''' Decode an unsigned value from a bytes at the specified `offset` (default 0).
      Return the value and the new offset.
  '''
  o = bs[offset]
  offset += 1
  n = o & 0x7f
  while o & 0x80:
    o = bs[offset]
    offset += 1
    n = (n<<7) | (o&0x7f)
  return n, offset

def put_bs(n):
  ''' Encode an unsigned value as an entensible octet sequence for decode by
      get_bs().
  '''
  if n < 0:
    raise ValueError("n < 0 (%r)", n)
  bs = [ n&0x7f ]
  n >>= 7
  while n > 0:
    bs.append( 0x80 | (n&0x7f) )
    n >>= 7
  return bytes(reversed(bs))
