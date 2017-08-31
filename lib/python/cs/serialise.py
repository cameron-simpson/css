#!/usr/bin/python -tt
#
# Common serialisation functions.
#       - Cameron Simpson <cs@cskk.id.au>
#

DISTINFO = {
    'description': "some serialisation functions",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'install_requires': ['cs.py3'],
}

import sys
from collections import namedtuple
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
  ''' Read an extensible byte serialised value from `data` at `offset`.
      Continuation octets have their high bit set.
      The value is big-endian.
      Return value and new offset.
  '''
  ##is_bytes(data)
  n = 0
  b = 0x80
  while b & 0x80:
    b = data[offset]
    offset += 1
    n = (n<<7) | (b&0x7f)
  return n, offset

def read_bs(fp):
  ''' Read an extensible byte serialised value from the binary stream `fp`.
      Continuation octets have their high bit set.
      The value is big-endian.
      Return value.
  '''
  n = 0
  b = 0x80
  while b & 0x80:
    bs = fp.read(1)
    if not bs:
      raise EOFError("%s: end of input" % (fp,))
    b = bs[0]
    n = (n<<7) | (b&0x7f)
  return n

@returns_bytes
def put_bs(n):
  ''' Encode a value as an entensible byte serialised octet sequence for decode by get_bs().
      Return the bytes object.
  '''
  bs = [ n&0x7f ]
  n >>= 7
  while n > 0:
    bs.append( 0x80 | (n&0x7f) )
    n >>= 7
  bs = bytes(reversed(bs))
  ##is_bytes(bs)
  return bs

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

@returns_bytes
def read_bsdata(fp):
  ''' Read a run length encoded data chunk from a file stream.
  '''
  length = read_bs(fp)
  data = fp.read(length)
  if len(data) == length:
    return data
  if len(data) < length:
    raise EOFError('%s: short read, expected %d bytes, got %d'
                   % (fp, length, len(data)))
  raise RuntimeError('%s: extra data: asked for %d bytes, received %d bytes!'
                     % (fp, length, len(data)))

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

_Packet = namedtuple('_Packet', 'channel tag is_request flags payload')

class Packet(_Packet):
  ''' A general purpose packet to wrap a multiplexable protocol.
  '''

  def __str__(self):
    return ( "Packet(channel=%s,tag=%s,is_request=%s,flags=0x%02x,payload=[%d]%r)"
           % ( self.channel, self.tag, self.is_request, self.flags,
               len(self.payload), self.payload[:16]
             )
           )
  def serialise(self):
    ''' Binary transcription of this packet.
        Format:
          bs(total_length)
          bs(tag)
          bs(flags)
          [bs(channel)] # if channel != 0
          payload       # remainder of packet, size derived from total_length
        Flags:
          0             # has_channel
          1             # is_request
          remaining flags shifted left and returned
    '''
    fl_has_channel = 1 if self.channel else 0
    fl_is_request  = 1 if self.is_request else 0
    pkt_flags = (self.flags << 2) | (fl_is_request << 1) | (fl_has_channel)
    bs_tag = put_bs(self.tag)
    bs_flags = put_bs(pkt_flags)
    if self.channel:
      bs_channel = put_bs(self.channel)
    else:
      bs_channel = bytes(())
    packet = bs_tag + bs_flags + bs_channel + self.payload
    return put_bsdata(packet)

def read_Packet(fp):
  ''' Read a Packet from a binary stream, return the Packet.
  '''
  packet = read_bsdata(fp)
  P, offset = get_Packet(packet)
  if offset < len(packet):
    raise ValueError("extra data in packet after offset=%d" % (offset,))
  return P

if sys.hexversion >= 0x03000000:
  def write_Packet(fp, P):
    ''' Write a Packet to a binary stream.
        Note: does not flush the stream.
    '''
    ##from cs.logutils import X
    ##X("write_Packet(%s)", P)
    fp.write(put_bsdata(P.serialise()))
else:
  def write_Packet(fp, P):
    ''' Write a Packet to a binary stream.
        Note: does not flush the stream.
    '''
    fp.write(put_bsdata(P.serialise()).as_buffer())

def get_Packet(data, offset=0):
  ''' Parse a Packet from the binary data `packet` at position `offset`.
      Return the Packet and the new offset.
  '''
  ##is_bytes(data)
  # collect packet from data chunk
  packet, offset0 = get_bsdata(data, offset)
  ##is_bytes(packet)
  # now decode packet
  tag, offset = get_bs(packet)
  flags, offset = get_bs(packet, offset)
  has_channel = flags & 0x01
  is_request = (flags & 0x02) != 0
  flags >>= 2
  if has_channel:
    channel, offset = get_bs(packet, offset)
  else:
    channel = 0
  payload = packet[offset:]
  return Packet(channel=channel, tag=tag, is_request=is_request, flags=flags, payload=payload), offset0

if __name__ == '__main__':
  import sys
  import cs.serialise_tests
  cs.serialise_tests.selftest(sys.argv)
