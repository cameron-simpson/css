#!/usr/bin/python -tt
#
# Common serialisation functions.
#       - Cameron Simpson <cs@zip.com.au>
#

DISTINFO = {
    'description': "some serialisation functions",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
}

from collections import namedtuple

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

def get_bsdata(bs, offset=0):
  ''' Fetch a length-prefixed data chunk.
      Decodes an unsigned value from a bytes at the specified `offset`
      (default 0), and collected that many following bytes.
      Return those following bytes and the new offset.
  '''
  offset0 = offset
  datalen, offset = get_bs(bs, offset=offset)
  data = bs[offset:offset+datalen]
  if len(data) != datalen:
    raise ValueError("bsdata(bs, offset=%d): unsufficient data: expected %d bytes, got %d bytes"
                     % (offset0, datalen, len(data)))
  offset += datalen
  return data, offset

def read_bsdata(fp):
  ''' Read a run length encoded data chunk from a file stream.
  '''
  length = fromBSfp(fp)
  data = fp.read(length)
  if len(data) != length:
    raise ValueError('short read, expected %d bytes, got %d' % (length, len(data)))
  return data

def put_bs(n):
  ''' Encode an unsigned value as an extensible octet sequence for decode by
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

def put_bsdata(data):
  ''' Encodes `data` as put_bs(len(`data`)) + `data`.
  '''
  return put_bs(len(data)) + data

def get_bsfp(fp):
  ''' Read an extensible value from a file.
      Return None at EOF.
  '''
  bs = fp.read(1)
  if len(bs) == 0:
    return None
  bss = [bs]
  while bs[0] & 0x80:
    ##debug("fromBSfp: reading another BS byte...")
    bs = fp.read(1)
    if len(bs) != 1:
      raise ValueError("unexpected EOF")
    bss.append(bs)
  bs = b''.join(bss)
  n, offset = get_bs(bs)
  if offset != len(bs):
    raise RuntimeError("failed decode of %r ==> n=%d, offset=%d" % (bs, n, offset))
  return n

_Packet = namedtuple('_Packet', 'channel tag is_request flags payload')

class Packet(_Packet):
  ''' A general purpose packet to wrap a multiplexable protocol.
  '''

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
    pkt_flags = (flags << 2) | (fl_is_request << 1) | (fl_has_channel)
    bs_tag = toBS(tag)
    bs_flags = toBS(flags)
    if channel:
      bs_channel = toBS(channel)
    else:
      bs_channel = b''
    packet = bs_tag + bs_flags + bs_channel + payload
    return put_bsdata(packet)

def get_bsPacket(fp):
  packet = read_bsdata(fp)
  tag, offset = get_bs(packet)
  flags, offset = get_bs(packet, offset)
  has_channel = flags & 0x01
  is_request = flags & 0x02
  flags >>= 2
  if has_channel:
    channel, offset = get_bs(packet, offset)
  else:
    channel = 0
  payload = packet[offset:]
  return Packet(channel=channel, tag=tag, is_request=is_request, flags=flag, payload=payload)
