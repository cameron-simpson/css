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

def get_bs(data, offset=0):
  ''' Read an extensible value from `data` at `offset`.
      Continuation octets have their high bit set.
      The value is big-endian.
      Return value and new offset.
  '''
  n = 0
  b = 0x80
  while b & 0x80:
    b = data[offset]
    offset += 1
    n = (n<<7) | (b&0x7f)
  return n, offset

def read_bs(fp):
  ''' Read an extensible value from the binary stream `fp`.
      Continuation octets have their high bit set.
      The value is big-endian.
      Return value.
  '''
  n = 0
  b = 0x80
  while b & 0x80:
    b = fp.read(1)[0]
    n = (n<<7) | (b&0x7f)
  return n

def put_bs(n):
  ''' Encode a value as an entensible octet sequence for decode by get_bs().
      Return the bytes object.
  '''
  bs = [ n&0x7f ]
  n >>= 7
  while n > 0:
    bs.append( 0x80 | (n&0x7f) )
    n >>= 7
  return bytes(reversed(bs))

def get_bsdata(bs, offset=0):
  ''' Fetch a length-prefixed data chunk.
      Decodes an unsigned value from a bytes at the specified `offset`
      (default 0), and collects that many following bytes.
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
  length = read_bs(fp)
  data = fp.read(length)
  if len(data) != length:
    raise ValueError('short read, expected %d bytes, got %d' % (length, len(data)))
  return data

def put_bsdata(data):
  ''' Encodes `data` as put_bs(len(`data`)) + `data`.
  '''
  return put_bs(len(data)) + data

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
    pkt_flags = (self.flags << 2) | (fl_is_request << 1) | (fl_has_channel)
    bs_tag = put_bs(self.tag)
    bs_flags = put_bs(pkt_flags)
    if self.channel:
      bs_channel = put_bs(self.channel)
    else:
      bs_channel = b''
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

def get_Packet(data, offset=0):
  ''' Parse a Packet from the binary data `packet` at position `offset`.
      Return the Packet and the new offset.
  '''
  # collect packet from data chunk
  packet, offset0 = get_bsdata(data)
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
