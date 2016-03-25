#!/usr/bin/python
#
# Facilities for ISO14496 files - the ISO Base Media File Format,
# the basis for several things including MP4.
#   - Cameron Simpson <cs@zip.com.au> 26mar2016
#
# ISO make the standard available here:
#   http://standards.iso.org/ittf/PubliclyAvailableStandards/index.html
#   http://standards.iso.org/ittf/PubliclyAvailableStandards/c068960_ISO_IEC_14496-12_2015.zip
#

from __future__ import print_function
import sys
from struct import pack, unpack
from cs.fileutils import read_data

def get_box(bs, offset=0):
  ''' Decode an box from the bytes `bs`, starting at `offset` (default 0). Return the box's length, name and data bytes (offset, length), and the new offset.
  '''
  usertype = None
  if offset + 8 > len(bs):
    raise ValueError("not enough bytes at offset %d for box size and type, only %d remaining"
                     % (offset, len(bs) - offset))
  box_size, = unpack('>L', bs[offset:offset+4])
  box_type = bs[offset+4:offset+8]
  offset += 8
  if box_size == 0:
    raise ValueError("box size 0 (\"to end of file\") not supported")
  if box_size == 1:
    if offset + 8 > len(bs):
      raise ValueError("not enough bytes at offset %d for largesize, only %d remaining"
                       % (offset, len(bs) - offset))
    length = unpack('>Q', bs[offset:offset+8])
    offset += 8
  elif box_size < 8:
    raise ValueError("box size too low: %d, expected at least 8"
                     % (box_size,))
  else:
    length = box_size
  if offset + length > len(bs):
    raise ValueError("not enough bytes at offset %d: box length %d but only %d bytes remain"
                     % (offset, length, len(bs) - offset))
  if box_type == 'uuid':
    if offset + 16 > len(bs):
      raise ValueError("not enough bytes at offset %d for usertype, only %d remaining"
                       % (offset, len(bs) - offset))
    usertype = bs[offset:offset+16]
    offset += 16
    box_type = usertype
  offset_final = offset0 + length
  if offset_final < offset:
    raise RuntimeError("final offset %d < preamble end offset %d (offset0=%d, box_size=%d, box_type=%r, length=%d, usertype=%r)"
                       % (offset_final, offset, offset0, box_size, box_type, length, usertype))
  tail_offset = offset
  tail_length = offset_final - tail_offset
  return length, box_type, tail_offset, tail_length

def read_box(fp):
  ''' Read an box from a file, return the box's length, name and data bytes.
  '''
  header = read_data(fp, 8)
  if not header:
    # indicate end of file
    return None, None, None
  if len(header) != 8:
    raise ValueError("short header: %d bytes, expected 8" % (len(len_bs),))
  sofar = 8
  box_size, box_type = unpack('>L4s', header)
  if box_size == 0:
    # TODO: implement box_size 0: read to end of file
    raise ValueError("box size 0 (\"to end of file\") not supported")
  elif box_size == 1:
    largesize_bs = read_data(fp, 8)
    if len(largesize_bs) < 8:
      raise ValueError("not enough bytes read for largesize, expected 8 but got %d (%r)"
                       % (len(largesize_bs), largesize_bs))
    sofar += 8
    length, = unpack('>Q', largesize_bs)
  elif box_size < 8:
    raise ValueError("box length too low: %d, expected at least 8"
                     % (box_size,))
  else:
    length = box_size
  if box_type == 'uuid':
    usertype = read_data(fp, 16)
    if len(usertype) != 16:
      raise ValueError("expected 16 bytes for usertype, got %d (%r)"
                       % (len(usertype), usertype))
    sofar += 16
    box_type = usertype
  tail_len = length - sofar
  if tail_len < 0:
    raise ValueError("negative tail length! (%d) - overrun from header?" % (tail_len,))
  elif tail_len == 0:
    tail_bs = b''
  else:
    tail_bs = read_data(fp, tail_len, rsize=tail_len)
  if len(tail_bs) != tail_len:
    raise ValueError("box tail length %d, expected %d" % (len(tail_bs), tail_len))
  return length, box_type, tail_bs

def write_box(fp, box_type, box_tail):
  ''' Write an box with name `box_type` (bytes) and data `box_tail` (bytes) to `fp`. Return number of bytes written (should equal the leading box length field).
  '''
  if not isinstance(box_type, bytes):
    raise TypeError("expected box_type to be bytes, received %s" % (type(box_type),))
  if not isinstance(box_tail, bytes):
    raise TypeError("expected box_tail to be bytes, received %s" % (type(box_tail),))
  if len(box_type) == 4:
    if box_type == 'uuid':
      raise ValueError("invalid box_type %r: expected 16 byte usertype for uuids" % (box_type,))
    usertype == b''
  elif len(box_type) == 16:
    usertype = box_type
    box_type = 'uuid'
  else:
    raise ValueError("invalid box_type, expect 4 or 16 byte values, got %d bytes" % (len(box_type),))
  length = 8 + len(usertype) + len(box_tail)
  if length < (1<<32):
    box_size = length
    largesize_bs = b''
  elif length < (1<<64):
    box_size = 1
    length += 8
    largesize_bs = pack('>Q', length)
  else:
    raise ValueError("box too big: size >= 1<<64: %d" % (length,))
  fp.write(pack('>L', box_size))
  fp.write(box_type)
  fp.write(largesize_bs)
  fp.write(usertype)
  fp.write(box_tail)
  return length

def boxs(fp):
  ''' Generator yielding box (length, name, data) until EOF on `fp`.
  '''
  while True:
    box_size, box_type, box_tail = read_box(sys.stdin)
    if box_size is None and box_type is None and box_tail is None:
      # EOF
      break
    yield box_size, box_type, box_tail

if __name__ == '__main__':
  # parse media stream from stdin as test
  from cs.logutils import setup_logging
  setup_logging(__file__)
  for al, an, ad in boxs(sys.stdin):
    print(al, repr(an))
    if ad is None:
      print('  no data')
    elif len(ad) <= 32:
      print(' ', repr(ad))
    else:
      print('  %d bytes: %r...' % (len(ad), ad[:32]))
