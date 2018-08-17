#!/usr/bin/python -tt
#
# Common serialisation functions.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Some serialising functions.
'''

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'install_requires': ['cs.py3', 'cs.binary'],
}

import sys
from collections import namedtuple
from cs.binary import BSUInt, BSData
from cs.py3 import bytes

DEBUG = False

if DEBUG:
  def returns_bytes(f):
    def wrapped(*a, **kw):
      value = f(*a, **kw)
      if type(value) is not bytes:
        raise RuntimeError("func %s(*%r, **%r) returns type %s: %r"
                           % (f, a, kw, type(value), value))
      return value
    return wrapped
else:
  def returns_bytes(f):
    return f

def is_bytes(value):
  if type(value) is not bytes:
    raise RuntimeError("value is not bytes: %s %r"
                       % (type(value), value))

def get_bs(data, offset=0):
  ''' Read an extensible byte serialised unsigned int from `data` at `offset`.
      Return value and new offset.

      Continuation octets have their high bit set.
      The value is big-endian.

      If you just have a bytes instance, this is the go. If you're
      reading from a stream you're better off with `cs.binary.BSUint`.
  '''
  ##is_bytes(data)
  n = 0
  b = 0x80
  while b & 0x80:
    b = data[offset]
    offset += 1
    n = (n << 7) | (b & 0x7f)
  return n, offset

put_bs = BSUInt.raw_transcribe

@returns_bytes
def put_bs(n):
  ''' Encode an unsigned int as an entensible byte serialised octet
      sequence for decode by `get_bs` or `cs.binary.BSUint`.
      Return the bytes object.
  '''
  bs = [ n & 0x7f ]
  n >>= 7
  while n > 0:
    bs.append( 0x80 | (n & 0x7f) )
    n >>= 7
  return bytes(reversed(bs))

def get_bsdata(chunk, offset=0):
  ''' Fetch a length-prefixed data chunk.
      Decodes an unsigned value from a bytes at the specified `offset`
      (default 0), and collects that many following bytes.
      Return those following bytes and the new offset.
  '''
  ##is_bytes(chunk)
  offset0 = offset
  datalen, offset = get_bs(chunk, offset=offset)
  data = chunk[offset:offset+datalen]
  ##is_bytes(data)
  if len(data) != datalen:
    raise ValueError("bsdata(chunk, offset=%d): insufficient data: expected %d bytes, got %d bytes"
                     % (offset0, datalen, len(data)))
  offset += datalen
  return data, offset

def bsdata_from_buffer(bfr):
  length = uint_from_buffer(bfr)
  return bfr.take(length)

# old name
read_bsdata = bsdata_from_buffer

@returns_bytes
def put_bsdata(data):
  ''' Encodes `data` as put_bs(len(`data`)) + `data`.
  '''
  ##is_bytes(data)
  chunk = put_bs(len(data)) + data
  ##is_bytes(chunk)
  return chunk

@returns_bytes
def put_bss(s, encoding='utf-8'):
  ''' Encode the string `s` to bytes using the specified encoding, default 'utf-8'; return the encoding encapsulated with put_bsdata.
  '''
  return put_bsdata(s.encode(encoding))

def get_bss(chunk, offset=0, encoding='utf-8'):
  ''' Fetch an encoded string using get_bsdata and decode with the specified encoding, default 'utf-8'; return the string and the new offset.
  '''
  data, offset = get_bsdata(chunk, offset)
  return data.decode(encoding), offset

def read_bss(fp):
  ''' Read an encoded string from `fp` using read_bsdata and decode with the specified encoding, default 'utf-8'; return the string.
  '''
  data = read_bsdata(fp, encoding='utf-8')
  return data.decode(encoding)

if __name__ == '__main__':
  import sys
  import cs.serialise_tests
  cs.serialise_tests.selftest(sys.argv)
