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
from collections import namedtuple
from os import SEEK_CUR
from struct import Struct
import sys
from cs.fileutils import read_data, pread, seekable
from cs.py.func import prop
from cs.py3 import bytes, pack, unpack, iter_unpack
# DEBUG
from cs.logutils import warning, X, Pfx

# a convenience chunk of 256 zero bytes, mostly for use by 'free' blocks
B0_256 = bytes(256)

# an arbitrary maximum read size for fetching the data section
SIZE_16MB = 1024*1024*16

def get_box(bs, offset=0):
  ''' Decode an box from the bytes `bs`, starting at `offset` (default 0). Return the box's length, type, data offset, data length and the new offset.
  '''
  offset0 = offset
  usertype = None
  if offset + 8 > len(bs):
    raise ValueError("not enough bytes at offset %d for box size and type, only %d remaining"
                     % (offset, len(bs) - offset))
  box_size, = unpack('>L', bs[offset:offset+4])
  box_type = bs[offset+4:offset+8]
  offset += 8
  if box_size == 0:
    # box extends to end of data/file
    length = len(bs) - offset0
  elif box_size == 1:
    # 64 bit length
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
  if offset0 + length > len(bs):
    raise ValueError("not enough bytes at offset %d: box length %d but only %d bytes remain"
                     % (offset, length, len(bs) - offset))
  if box_type == 'uuid':
    # user supplied 16 byte type
    if offset + 16 > len(bs):
      raise ValueError("not enough bytes at offset %d for usertype, only %d remaining"
                       % (offset, len(bs) - offset))
    usertype = bs[offset:offset+16]
    box_type = usertype
    offset += 16
  offset_final = offset0 + length
  if offset_final < offset:
    raise RuntimeError("final offset %d < preamble end offset %d (offset0=%d, box_size=%d, box_type=%r, length=%d, usertype=%r)"
                       % (offset_final, offset, offset0, box_size, box_type, length, usertype))
  tail_offset = offset
  tail_length = offset_final - tail_offset
  return length, box_type, tail_offset, tail_length, offset_final

def read_box_header(fp, offset=None):
  ''' Read a raw box header from a file, return the box's length, type and remaining data length.
      `offset`: if not None, perform a seek to this offset before
        reading the box.
  '''
  if offset is not None:
    fp.seek(offset)
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
  return length, box_type, tail_len

def read_box(fp, offset=None, skip_data=False):
  ''' Read a raw box from a file, return the box's length, type and data bytes.
      No decoding of the data section is performed.
      `offset`: if not None, perform a seek to this offset before
        reading the box.
      `skip_data`: if true (default false), do not read the data
        section after the box header; instead of returning the data
        bytes, return their length. NOTE: in this case the file pointer
        is _not_ advanced to the start of the next box; subsequent
        callers must do this themselves, for example by doing a
        relative seek over the data length or an absolute seek to the
        starting file offset plus the box length, or by supplying such
        an absolute `offset` on the next call.
  '''
  length, box_type, tail_len = read_box_header(fp, offset=offset)
  if length is None and box_type is None and tail_len is None:
    return None, None, None
  if skip_data:
    return length, box_type, tail_len
  if tail_len == 0:
    tail_bs = b''
  else:
    tail_bs = read_data(fp, tail_len, rsize=tail_len)
  if len(tail_bs) != tail_len:
    raise ValueError("box tail length %d, expected %d" % (len(tail_bs), tail_len))
  return length, box_type, tail_bs

def file_boxes(fp):
  ''' Generator yielding box (length, type, data) until EOF on `fp`.
  '''
  while True:
    box_size, box_type, box_tail = read_box(sys.stdin)
    if box_size is None and box_type is None and box_tail is None:
      # EOF
      break
    yield box_size, box_type, box_tail

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
  ''' Write an box with type `box_type` (bytes) and data `box_tail` (bytes) to `fp`. Return number of bytes written (should equal the leading box length field).
  '''
  written = 0
  for bs in transcribe_box(box_type, box_tail):
    written += len(bs)
    fp.write(bs)
  return written

def get_utf8_nul(bs, offset=0):
  ''' Collect a NUL terminated UTF8 encoded string. Return string and new offset.
  '''
  endpos = bs.find(b'\0', offset)
  if endpos < 0:
    raise ValueError('no NUL in data: %r' % (bs[offset:],))
  return bs[offset:endpos].decode('utf-8'), endpos + 1

class Box(object):
  ''' Base class for all boxes - ISO14496 section 4.2.
  '''

  def __init__(self, box_type, box_data):
    # sanity check the supplied box_type
    # against the box types this class supports
    if sys.hexversion < 0x03000000:
      if isinstance(box_type, bytes):
        box_type = box_type._bytes__s
    try:
      BOX_TYPE = self.BOX_TYPE
    except AttributeError:
      try:
        BOX_TYPES = self.BOX_TYPES
      except AttributeError:
        if type(self) is not Box:
          raise RuntimeError("no box_type check in %s, box_type=%r"
                             % (self.__class__, box_type))
        pass
      else:
        if box_type not in BOX_TYPES:
          raise ValueError("box_type should be in %r but got %r"
                           % (BOX_TYPES, box_type))
    else:
      if box_type != BOX_TYPE:
        raise ValueError("box_type should be %r but got %r"
                         % (BOX_TYPE, box_type))
    self.box_type = box_type
    # store the box_data, which may be in various forms
    if isinstance(box_data, bytes):
      # bytes? store directly for use
      self._box_data = box_data
    elif isinstance(box_data, str):
      self._box_data = bytes(box_data.decode('iso8859-1'))
    else:
      # otherwise it should be a callable returning the bytes
      self._fetch_box_data = box_data
      self._box_data = None

  @classmethod
  def box_type_from_klass(klass):
    ''' Compute the Box's 4 byte type field from the class name.
    '''
    klass_name = klass.__name__
    if len(klass_name) == 7 and klass_name.endswith('Box'):
      klass_prefix = klass_name[:4]
      if klass_prefix.isupper():
        return klass_prefix.lower().encode('ascii')
    raise AttributeError("no automatic box type for %s" % (klass,))

  # NB: a @property instead of @prop to preserve AttributeError
  @property
  def BOX_TYPE(self):
    ''' The default .BOX_TYPE is inferred from the class name.
    '''
    return type(self).box_type_from_klass()

  def attribute_summary(self):
    ''' Comma separator list of attribute values honouring format strings.
    '''
    strs = []
    for attr in self.ATTRIBUTES:
      if isinstance(attr, str):
        fmt = '%s'
      else:
        # an (attr, fmt) tuple
        attr, fmt = attr
      strs.append(attr + '=' + fmt % (getattr(self, attr),))
    return ','.join(strs)

  def __str__(self):
    if self._box_data is None:
      # do not load the data just for __str__
      return 'Box(%r,box_data=%s())' \
             % (self.box_type, self._fetch_box_data)
    return 'Box(%r,box_data=%d:%r%s)' \
           % (self.box_type, len(self._box_data),
              self._box_data[:32],
              '...' if len(self._box_data) > 32 else '')

  def dump(self, indent='', fp=None):
    if fp is None:
      fp = sys.stdout
    fp.write(indent)
    fp.write(str(self))
    fp.write('\n')

  @staticmethod
  def from_file(fp, cls=None):
    ''' Decode a Box subclass from the file `fp`, return it. Return None at EOF.
        `cls`: if not None, use to construct the instance. Otherwise,
          look up the box_type in KNOWN_BOX_CLASSES and use that class
          or Box if not present.
    '''
    ##TODO: use read_box_header and skip things like mdat data block
    length, box_type, box_data_length = read_box_header(fp)
    if length is None and box_type is None and box_data_length is None:
      return None
    if seekable(fp):
      # create callable to fetch the data section of the box
      # snapshot the fd and position, make callable, then skip the data section
      box_data_offset = fp.tell()
      box_data_fd = fp.fileno()
      def fetch_data():
        chunks = []
        offset = box_data_offset
        needed = box_data_length
        if needed > 10*SIZE_16MB:
          raise RuntimeError("BIG FETCH!")
        while needed > 0:
          read_size = min(needed, SIZE_16MB)
          chunk = pread(box_data_fd, read_size, offset)
          if len(chunk) != read_size:
            X("WRONG PREAD: asked for %d bytes, got %d bytes",
              read_size, len(chunk))
            if len(chunk) == 0:
              break
          chunks.append(chunk)
          needed -= len(chunk)
          offset += len(chunk)
        return b''.join(chunks)
      fp.seek(box_data_length, SEEK_CUR)
    else:
      # not seekable: read all the data now and damn the memory expense
      fetch_data = read_data(fp, box_data_length)
      if len(fetch_data) != box_data_length:
        raise ValueError("expected to read %d box data bytes but got %d"
                         % (box_data_length, len(fetch_data)))
    if cls is None:
      cls = pick_box_class(box_type)
      ##X("from_file: KNOWN_BOX_CLASSES.get(%r) => %s", box_type, Box)
    return cls(box_type, fetch_data)

  @staticmethod
  def from_bytes(bs, offset=0, cls=None):
    ''' Decode a Box from a bytes object `bs`, return the Box and the new offset.
        `offset`: starting point in `bs` for decode, default 0.
        `cls`: if not None, use to construct the instance. Otherwise,
          look up the box_type in KNOWN_BOX_CLASSES and use that class
          or Box if not present.
    '''
    offset0 = offset
    if offset == len(bs):
      return None, None
    if offset > len(bs):
      raise ValueError("from_bytes: offset %d is past the end of bs" % (offset,))
    offset0 = offset
    length, box_type, tail_offset, tail_length, offset = get_box(bs, offset=offset)
    if offset0 + length != offset:
      raise RuntimeError("get_box(bs,offset=%d) returned length=%d,offset=%d which do not match"
                         % (offset0, length, offset))
    if offset > len(bs):
      raise RuntimeError("box length=%d, but that exceeds the size of bs (%d bytes, offset=%d)"
                         % (length, len(bs), offset0))
    fetch_box_data = lambda: bs[tail_offset:tail_offset+tail_length]
    if cls is None:
      cls = pick_box_class(box_type)
      ##X("from_bytes: KNOWN_BOX_CLASSES.get(%r) => %s", box_type, Box)
    B = cls(box_type, fetch_box_data)
    return B, offset

  def _load_box_data(self):
    ''' Load the box data into private attribute ._box_data.
    '''
    if self._box_data is None:
      self._box_data = bytes(self._fetch_box_data())
    return self._box_data

  def _set_box_data(self, data):
    ''' Set the private attribute ._box_data to `data`.
        This may be used by subclasses to discard loaded data after
        processing if they override the .data_chunks method.
    '''
    self._box_data = data

  def _advance_box_data(self, advance):
    ''' Advance/crop _box_data to allow for ingested parsed fields.
        This requires matching subclass .data_chunks to extrude the parsed fields.
    '''
    if advance <= 0:
      raise ValueError("_parsed_box_data: advance should be > 0: %d" % (advance,))
    if advance > len(self._box_data):
      raise ValueError("_parsed_box_data: advance beyond len(_box_data:%d): %d" % (len(self._box_data), advance))
    self._set_box_data(self._box_data[advance:])

  def data_chunks(self):
    ''' Return an iterable of bytes objects comprising the data section of this Box.
        This method should be overridden by subclasses which decompose data sections.
        If they also call ._parsed_box_data to advance past the
        ingested fields they can then call this method to emit the
        trailing unparsed data if any.
    '''
    yield self._load_box_data()

  @prop
  def box_data(self):
    ''' A bytes object containing the data section for this Box.
    '''
    return b''.join(self.data_chunks())

  def transcribe(self):
    ''' Generator yielding bytes objects which together comprise a serialisation of this box.
    '''
    return transcribe_box(self.box_type, self.data_chunks())

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

def add_box_class(klass):
  ''' Register a box class in KNOWN_BOX_CLASSES.
  '''
  global KNOWN_BOX_CLASSES
  with Pfx("add_box_class(%s)", klass):
    try:
      box_types = klass.BOX_TYPES
    except AttributeError:
      box_type = klass.box_type_from_klass()
      box_types = (box_type,)
    for box_type in box_types:
      if box_type in KNOWN_BOX_CLASSES:
        raise TypeError("box_type %r already in KNOWN_BOX_CLASSES as %s"
                        % (box_type, KNOWN_BOX_CLASSES[box_type]))
      KNOWN_BOX_CLASSES[box_type] = klass

def add_box_subclass(superclass, box_type, section, desc):
  ''' Create and register a new Box class that is simply a subclass of another.
  '''
  if isinstance(box_type, bytes):
    classname = box_type.decode('ascii').upper() + 'Box'
  else:
    classname = box_type.upper() + 'Box'
    box_type = box_type.decode('ascii')
  K = type(classname, (superclass,), {})
  K.__doc__ = "Box type %r %s box - ISO14496 section %s." % (box_type, desc, section)
  add_box_class(K)

if sys.hexversion >= 0x03000000:
  def pick_box_class(box_type):
    global KNOWN_BOX_CLASSES
    return KNOWN_BOX_CLASSES.get(box_type, Box)
else:
  def pick_box_class(box_type):
    global KNOWN_BOX_CLASSES
    if isinstance(box_type, bytes):
      box_type = box_type._bytes__s
    return KNOWN_BOX_CLASSES.get(box_type, Box)

class FullBox(Box):
  ''' A common extension of a basic Box, with a version and flags field.
      ISO14496 section 4.2.
  '''

  def __init__(self, box_type, box_data):
    Box.__init__(self, box_type, box_data)
    box_data = self._load_box_data()
    self.version = box_data[0]
    self.flags = (box_data[1]<<16) | (box_data[2]<<8) | box_data[3]
    self._advance_box_data(4)

  def __str__(self):
    prefix = '%s(%r-v%d-0x%02x' % (self.__class__.__name__,
                                   self.box_type,
                                   self.version,
                                   self.flags)
    attr_summary = self.attribute_summary()
    return prefix + ',' + attr_summary + ')'

  @prop
  def box_vf_data_chunk(self):
    ''' Return the leading version and flags.
        Subclasses need to yield this first from .data_chunks().
    '''
    return bytes([ self.version,
                   (self.flags>>16) & 0xff,
                   (self.flags>>8) & 0xff,
                   self.flags & 0xff
                 ])

class FREEBox(Box):
  ''' A 'free' or 'skip' box - ISO14496 section 8.1.2.
      Note the length and discard the data portion.
  '''

  BOX_TYPES = (b'free', b'skip')

  def __init__(self, box_type, box_data):
    Box.__init__(self, box_type, box_data)
    box_data = self._load_box_data()
    self.free_size = len(box_data)
    # discard cache of padding data
    self._set_box_data(b'')

  def __str__(self):
    return 'FREEBox(free_size=%d)' \
           % (self.free_size,)

  def data_chunks(self):
    global B0_256
    free_bytes = self.free_size
    len256 = len(B0_256)
    while free_bytes > len256:
      yield B0_256
      free_bytes -= len256
    if free_bytes > 0:
      yield bytes(free_bytes)

add_box_class(FREEBox)

class FTYPBox(Box):
  ''' An 'ftyp' File Type box - ISO14496 section 4.3.
      Decode the major_brand, minor_version and compatible_brands.
  '''

  def __init__(self, box_type, box_data):
    Box.__init__(self, box_type, box_data)
    box_data = self._load_box_data()
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
    self._set_box_data(b'')

  def __str__(self):
    return 'FTYPBox(major_brand=%r,minor_version=%d,compatible_brands=%r)' \
           % (self.major_brand, self.minor_version, self.compatible_brands)

  def data_chunks(self):
    yield self.major_brand
    yield pack('>L', self.minor_version)
    for brand in self.compatible_brands:
      yield brand

add_box_class(FTYPBox)

# field names for the tuples in a PDINBox
PDInfo = namedtuple('PDInfo', 'rate initial_delay')

class PDINBox(FullBox):
  ''' An 'pdin' Progressive Download Information box - ISO14496 section 8.1.3.
      Decode the (rate, initial_delay) pairs of the data section.
  '''

  ATTRIBUTES = (('pdinfo', '%r'),)

  def __init__(self, box_type, box_data):
    FullBox.__init__(self, box_type, box_data)
    # obtain box data after version and flags decode
    box_data = self._box_data
    if len(box_data) % 8 != 0:
      raise ValueError("box_data not a multiple of 2x4 bytes: %d"
                       % (len(box_data),))
    self.pdinfo = [ PDInfo(unpack('>LL', box_data[offset:offset+8]))
                    for offset in range(0, len(box_data), 8)
                  ]
    # forget data bytes
    self._set_box_data(b'')

  def data_chunks(self):
    yield self.box_vf_data_chunk
    for pdinfo in self.pdinfo:
      yield pack('>LL', pdinfo.rate, pdinfo.initial_delay)

add_box_class(PDINBox)

def get_boxes(bs, offset=0, max_offset=None):
  ''' Generator collecting Boxes from the supplied data `bs`, starting at `offset` (default: 0) and ending at `max_offset` (default: end of `bs`).
      Postcondition: all data up to `max_offset` has been collectewd into Boxes.
  '''
  if max_offset is None:
    max_offset = len(bs)
  while offset < max_offset:
    B, offset = Box.from_bytes(bs, offset)
    if B is None:
      if offset is not None:
        raise RuntimeError("unexpected offset=%r with B=None" % (offset,))
      break
    if offset > max_offset:
      raise ValueError('final box exceeds limit: finished at %d but limit was %d'
                       % (offset, max_offset))
    yield B

class ContainerBox(Box):
  ''' A base class for pure container boxes.
  '''

  def __init__(self, box_type, box_data):
    Box.__init__(self, box_type, box_data)
    self._boxes = None

  @prop
  def boxes(self):
    if self._boxes is None:
      box_data = self._load_box_data()
      self._boxes = list(get_boxes(box_data))
    return self._boxes

  def __str__(self):
    return '%s(%s)' \
           % (self.__class__.__name__, ','.join(str(B) for B in self.boxes))

  def dump(self, indent='', fp=None):
    if fp is None:
      fp = sys.stdout
    fp.write(indent)
    fp.write(self.__class__.__name__)
    fp.write('\n')
    indent += '  '
    for B in self.boxes:
      B.dump(indent, fp)

  def data_chunks(self):
    for B in self.boxes:
      for chunk in B.data_chunks():
        yield chunk

class MOOVBox(ContainerBox):
  ''' An 'moov' Movie box - ISO14496 section 8.2.1.
      Decode the contained boxes.
  '''
  pass
add_box_class(MOOVBox)

class MVHDBox(FullBox):
  ''' An 'mvhd' Movie Header box - ISO14496 section 8.2.2.
  '''

  ATTRIBUTES = ( ('rate', '%g'),
                 ('volume', '%g'),
                 ('matrix', '%r'),
                 ('next_track_id', '%d') )

  def __init__(self, box_type, box_data):
    FullBox.__init__(self, box_type, box_data)
    # obtain box data after version and flags decode
    box_data = self._box_data
    if self.version == 0:
      self.creation_time, \
      self.modification_time, \
      self.timescale, \
      self.duration = unpack('>LLLL', box_data[:16])
      offset = 16
    elif self.version == 1:
      self.creation_time, \
      self.modification_time, \
      self.timescale, \
      self.duration = unpack('>QQLQ', box_data[:28])
      offset = 28
    else:
      raise ValueError("MVHD: unsupported version %d" % (self.version,))
    self._rate, \
    self._volume = unpack('>lh', box_data[offset:offset+6])
    offset += 6 + 10    # 4 rate, 2 volume, 2-reserved, 2x4 reserved
    self.matrix = unpack('>lllllllll', box_data[offset:offset+36])
    offset += 36 + 24   # 9x4 matrix, 6x4 predefined
    self.next_track_id, = unpack('>L', box_data[offset:offset+4])
    offset += 4
    if offset != len(box_data):
      raise ValueError("MVHD: after decode offset=%d but len(box_data)=%d"
                       % (offset, len(box_data)))

  @prop
  def rate(self):
    ''' Rate field converted to float: 1.0 represents normal rate.
    '''
    _rate = self._rate
    return (_rate>>16) + (_rate&0xffff)/65536.0

  @prop
  def volume(self):
    ''' Volume field converted to float: 1.0 represents full volume.
    '''
    _volume = self._volume
    return (_volume>>8) + (_volume&0xff)/256.0

  def data_chunks(self):
    yield self.box_vf_data_chunk
    if self.version == 0:
      yield pack('>LLLL',
                 self.creation_time,
                 self.modification_time,
                 self.timescale,
                 self.duration)
    elif self.version == 1:
      yield pack('>QQLQ',
                 self.creation_time,
                 self.modification_time,
                 self.timescale,
                 self.duration)
    else:
      raise RuntimeError("unsupported version %d" % (self.version,))
    yield pack('>l', self._rate)
    yield pack('>h', self._volume)
    yield bytes(10)
    yield pack('>lllllllll', *self.matrix)
    yield bytes(24)
    yield pack('>L', self.next_track_id)

add_box_class(MVHDBox)

class TRAKBox(ContainerBox):
  ''' A 'trak' Track box - ISO14496 section 8.3.1.
      Decode the contained boxes.
  '''
  pass

add_box_class(TRAKBox)

class TKHDBox(FullBox):
  ''' An 'tkhd' Track Header box - ISO14496 section 8.2.2.
  '''

  ATTRIBUTES = ( 'track_enabled',
                 'track_in_movie',
                 'track_in_preview',
                 'track_size_is_aspect_ratio',
                 'creation_time',
                 'modification_time',
                 'track_id',
                 'duration',
                 'layer',
                 'alternate_group',
                 'volume',
                 ('matrix', '%r'),
                 'width',
                 'height',
               )

  def __init__(self, box_type, box_data):
    FullBox.__init__(self, box_type, box_data)
    # obtain box data after version and flags decode
    box_data = self._box_data
    if self.version == 0:
      self.creation_time, \
      self.modification_time, \
      self.track_id, \
      self.reserved1, \
      self.duration = unpack('>LLLLL', box_data[:20])
      offset = 20
    elif self.version == 1:
      self.creation_time, \
      self.modification_time, \
      self.track_id, \
      self.reserved1, \
      self.duration = unpack('>QQLLQ', box_data[:32])
      offset = 32
    else:
      raise ValueError("TRHD: unsupported version %d" % (self.version,))
    self.reserved2, self.reserved3, \
    self.layer, \
    self.alternate_group, \
    self.volume, \
    self.reserved4 = unpack('>LLhhhH', box_data[offset:offset+16])
    offset += 16
    self.matrix = unpack('>lllllllll', box_data[offset:offset+36])
    offset += 36
    self.width, self.height = unpack('>LL', box_data[offset:offset+8])
    offset += 8

  @prop
  def track_enabled(self):
    return (self.flags&0x1) != 0

  @prop
  def track_in_movie(self):
    return (self.flags&0x2) != 0

  @prop
  def track_in_preview(self):
    return (self.flags&0x4) != 0

  @prop
  def track_size_is_aspect_ratio(self):
    return (self.flags&0x8) != 0

  def data_chunks(self):
    yield self.box_vf_data_chunk
    if self.version == 0:
      yield pack('>LLLLL',
                 self.creation_time,
                 self.modification_time,
                 self.track_id,
                 self.reserved1,
                 self.duration)
    elif self.version == 1:
      yield pack('>QQLLQ',
                 self.creation_time,
                 self.modification_time,
                 self.track_id,
                 self.reserved1,
                 self.duration)
    else:
      raise RuntimeError("unsupported version %d" % (self.version,))
    yield pack('>LLhhhH',
               self.reserved2, self.reserved3,
               self.layer,
               self.alternate_group,
               self.volume,
               self.reserved4)
    yield pack('>lllllllll', *self.matrix)
    yield pack('>LL', self.width, self.height)

add_box_class(TKHDBox)

class TREFBox(ContainerBox):
  ''' An 'tref' Track Reference box - ISO14496 section 8.3.3.
      Decode the contained boxes.
  '''
  pass
add_box_class(TREFBox)

class TrackReferenceTypeBox(Box):
  ''' A TrackReferenceTypeBox continas references to other tracks - ISO14496 section 8.3.3.2.
  '''

  BOX_TYPES = (b'hint', b'cdsc', b'font', b'hind', b'vdep', b'vplx', b'subt')

  def __init__(self, box_type, box_data):
    Box.__init__(self, box_type, box_data)
    box_data = self._load_box_data()
    track_ids = []
    for track_id, in iter_unpack('>L', box_data):
      track_ids.append(track_id)
    self.track_ids = track_id

  def __str__(self):
    return '%s(type=%r,track_ids=%r)' % (self.__class__.__name__, self.box_type, self.track_ids)

  def data_chunks(self):
    for track_id in self.track_ids:
      yield pack('>L', track_id)

for box_type in TrackReferenceTypeBox.BOX_TYPES:
  KNOWN_BOX_CLASSES[box_type] = TrackReferenceTypeBox
del box_type

class TRGRBox(ContainerBox):
  ''' An 'trgr' Track Group box - ISO14496 section 8.3.4.
      Decode the contained boxes.
  '''
  pass

add_box_class(TRGRBox)

class TrackGroupTypeBox(FullBox):
  ''' A TrackGroupTypeBox contains track group id types - ISO14496 section 8.3.3.2.
  '''
  ATTRIBUTES = ( 'track_group_id', )

  def __init__(self, box_type, box_data):
    FullBox.__init__(self, box_type, box_data)
    # obtain box data after version and flags decode
    box_data = self._box_data
    self.track_group_id, = unpack('>L', box_data[:4])
    if len(box_data) > 4:
      warning('%s: %d bytes of unparsed data after track_group_id: %r',
              self.__class__.__name__, len(box_data)-4, box_data[4:])

  def data_chunks(self):
    yield self.box_vf_data_chunk
    yield pack('>L', self.track_group_id)

class MSRCBox(TrackGroupTypeBox):
  ''' Multi-source presentation TrackGroupTypeBox - ISO14496 section 8.3.4.3.
  '''
  pass

add_box_class(MSRCBox)

class MDIABox(ContainerBox):
  ''' An 'mdia' Media box - ISO14496 section 8.4.1.
      Decode the contained boxes.
  '''
  pass

add_box_class(MDIABox)

class MDHDBox(FullBox):
  ''' A MDHDBox is a Media Header box - ISO14496 section 8.4.2.
  '''

  ATTRIBUTES = ( 'creation_time',
                 'modification_time',
                 'timescale',
                 'duration',
                 'language' )

  def __init__(self, box_type, box_data):
    FullBox.__init__(self, box_type, box_data)
    # obtain box data after version and flags decode
    box_data = self._box_data
    if self.version == 0:
      self.creation_time, \
      self.modification_time, \
      self.timescale, \
      self.duration = unpack('>LLLL', box_data[:16])
      offset = 16
    elif self.version == 1:
      self.creation_time, \
      self.modification_time, \
      self.timescale, \
      self.duration = unpack('>QQLQ', box_data[:28])
      offset = 28
    else:
      raise RuntimeError("unsupported version %d" % (self.version,))
    self._language, \
    self.pre_defined = unpack('>HH', box_data[offset:offset+4])
    offset += 4
    if offset != len(box_data):
      warning("MDHD: %d unparsed bytes after pre_defined: %r",
              len(box_data)-offset, box_data[offset:])

  def data_chunks(self):
    yield self.box_vf_data_chunk
    if self.version == 0:
      yield pack('>LLLL',
                 self.creation_time,
                 self.modification_time,
                 self.timescale,
                 self.duration)
    elif self.version == 1:
      yield pack('>QQLQ',
                 self.creation_time,
                 self.modification_time,
                 self.timescale,
                 self.duration)
    else:
      raise RuntimeError('unsupported version: %d', self.version)
    yield self.pack('>HH', self._language, self.pre_defined)

  @prop
  def language(self):
    ''' The ISO 639‐2/T language code as decoded from the packed form.
    '''
    _language = self._language
    return bytes([ x+0x60
                   for x in ( (_language>>10)&0x1f,
                              (_language>>5)&0x1f,
                              _language&0x1f
                            )
                 ]).decode('ascii')

add_box_class(MDHDBox)

class HDLRBox(FullBox):
  ''' A HDLRBox is a Handler Reference box - ISO14496 section 8.4.3.
  '''

  ATTRIBUTES = ( ('handler_type', '%r'), 'name' )

  def __init__(self, box_type, box_data):
    FullBox.__init__(self, box_type, box_data)
    # obtain box data after version and flags decode
    box_data = self._box_data
    # NB: handler_type is supported to be an unsigned long, but in practice seems to be 4 ASCII bytes, so we load it as a string for readability
    self.pre_defined, \
    self.handler_type, \
    self.reserved1, \
    self.reserved2, \
    self.reserved3 = unpack('>L4sLLL', box_data[:20])
    offset1 = 20
    self.name, offset = get_utf8_nul(box_data, offset1)
    if offset < len(box_data):
      raise ValueError('HDLR: found NUL not at end of data: %r' % (box_data[offset1:],))

  def data_chunks(self):
    yield self.box_vf_data_chunk
    yield pack('>L4sLLL',
               self.pre_defined,
               self.handler_type,
               self.reserved1,
               self.reserved2,
               self.reserved3)
    yield self.name.encode('utf-8')
    yield b'\0'

add_box_class(HDLRBox)

add_box_subclass(ContainerBox, b'minf', '8.4.4', 'Media Information')

class NMHDBox(FullBox):
  ''' A NMHDBox is a Null Media Header box - ISO14496 section 8.4.5.2.
  '''

  ATTRIBUTES = ()

  def __init__(self, box_type, box_data):
    FullBox.__init__(self, box_type, box_data)
    # obtain box data after version and flags decode
    box_data = self._box_data
    if len(box_data) > 0:
      raise ValueError("NMHD: unexpected data: %r" % (box_data,))

  def data_chunks(self):
    yield self.box_vf_data_chunk

add_box_class(NMHDBox)

class ELNGBox(FullBox):
  ''' A ELNGBox is a Extended Language Tag box - ISO14496 section 8.4.6.
  '''

  ATTRIBUTES = ( 'extended_language', )

  def __init__(self, box_type, box_data):
    FullBox.__init__(self, box_type, box_data)
    # obtain box data after version and flags decode
    box_data = self._box_data
    # extended language based on RFC4646
    self.extended_language, offset = get_utf8_nul(box_data)
    if offset < len(box_data):
      raise ValueError("ELNG: unexpected data: %r" % (box_data[offset:],))

  def data_chunks(self):
    yield self.box_vf_data_chunk
    yield self.extended_language.encode('utf-8')
    yield b'\0'

add_box_class(ELNGBox)

add_box_subclass(ContainerBox, b'stbl', '8.5.1', 'Sample Table')

class _SampleTableContainerBox(FullBox):
  ''' An intermediate FullBox subclass which contains more boxes.
  '''

  ATTRIBUTES = ()

  def __init__(self, box_type, box_data):
    FullBox.__init__(self, box_type, box_data)
    # obtain box data after version and flags decode
    box_data = self._box_data
    entry_count, = unpack('>L', box_data[:4])
    self.boxes = list(get_boxes(box_data, 4))
    if len(self.boxes) != entry_count:
      raise ValueError('expected %d contained Boxes but parsed %d'
                       % (entry_count, len(self.boxes)))

  def __str__(self):
    return '%s(%s)' \
           % (self.__class__.__name__, ','.join(str(B) for B in self.boxes))

  def dump(self, indent='', fp=None):
    if fp is None:
      fp = sys.stdout
    fp.write(indent)
    fp.write(self.__class__.__name__)
    fp.write('\n')
    indent += '  '
    for B in self.boxes:
      B.dump(indent, fp)

  def data_chunks(self):
    yield self.box_vf_data_chunk
    yield pack('>L', len(self.boxes))
    for B in self.boxes:
      for chunk in B.data_chunks():
        yield chunk

add_box_subclass(_SampleTableContainerBox, b'stsd', '8.5.2', 'Sample Description')

class _SampleEntry(Box):
  ''' Superclass of Sample Entry boxes.
  '''

  def __init__(self, box_type, box_data):
    Box.__init__(self, box_type, box_data)
    box_data = self._load_box_data()
    self.reserved, self.data_reference_index = unpack('>6sH', box_data[:8])
    self._set_box_data(box_data[8:])

  def __str__(self):
    prefix = '%s(%r-%r,data_reference_index=%d' \
           % (self.__class__.__name__,
              self.box_type,
              self.reserved,
              self.data_reference_index)
    attr_summary = self.attribute_summary()
    return prefix + ',' + attr_summary + ')'

  @prop
  def box_se_data_chunk(self):
    ''' Return the leading reserved bytes and data_reference_index.
        Subclasses need to yield this first from .data_chunks().
    '''
    return pack('>6sH', self.reserved, self.data_reference_index)

class BTRTBox(Box):
  ''' BitRateBox - section 8.5.2.2.
  '''

  ATTRIBUTES = ( 'bufferSizeDB', 'maxBitrate', 'avgBitrate' )

  def __init__(self, box_type, box_data):
    box_data = self._load_box_data()
    self.bufferSizeDB, \
    self.maxBitrate, \
    self.avgBitrate = unpack('>LLL', box_data)

  def __str__(self):
    attr_summary = self.attribute_summary()
    return self.__class__.__name__ + '(' + attr_summary + ')'

  def data_chunks(self):
    yield pack('>LLL',
               self.bufferSizeDB,
               self.maxBitrate,
               self.avgBitrate)

add_box_class(BTRTBox)

add_box_subclass(_SampleTableContainerBox, b'stdp', '8.5.3', 'Degradation Priority')

TTSB_Sample = namedtuple('TTSB_Sample', 'count delta')

class _GenericSampleBox(FullBox):
  ''' Time to Sample box - section 8.6.1.
  '''

  ATTRIBUTES = ( ('samples', '%r'), )

  def __init__(self, box_type, box_data, sample_struct_format_v0, sample_fields, sample_struct_format_v1=None, inferred_entry_count=False):
    if sample_struct_format_v1 is None:
      sample_struct_format_v1 = sample_struct_format_v0
    FullBox.__init__(self, box_type, box_data)
    if self.version == 0:
      S = Struct(sample_struct_format_v0)
    elif self.version == 1:
      S = Struct(sample_struct_format_v1)
    else:
      warning("unsupported version %d, treating like version 1", self.version)
      S = Struct(sample_struct_format_v1)
    sample_type = namedtuple(type(self).__name__ + '_Sample',
                             sample_fields)
    # obtain box data after version and flags decode
    box_data = self._box_data
    if inferred_entry_count:
      entry_count = len(box_data) // S.size
      remainder = len(box_data) % S.size
      if remainder != 0:
        warning("box_data length %d is not a multiple of len(%s), %d bytes left over: %r",
                len(box_data), S.size, remainder, box_data[-remainder:])
      bd_offset = 0
    else:
      entry_count, = unpack('>L', box_data[:4])
      bd_offset = 4
    samples = []
    for i in range(entry_count):
      sample = sample_type(*S.unpack(box_data[bd_offset:bd_offset+S.size]))
      samples.append(sample)
      bd_offset += S.size
    self.samples = samples

class _TimeToSampleBox(_GenericSampleBox):
  ''' Time to Sample box - section 8.6.1.
  '''
  def __init__(self, box_type, box_data):
    _GenericSampleBox.__init__(self, box_type, box_data, '>LL', 'count delta')
add_box_subclass(_TimeToSampleBox, b'stts', '8.6.1.2.1', 'Time to Sample')

class CTTSBox(FullBox):
  ''' A 'ctts' Composition Time to Sample box - section 8.6.1.3.
  '''
  def __init__(self, box_type, box_data):
    _GenericSampleBox.__init__(self, box_type, box_data, '>LL', 'count delta', '>Ll')
add_box_class(CTTSBox)

class CSLGBox(FullBox):
  ''' A 'cslg' Composition to Decode box - section 8.6.1.4.
  '''

  ATTRIBUTES = ( 'compositionToDTSShift',
                 'leastDecodeToDisplayDelta',
                 'greatestDecodeToDisplayDelta',
                 'compositionStartTime',
                 'compositionEndTime',
               )

  def __init__(self, box_type, box_data):
    FullBox.__init__(self, box_type, box_data)
    # obtain box data after version and flags decode
    box_data = self._box_data
    if self.version == 0:
      struct_format = '>lllll'
    elif self.version == 1:
      struct_format = '>qqqqq'
    else:
      warning("unsupported version %d, treating like version 1")
      struct_format = '>qqqqq'
    S = Struct(struct_format)
    self.compositionToDTSShift, \
    self.leastDecodeToDisplayDelta, \
    self.greatestDecodeToDisplayDelta, \
    self.compositionStartTime, \
    self.compositionEndTime \
      = S.unpack(struct_format, box_data[:S.size])

add_box_class(CSLGBox)

class STSSBox(_GenericSampleBox):
  ''' A 'stss' Sync Sample box - section 8.6.2.
  '''
  def __init__(self, box_type, box_data):
    _GenericSampleBox.__init__(self, box_type, box_data, '>L', 'number')
add_box_class(STSSBox)

class STSHBox(_GenericSampleBox):
  ''' A 'stsh' Shadow Sync Table box - section 8.6.3.
  '''
  def __init__(self, box_type, box_data):
    _GenericSampleBox.__init__(self, box_type, box_data, '>LL',
                               'shadowed_sample_number sync_sample_number')
add_box_class(STSHBox)

class SDTPBox(_GenericSampleBox):
  ''' A 'sdtp' Independent and Disposable Samples box - section 8.6.4.
  '''
  def __init__(self, box_type, box_data):
    _GenericSampleBox.__init__(self, box_type, box_data, '>HHHH',
                               'is_leading sample_depends_on sample_is_depended_on sample_has_redundancy',
                               inferred_entry_count=True)
add_box_class(SDTPBox)

add_box_subclass(Box, b'edts', '8.6.5.1', 'Edit')

class ELSTBox(_GenericSampleBox):
  ''' A 'elst' Edit List box - section 8.6.6.
  '''
  def __init__(self, box_type, box_data):
    _GenericSampleBox.__init__(self, box_type, box_data, '>Ll',
                               'segment_duration media_time',
                               sample_struct_format_v1='>Qq')
add_box_class(ELSTBox)

if __name__ == '__main__':
  # parse media stream from stdin as test
  from os import fdopen
  from cs.logutils import setup_logging
  setup_logging(__file__)
  stdin = fdopen(sys.stdin.fileno(), 'rb')
  while True:
    B = Box.from_file(stdin)
    if B is None:
      break
    B.dump()
    ##print(B)
