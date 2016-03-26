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

def transcribe_box(fp, box_type, box_tail):
  ''' Generator yielding bytes objects which together comprise a serialisation of this
   box.
      `box_tail` may be a bytes object or an iterable of bytes objects.
  '''
  if not isinstance(box_type, bytes):
    raise TypeError("expected box_type to be bytes, received %s" % (type(box_type),))
  if len(box_type) == 4:
    if box_type == 'uuid':
      raise ValueError("invalid box_type %r: expected 16 byte usertype for uuids" % (box_type,))
    usertype == b''
  elif len(box_type) == 16:
    usertype = box_type
    box_type = 'uuid'
  else:
    raise ValueError("invalid box_type, expect 4 or 16 byte values, got %d bytes" % (len(box_type),))
  if isinstance(box_tail, bytes):
    box_tail = [box_tail]
  else:
    box_tail = list(box_tail)
  tail_len = sum(len(bs) for bs in box_tail)
  length = 8 + len(usertype) + tail_len
  if length < (1<<32):
    box_size = length
    largesize_bs = b''
  elif length < (1<<64):
    box_size = 1
    length += 8
    largesize_bs = pack('>Q', length)
  else:
    raise ValueError("box too big: size >= 1<<64: %d" % (length,))
  yield pack('>L', box_size)
  yield box_type
  yield largesize_bs
  yield usertype
  for bs in box_tail:
    yield bs

def write_box(fp, box_type, box_tail):
  ''' Write an box with name `box_type` (bytes) and data `box_tail` (bytes) to `fp`. Return number of bytes written (should equal the leading box length field).
  '''
  written = 0
  for bs in transcribe_box(box_type, box_tail):
    written += len(bs)
    fp.write(bs)
  return written

class Box(object):

  def __init__(self, box_type, box_data):
    self.box_type = box_type
    self._box_data = box_data

  def __str__(self):
    return 'Box(box_type=%r,box_data=%d:%r%s)' \
           % (self.box_type, len(self._box_data),
              self._box_data[:32],
              '...' if len(self._box_data) > 32 else '')

  @classmethod
  def from_file(cls, fp):
    ''' Read a Box from the file `fp`, return it. Return None at EOF.
    '''
    length, box_type, box_data = read_box(fp)
    if length is None and box_type is None and box_data is None:
      return None
    return cls(box_type, box_data)

  @classmethod
  def from_bytes(cls, bs, offset=0):
    ''' Decode a Box from a bytes object `bs`, return the Box and the new offset.
    '''
    offset0 = offset
    length, box_type, tail_offset, tail_length = get_box(bs, offset=offset)
    offset += length
    if offset > len(bs):
      raise RuntimeError("box length=%d, but that exceeds the size of bs (%d bytes, offset=%d)"
                         % (length, len(bs), offset0))
    box_data = bs[tail_offset:tail_offset+tail_length]
    if len(box_data) != tail_length:
      raise RuntimeError("expected %d bytes from bs for tail, got %d"
                         % (tail_length, len(box_data)))
    B = cls(box_type, box_data)
    return B, offset

  @property
  def box_data(self):
    ''' An iterable of bytes objects comprising the data section of this Box.
        This property should be overridden by subclasses which decompose data sections.
    '''
    yield self._box_data

  def transcribe(self):
    ''' Generator yielding bytes objects which together comprise a serialisation of this box.
    '''
    return transcribe_box(self.box_type, self.box_data)

  def write(self, fp):
    ''' Transcribe this box to a file in serialised form.
        This method uses transcribe, so it should not need overriding in subclasses.
    '''
    written = 0
    for bs in self.transcribe():
      written += len(bs)
      fp.write(bs)
    return written

# mapping of known box subclasses for use by factories
KNOWN_BOX_CLASSES = {}

class FTYPBox(Box):

  BOX_TYPE = b'ftyp'

  def __init__(self, box_type, box_data):
    if box_type != self.BOX_TYPE:
      raise ValueError("box_type should be %r but got %r"
                       % (self.BOX_TYPE, box_type))
    if len(box_data) < 8:
      raise ValueError("box_data too short, expected at least 8 bytes, got %d"
                       % (len(box_data),))
    if len(box_data) % 4 != 0:
      raise ValueError("box_data not a multiple of 4 bytes: %d"
                       % (len(box_data),))
    self.major_brand = box_data[:4]
    self.minor_version, = unpack('>L', box_data[4:8])
    self.compatible_brands = [ box_data[offset:offset+4]
                               for offset in range(8, len(box_data), 4)
                             ]
    Box.__init__(self, self.BOX_TYPE, b'')

  def __str__(self):
    return 'FTYPBox(major_brand=%r,minor_version=%d,compatible_brands=%r)' \
           % (self.major_brand, self.minor_version, self.compatible_brands)

  @property
  def box_data(self):
    yield self.major_brand
    yield pack('>L', self.minor_version)
    for brand in self.compatible_brands:
      yield brand

KNOWN_BOX_CLASSES[FTYPBox.BOX_TYPE] = FTYPBox

def file_boxes(fp):
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
  while True:
    B = Box.from_file(sys.stdin)
    if B is None:
      break
    print(B)
