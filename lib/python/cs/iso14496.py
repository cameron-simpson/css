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
from os import fdopen, SEEK_CUR
from os.path import basename
from struct import Struct
import sys
from cs.buffer import CornuCopyBuffer
from cs.fileutils import read_data, read_from, pread, seekable
from cs.logutils import setup_logging, warning, X, Pfx
from cs.py.func import prop
from cs.py3 import bytes, pack, unpack, iter_unpack

USAGE = '''Usage:
  %s parse [{-|filename}]...
            Parse the named files (or stdin for "-").
  %s test   Run unit tests.'''

def main(argv):
  cmd = basename(argv.pop(0))
  setup_logging(cmd)
  if not argv:
    argv = ['parse']
  badopts = False
  op = argv.pop(0)
  with Pfx(op):
    if op == 'parse':
      if not argv:
        argv = ['-']
      for spec in argv:
        with Pfx(spec):
          if spec == '-':
            fp = fdopen(sys.stdin.fileno(), 'rb')
          else:
            fp = open(spec, 'rb')
          for B in parse_file(fp, discard=True):
            B.dump()
    elif op == 'test':
      import cs.iso14496_tests
      cs.iso14496_tests.selftest(sys.argv)
    else:
      warning("unknown op")
      badopts = True
  if badopts:
    print(USAGE % (cmd, cmd), file=sys.stderr)
    return 2

# a convenience chunk of 256 zero bytes, mostly for use by 'free' blocks
B0_256 = bytes(256)

# an arbitrary maximum read size for fetching the data section
SIZE_16MB = 1024*1024*16

# Note: the length includes the length of the header; if the length
#       is None the associated Box extends to the end of the input
#       otherwise the Box extends from the start of the header to that point
#       plus the length.
BoxHeader = namedtuple('BoxHeader', 'type user_type length header_length')

def parse_box_header(bfr):
  ''' Decode a box header from the CornuCopyBuffer `bfr`. Return (box_header, new_buf, new_offset) or None at end of input.
  '''
  # return BoxHeader=None if at the end of the data
  bfr.extend(1, short_ok=True)
  if not bfr:
    return None
  # note start point
  offset0 = bfr.offset
  user_type = None
  bfr.extend(8)
  box_size, = unpack('>L', bfr.take(4))
  box_type = bfr.take(4)
  if box_size == 0:
    # box extends to end of data/file
    length = None
  elif box_size == 1:
    # 64 bit length
    length, = unpack('>Q', bfr.take(8))
  else:
    length = box_size
  if box_type == 'uuid':
    # user supplied 16 byte type
    user_type = bfr.take(16)
  else:
    user_type = None
  offset = bfr.offset
  if length is not None and offset0+length < offset:
    raise ValueError("box length:%d is less than the box header size:%d"
                     % (length, offset-offset0))
  return BoxHeader(type=box_type, user_type=user_type,
                   length=length, header_length=offset-offset0)

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
  bs = bytes(bs)
  endpos = bs.find(b'\0', offset)
  if endpos < 0:
    raise ValueError('no NUL in data: %r' % (bs[offset:],))
  return bs[offset:endpos].decode('utf-8'), endpos + 1

def put_utf8_nul(s):
  ''' Return bytes encoding a string in UTF-8 with a trailing NUL.
  '''
  return s.encode('utf-8') + b'\0'

def unfold_chunks(chunks):
  ''' Unfold `chunks` into an iterable of bytes.
      This exists to allow subclass methods to easily return ASCII
      strings or bytes or iterables, in turn allowing them to
      simply return their subperclass' chunks iterators directly
      instead of having to unpack them.
  '''
  if isinstance(chunk, bytes):
    yield chunk
  elif isinstance(chunk, str):
    yield chunk.encode('ascii')
  else:
    for subchunk in chunk:
      for unfolded_chunk in self.unfold_chunks(subchunk):
        yield unfolded_chunk

class Box(object):
  ''' Base class for all boxes - ISO14496 section 4.2.
  '''

  def __init__(self, header):
    # sanity check the supplied box_type
    # against the box types this class supports
    box_type = header.type
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
    self.header = header

  @property
  def length(self):
    return self.header.length

  @property
  def box_type(self):
    return self.header.type

  @property
  def user_type(self):
    return self.header.user_type

  @classmethod
  def box_type_from_klass(klass):
    ''' Compute the Box's 4 byte type field from the class name.
    '''
    klass_name = klass.__name__
    if len(klass_name) == 7 and klass_name.endswith('Box'):
      klass_prefix = klass_name[:4]
      if klass_prefix.rstrip('_').isupper():
        return klass_prefix.replace('_', ' ').lower().encode('ascii')
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
    if self.data_chunks is None:
      return '%s(%r,box_data=DISCARDED)' \
             % (type(self).__name__, bytes(self.box_type))
    box_data = b''.join(self.data_chunks)
    return '%s(%r,box_data=%d:%r%s)' \
           % (type(self).__name__, bytes(self.box_type), len(box_data),
              box_data[:32],
              '...' if len(box_data) > 32 else '')

  def dump(self, indent='', fp=None):
    if fp is None:
      fp = sys.stdout
    fp.write(indent)
    fp.write(str(self))
    fp.write('\n')

  @staticmethod
  def from_buffer(bfr, cls=None, discard_data=False):
    ''' Decode a Box from `bfr`. Return the Box or None at end of input.
        `cls`: the Box class; if not None, use to construct the instance.
          Otherwise, look up the box_type in KNOWN_BOX_CLASSES and use that
          class or Box if not present.
        `discard_data`: if false (default), keep the unparsed data portion as
          a list of data chunk in the attribute .data_chunks; if true,
          discard the unparsed data
        `copy_offsets`: if not None, call `copy_offsets` with each
          Box starting offset
    '''
    offset0 = bfr.offset
    box_header = parse_box_header(bfr)
    if box_header is None:
      return None
    if cls is None:
      cls = pick_box_class(box_header.type)
    B = cls(box_header)
    B.offset = offset0
    X("Box.from_buffer: found %s at %d", bytes(B.box_type), offset0)
    bfr.report_offset(offset0)
    # further parse some or all of the data
    B.parse_data(bfr)
    # record the offset of any unparsed data portion
    B.unparsed_offset = bfr.offset
    # advance over the remaining data, optionally keeping it
    B.data_chunks = B._skip_data(bfr, discard=discard_data)
    return B

  @property
  def end_offset(self):
    ''' The offset of the next Box.
        This is None if the Box's length is None (implies that the
          box runs to the end of the input).
        Otherwise it is the start offset of the Box plus its length.
    '''
    length = self.length
    if length is None:
      return None
    return self.offset + length

  def _take_tail(self, bfr):
    ''' Take the remaining bytes of the Box data and return them.
    '''
    return bfr.take(self.end_offset-bfr.offset)

  def parse_data(self, bfr):
    ''' Decode the salient parts of the data section, return (new_buf, new_offset).
    '''
    # a base Box does not parse any of its data section
    pass

  def _skip_data(self, bfr, discard=False):
    ''' Consume any remaining Box data input. Return the data.
        `bfr`: a CornuCopyBuffer
        Return values:
        `data`: the data section as a list of chunks. None if `discard` is true.
    '''
    end_offset = self.end_offset
    if end_offset is None:
      raise ValueError("end_offset is None, cannot deduce target offset")
    data_chunks = None if discard else []
    bfr.skipto(end_offset, copy_skip=( None if discard else data_chunks.append ))
    self.data_chunks = data_chunks
    return data_chunks

  def parse_subboxes(self, bfr, max_offset, max_boxes=None):
    boxes = []
    while (max_boxes is None or len(boxes) < max_boxes) and bfr.offset < max_offset:
      B = Box.from_buffer(bfr)
      if B is None:
        raise ValueError("end of input reached after %d contained Boxes"
                         % (len(boxes)))
      boxes.append(B)
    if bfr.offset > max_offset:
      raise ValueError("contained Boxes overran max_offset:%d by %d bytes"
                       % (max_offset, offset-max_offset))
    return boxes

  def parsed_data_chunks(self):
    ''' Stub parsed_data_chunks to return chunks derived from parsed data fields.
        Any unparsed data remain in self.box_data (if not discarded) and are
        transcribed after these chunks.
    '''
    return ()

  def data_chunks(self):
    ''' Return an iterable of bytes objects comprising the data section of this Box.
        This method should be overridden by subclasses which decompose data sections.
        If they also call ._parsed_box_data to advance past the
        ingested fields they can then call this method to emit the
        trailing unparsed data if any.
    '''
    yield from unfold_chunks(self.parsed_data_chunks())
    yield from self.box_data

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
      fp.write(bs)
      written += len(bs)
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
    box_type = box_type.encode('ascii')
  K = type(classname, (superclass,), {})
  K.__doc__ = "Box type %r %s box - ISO14496 section %s." % (box_type, desc, section)
  add_box_class(K)

def pick_box_class(box_type):
  global KNOWN_BOX_CLASSES
  return KNOWN_BOX_CLASSES.get(box_type, Box)

class FullBox(Box):
  ''' A common extension of a basic Box, with a version and flags field.
      ISO14496 section 4.2.
  '''

  ATTRIBUTES = ()

  def parse_data(self, bfr):
    super().parse_data(bfr)
    self.version = bfr.take(1)[0]
    flags_bs = bfr.take(3)
    self.flags = (flags_bs[0]<<16) | (flags_bs[1]<<8) | flags_bs[2]

  def __str__(self):
    prefix = '%s(%r-v%d-0x%02x' % (self.__class__.__name__,
                                   self.box_type,
                                   self.version,
                                   self.flags)
    attr_summary = self.attribute_summary()
    return prefix + ',' + attr_summary + ')'

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
    yield bytes([ self.version,
                  (self.flags>>16) & 0xff,
                  (self.flags>>8) & 0xff,
                  self.flags & 0xff
                ])

class FREEBox(Box):
  ''' A 'free' or 'skip' box - ISO14496 section 8.1.2.
      Note the length and discard the data portion.
  '''

  BOX_TYPES = (b'free', b'skip')

  def parse_data(self, bfr):
    super().parse_data(bfr)
    offset0 = bfr.offset
    self._skip_data(bfr, discard=True)
    self.free_size = bfr.offset - offset0

  def __str__(self):
    return 'FREEBox(free_size=%d)' \
           % (self.free_size,)

  def parsed_data_chunks(self):
    global B0_256
    yield from super().parsed_data_chunks()
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

  def parse_data(self, bfr):
    super().parse_data(bfr)
    self.major_brand = bfr.take(4)
    self.minor_version, = unpack('>L', bfr.take(4))
    brands_bs = b''.join(self._skip_data(bfr))
    self.compatible_brands = [ brands_bs[offset:offset+4]
                               for offset in range(0, len(brands_bs), 4)
                             ]

  def __str__(self):
    return 'FTYPBox(major_brand=%r,minor_version=%d,compatible_brands=%r)' \
           % (bytes(self.major_brand), self.minor_version, self.compatible_brands)

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
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

  def parse_data(self, bfr):
    super().parse_data(bfr)
    # obtain box data after version and flags decode
    pdinfo_bs = b''.join(self._skip_data(bfr))
    self.pdinfo = [ PDInfo(unpack('>LL', pdinfo_bs[offset:offset+8]))
                    for offset in range(0, len(pdinfo_bs), 8)
                  ]

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
    for pdinfo in self.pdinfo:
      yield pack('>LL', *pdinfo)

add_box_class(PDINBox)

class ContainerBox(Box):
  ''' A base class for pure container boxes.
  '''

  def parse_data(self, bfr):
    super().parse_data(bfr)
    self.boxes = self.parse_subboxes(bfr, self.end_offset)

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

  def parsed_data_chunks(self):
    yield Box.parsed_data_chunks()
    for B in self.boxes:
      yield B.data_chunks()

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

  def parse_data(self, bfr):
    super().parse_data(bfr)
    # obtain box data after version and flags decode
    if self.version == 0:
      self.creation_time, \
      self.modification_time, \
      self.timescale, \
      self.duration = unpack('>LLLL', bfr.take(16))
    elif self.version == 1:
      self.creation_time, \
      self.modification_time, \
      self.timescale, \
      self.duration = unpack('>QQLQ', bfr.take(28))
    else:
      raise ValueError("MVHD: unsupported version %d" % (self.version,))
    self._rate, self._volume = unpack('>lh', bfr.take(6))
    bfr.take(10)    # 2-reserved, 2x4 reserved
    self.matrix = unpack('>lllllllll', bfr.take(36))
    bfr.take(24)    # 6x4 predefined
    self.next_track_id, = unpack('>L', bfr.take(4))
    if bfr.offset < self.end_offset:
      raise ValueError("MVHD: after decode offset=%d but end_offset=%d"
                       % (bfr.offset, self.end_offset))

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

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
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
add_box_subclass(ContainerBox, 'trak', '8.3.1', 'Track')

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

  def parse_data(self, bfr):
    super().parse_data(bfr)
    # obtain box data after version and flags decode
    if self.version == 0:
      self.creation_time, \
      self.modification_time, \
      self.track_id, \
      self.reserved1, \
      self.duration = unpack('>LLLLL', bfr.take(20))
    elif self.version == 1:
      self.creation_time, \
      self.modification_time, \
      self.track_id, \
      self.reserved1, \
      self.duration = unpack('>QQLLQ', bfr.take(32))
    else:
      raise ValueError("TRHD: unsupported version %d" % (self.version,))
    self.reserved2, self.reserved3, \
    self.layer, \
    self.alternate_group, \
    self.volume, \
    self.reserved4 = unpack('>LLhhhH', bfr.take(16))
    self.matrix = unpack('>lllllllll', bfr.take(36))
    self.width, self.height = unpack('>LL', bfr.take(8))

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

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
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
add_box_subclass(ContainerBox, 'tref', '8.3.3', 'track Reference')

class TrackReferenceTypeBox(Box):
  ''' A TrackReferenceTypeBox continas references to other tracks - ISO14496 section 8.3.3.2.
  '''

  BOX_TYPES = (b'hint', b'cdsc', b'font', b'hind', b'vdep', b'vplx', b'subt')

  def parse_data(self, bfr):
    super().parse_data(bfr)
    track_bs = b''.join(self._skip_data(bfr))
    track_ids = []
    for track_id, in iter_unpack('>L', track_bs):
      track_ids.append(track_id)
    self.track_ids = track_ids

  def __str__(self):
    return '%s(type=%r,track_ids=%r)' % (self.__class__.__name__, self.box_type, self.track_ids)

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
    for track_id in self.track_ids:
      yield pack('>L', track_id)

add_box_class(TrackReferenceTypeBox)
add_box_subclass(ContainerBox, 'trgr', '8.3.4', 'Track Group')

class TrackGroupTypeBox(FullBox):
  ''' A TrackGroupTypeBox contains track group id types - ISO14496 section 8.3.3.2.
  '''
  ATTRIBUTES = ( 'track_group_id', )

  def __init__(self, box_type, box_data):
    FullBox.__init__(self, box_type, box_data)

  def parse_data(self, bfr):
    super().parse_data(bfr)
    # obtain box data after version and flags decode
    self.track_group_id, = unpack('>L', bfr.take(4))

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
    yield pack('>L', self.track_group_id)

add_box_subclass(TrackGroupTypeBox, 'msrc', '8.3.4.3', 'Multi-source presentation Track Group')
add_box_subclass(ContainerBox, 'mdia', '8.4.1', 'Media')

class MDHDBox(FullBox):
  ''' A MDHDBox is a Media Header box - ISO14496 section 8.4.2.
  '''

  ATTRIBUTES = ( 'creation_time',
                 'modification_time',
                 'timescale',
                 'duration',
                 'language' )

  def parse_data(self, bfr):
    super().parse_data(bfr)
    # obtain box data after version and flags decode
    if self.version == 0:
      self.creation_time, \
      self.modification_time, \
      self.timescale, \
      self.duration = unpack('>LLLL', bfr.take(16))
    elif self.version == 1:
      self.creation_time, \
      self.modification_time, \
      self.timescale, \
      self.duration = unpack('>QQLQ', bfr.take(28))
    else:
      raise RuntimeError("unsupported version %d" % (self.version,))
    self._language, \
    self.pre_defined = unpack('>HH', bfr.take(4))
    if bfr.offset != self.end_offset:
      warning("MDHD: %d unparsed bytes", self.end_offset-bfr.offset)

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
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

  def parse_data(self, bfr):
    super().parse_data(bfr)
    # obtain box data after version and flags decode
    # NB: handler_type is supposed to be an unsigned long, but in practice seems to be 4 ASCII bytes, so we load it as a string for readability
    self.pre_defined, \
    self.handler_type, \
    self.reserved1, \
    self.reserved2, \
    self.reserved3 = unpack('>L4sLLL', bfr.take(20))
    name_bs = self._take_tail(bfr)
    self.name, offset = get_utf8_nul(name_bs)
    if offset < len(name_bs):
      raise ValueError('HDLR: extra data after name: %d bytes: %r'
                       % (len(name_bs)-offset, name_bs[offset:]))

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
    yield pack('>L4sLLL',
               self.pre_defined,
               self.handler_type,
               self.reserved1,
               self.reserved2,
               self.reserved3)
    yield put_utf8_nul(self.name)

add_box_class(HDLRBox)
add_box_subclass(ContainerBox, b'minf', '8.4.4', 'Media Information')
add_box_subclass(FullBox, 'nmhd', '8.4.5.2', 'Null Media Header')

class ELNGBox(FullBox):
  ''' A ELNGBox is a Extended Language Tag box - ISO14496 section 8.4.6.
  '''

  ATTRIBUTES = ( 'extended_language', )

  def parse_data(self, bfr):
    super().parse_data(bfr)
    # obtain box data after version and flags decode
    # extended language based on RFC4646
    lang_bs = self._take_tail(bfr)
    self.extended_language, offset = get_utf8_nul(lang_bs)
    if offset < len(lang_bs):
      raise ValueError("ELNG: %d extra bytes in extended_language: %r"
                       % (len(lang_bs)-offset, lang_bs[offset:]))

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
    yield put_utf8_nul(self.extended_language)

add_box_class(ELNGBox)
add_box_subclass(ContainerBox, b'stbl', '8.5.1', 'Sample Table')

class _SampleTableContainerBox(FullBox):
  ''' An intermediate FullBox subclass which contains more boxes.
  '''

  ATTRIBUTES = ()

  def parse_data(self, bfr):
    super().parse_data(bfr)
    # obtain box data after version and flags decode
    entry_count, = unpack('>L', bfr.take(4))
    self.boxes = self.parse_subboxes(bfr, self.end_offset, max_boxes=entry_count)
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

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
    yield pack('>L', len(self.boxes))
    for B in self.boxes:
      for chunk in B.data_chunks():
        yield chunk

add_box_subclass(_SampleTableContainerBox, b'stsd', '8.5.2', 'Sample Description')

class _SampleEntry(Box):
  ''' Superclass of Sample Entry boxes.
  '''

  def parse_data(self, bfr):
    super().parse_data(bfr)
    self.reserved, self.data_reference_index = unpack('>6sH', bfr.take(8))

  def __str__(self):
    prefix = '%s(%r-%r,data_reference_index=%d' \
           % (self.__class__.__name__,
              bytes(self.box_type),
              self.reserved,
              self.data_reference_index)
    attr_summary = self.attribute_summary()
    return prefix + ',' + attr_summary + ')'

  def parsed_data_chunks(self):
    ''' Return the leading reserved bytes and data_reference_index.
        Subclasses need to yield this first from .data_chunks().
    '''
    yield from super().parsed_data_chunks()
    yield pack('>6sH', self.reserved, self.data_reference_index)

class BTRTBox(Box):
  ''' BitRateBox - section 8.5.2.2.
  '''

  ATTRIBUTES = ( 'bufferSizeDB', 'maxBitrate', 'avgBitrate' )

  def parse_data(self, bfr):
    self.bufferSizeDB, \
    self.maxBitrate, \
    self.avgBitrate = unpack('>LLL', self._take_tail(bfr))

  def __str__(self):
    attr_summary = self.attribute_summary()
    return self.__class__.__name__ + '(' + attr_summary + ')'

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
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

  def parse_data(self, bfr, sample_struct_format_v0, sample_fields, sample_struct_format_v1=None, inferred_entry_count=False):
    if sample_struct_format_v1 is None:
      sample_struct_format_v1 = sample_struct_format_v0
    super().parse_data(bfr)
    if self.version == 0:
      S = Struct(sample_struct_format_v0)
    elif self.version == 1:
      S = Struct(sample_struct_format_v1)
    else:
      warning("unsupported version %d, treating like version 1", self.version)
      S = Struct(sample_struct_format_v1)
    self.sample_struct = S
    sample_type = namedtuple(type(self).__name__ + '_Sample',
                             sample_fields)
    self.sample_type = sample_type
    self.inferred_entry_count = inferred_entry_count
    # obtain box data after version and flags decode
    if inferred_entry_count:
      remaining = (self.end_offset-bfr.offset)
      entry_count = remaining // S.size
      remainder = remaining % S.size
      if remainder != 0:
        warning("remaining length %d is not a multiple of len(%s), %d bytes left over: %r",
                remaining, S.size, remainder, box_data[-remainder:])
    else:
      entry_count, = unpack('>L', bfr.take(4))
    samples = []
    for i in range(entry_count):
      sample = sample_type(*S.unpack(bfr.take(S.size)))
      samples.append(sample)
    ##samples.__str__ = lambda self: "%d-samples" % (len(self),)
    self.samples = samples

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunk()
    if not self.inferred_entry_count:
      yield pack('>L', len(self.samples))
    for sample in self.samples:
      yield self.sample_struct.pack(*sample)

class _TimeToSampleBox(_GenericSampleBox):
  ''' Time to Sample box - section 8.6.1.
  '''
  def parse_data(self, bfr):
    super().parse_data(bfr, '>LL', 'count delta')
add_box_subclass(_TimeToSampleBox, b'stts', '8.6.1.2.1', 'Time to Sample')

class CTTSBox(_GenericSampleBox):
  ''' A 'ctts' Composition Time to Sample box - section 8.6.1.3.
  '''
  def parse_data(self, bfr):
    super().parse_data(bfr, '>LL', 'count offset', '>Ll')
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

  def parse_data(self, bfr):
    super().parse_data(bfr)
    if self.version == 0:
      struct_format = '>lllll'
    elif self.version == 1:
      struct_format = '>qqqqq'
    else:
      warning("unsupported version %d, treating like version 1")
      struct_format = '>qqqqq'
    S = self.struct = Struct(struct_format)
    self.compositionToDTSShift, \
    self.leastDecodeToDisplayDelta, \
    self.greatestDecodeToDisplayDelta, \
    self.compositionStartTime, \
    self.compositionEndTime \
      = S.unpack(struct_format, bfr.take(S.size))

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
    yield self.struct.pack(
      self.compositionToDTSShift,
      self.leastDecodeToDisplayDelta,
      self.greatestDecodeToDisplayDelta,
      self.compositionStartTime,
      self.compositionEndTime)

add_box_class(CSLGBox)

class STSSBox(_GenericSampleBox):
  ''' A 'stss' Sync Sample box - section 8.6.2.
  '''
  def parse_data(self, bfr):
    super().parse_data(bfr, '>L', 'number')
add_box_class(STSSBox)

class STSHBox(_GenericSampleBox):
  ''' A 'stsh' Shadow Sync Table box - section 8.6.3.
  '''
  def parse_data(self, bfr):
    super().parse_data(bfr, '>LL', 'shadowed_sample_number sync_sample_number')
add_box_class(STSHBox)

class SDTPBox(_GenericSampleBox):
  ''' A 'sdtp' Independent and Disposable Samples box - section 8.6.4.
  '''
  def parse_data(self, bfr):
    super().parse_data(bfr,
                       '>HHHH',
                       'is_leading sample_depends_on sample_is_depended_on sample_has_redundancy',
                       inferred_entry_count=True)
add_box_class(SDTPBox)
add_box_subclass(Box, b'edts', '8.6.5.1', 'Edit')

class ELSTBox(_GenericSampleBox):
  ''' A 'elst' Edit List box - section 8.6.6.
  '''
  def parse_data(self, bfr):
    super().parse_data(bfr, '>Ll', 'segment_duration media_time', sample_struct_format_v1='>Qq')
add_box_class(ELSTBox)
add_box_subclass(Box, b'dinf', '8.7.1', 'Data Information')

class URL_Box(FullBox):
  ''' An 'url ' Data Entry URL Box - section 8.7.2.1.
  '''

  ATTRIBUTES = ('location',)

  def parse_data(self, bfr):
    super().parse_data(bfr)
    self.location, _ = get_utf8_nul(sefl._take_tail())

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
    yield put_utf8_nul(self.location)

add_box_class(URL_Box)

class URN_Box(FullBox):
  ''' An 'urn ' Data Entry URL Box - section 8.7.2.1.
  '''

  ATTRIBUTES = ('name', 'location',)

  def parse_data(self, bfr):
    super().parse_data(bfr)
    tail_bs = self._take_tail()
    self.name, offset = get_utf8_nul(tail_bs)
    self.location, offset = get_utf8_nul(tail_bs, offset=offset)

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
    yield put_utf8_nul(self.name)
    yield put_utf8_nul(self.location)

add_box_class(URN_Box)

class DREFBox(FullBox):
  ''' A 'dref' Data Reference box containing Data Entry boxes - section 8.7.2.1.
  '''

  def parse_data(self, bfr):
    super().parse_data(bfr)
    entry_count = unpack('>L', bfr.take(4))
    self.boxes = self.parse_subboxes(bfr, self.end_offset, max_boxes=entry_count)

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

  def parsed_data_chunks(self):
    yield from super().parsed_data_chunks()
    for B in self.boxes:
      yield from B.parsed_data_chunks()

add_box_class(DREFBox)

def parse_file(fp, discard=False, copy_offsets=None):
  return parse_chunks(read_from(fp), discard=discard, copy_offsets=copy_offsets)

def parse_chunks(chunks, discard=False, copy_offsets=None):
  return parse_buffer(CornuCopyBuffer(chunks, copy_offsets=copy_offsets),
                      discard=discard)

def parse_buffer(bfr, discard=False, copy_offsets=None):
  if copy_offsets is not None:
    bfr.copy_offsets = copy_offsets
  while True:
    B = Box.from_buffer(bfr, discard_data=discard)
    if B is None:
      break
    yield B

if __name__ == '__main__':
  sys.exit(main(sys.argv))
