#!/usr/bin/python
#
# ISO14496 parser. - Cameron Simpson <cs@cskk.id.au> 26mar2016
#

'''
Facilities for ISO14496 files - the ISO Base Media File Format,
the basis for several things including MP4 and MOV.

ISO make the standard available here:
* [link](http://standards.iso.org/ittf/PubliclyAvailableStandards/index.html)
* [link](http://standards.iso.org/ittf/PubliclyAvailableStandards/c068960_ISO_IEC_14496-12_2015.zip)
'''

from __future__ import print_function
from collections import namedtuple
from functools import partial
import os
from os.path import basename
import stat
import sys
from cs.binary import (
    Packet, PacketField, BytesesField, ListField,
    UInt8, Int16BE, Int32BE, UInt16BE, UInt32BE, UInt64BE,
    UTF8NULField, BytesField, BytesRunField,
    EmptyField, EmptyPacketField,
    multi_struct_field, structtuple,
)
from cs.buffer import CornuCopyBuffer
from cs.logutils import setup_logging, warning, error
from cs.pfx import Pfx
from cs.py.func import prop

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 3 - Alpha",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Topic :: Multimedia :: Video",
    ],
    'install_requires': [],
}

USAGE = '''Usage:
  %s extract [-H] filename boxref output
            Extract the referenced Box from the specified filename into output.
            -H  Skip the Box header.
  %s [parse [{-|filename}]...]
            Parse the named files (or stdin for "-").
  %s test   Run unit tests.'''

def main(argv):
  ''' Module main programme.
  '''
  cmd = basename(argv.pop(0))
  usage = USAGE % (cmd, cmd, cmd)
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
            parsee = sys.stdin.fileno()
          else:
            parsee = spec
          over_box, = parse(parsee)
          over_box.dump()
    elif op == 'extract':
      skip_header = False
      if argv and argv[0] == '-H':
        argv.pop(0)
        skip_header = True
      if not argv:
        warning("missing filename")
        badopts = True
      else:
        filename = argv.pop(0)
      if not argv:
        warning("missing boxref")
        badopts = True
      else:
        boxref = argv.pop(0)
      if not argv:
        warning("missing output")
        badopts = True
      else:
        output = argv.pop(0)
      if argv:
        warning("extra argments after boxref: %s", ' '.join(argv))
        badopts = True
      if not badopts:
        over_box = parse(filename)
        over_box.dump()
        B = over_box
        for box_type_s in boxref.split('.'):
          B = getattr(B, box_type_s.upper())
        with Pfx(filename):
          fd = os.open(filename, os.O_RDONLY)
          bfr = CornuCopyBuffer.from_fd(fd)
          offset = B.offset
          need = B.length
          if skip_header:
            offset += B.header_length
            if need is not None:
              need -= B.header_length
          bfr.seek(offset)
          with Pfx(output):
            with open(output, 'wb') as ofp:
              for chunk in bfr:
                if need is not None and need < len(chunk):
                  chunk = chunk[need]
                ofp.write(chunk)
                need -= len(chunk)
          os.close(fd)
    elif op == 'test':
      import cs.iso14496_tests
      cs.iso14496_tests.selftest(["%s: %s" % (cmd, op)] + argv)
    else:
      warning("unknown op")
      badopts = True
  if badopts:
    print(usage, file=sys.stderr)
    return 2
  return 0

# a convenience chunk of 256 zero bytes, mostly for use by 'free' blocks
B0_256 = bytes(256)

# an arbitrary maximum read size for fetching the data section
SIZE_16MB = 1024*1024*16

class BoxHeader(Packet):
  ''' An ISO14496 Box header packet.
  '''

  # speculative max size that will fit in the UInt32BE box_size
  # with room for bigger sizes in the optional UInt64BE length field
  MAX_BOX_SIZE_32 = 2 ^ 32 - 8

  PACKET_FIELDS = {
      'box_size': UInt32BE,
      'box_type': BytesField,
      'length': (
          True,
          (
              UInt64BE,
              EmptyPacketField,
          ),
      ),
  }

  @classmethod
  def from_buffer(cls, bfr):
    ''' Decode a box header from the CornuCopyBuffer `bfr`.
    '''
    header = cls()
    # note start of header
    header.offset = bfr.offset
    box_size = header.add_from_buffer('box_size', bfr, UInt32BE)
    box_type = header.add_from_buffer('box_type', bfr, 4)
    if box_size == 0:
      # box extends to end of data/file
      header._length = Ellipsis
      header.add_field('length', EmptyField)
    elif box_size == 1:
      # 64 bit length
      header._length = header.add_from_buffer('length', bfr, UInt64BE)
    else:
      # other box_size values are the length
      header._length = box_size
      header.add_field('length', EmptyField)
    if box_type == b'uuid':
      # user supplied 16 byte type
      header.add_from_buffer('user_type', bfr, 16)
    else:
      header.user_type = None
    # note end of header
    header.end_offset = bfr.offset
    header.type = box_type
    header.self_check()
    return header

  @property
  def length(self):
    ''' The overall packet length.
    '''
    return self._length

  @length.setter
  def length(self, new_length):
    ''' Set a new length value, and configure the associated
        PacketFields to match.
    '''
    if new_length is Ellipsis:
      self.set_field('box_size', UInt32BE(0))
      self.set_field('length', EmptyField)
    elif new_length > self.MAX_BOX_SIZE_32:
      self.set_field('box_size', UInt32BE(new_length))
      self.set_field('length', EmptyField)
    elif new_length >= 1:
      self.set_field('box_size', UInt32BE(1))
      self.set_field('length', UInt64BE(new_length))
    else:
      raise ValueError("invalid new_length %r" % (new_length,))
    self._length = new_length

class BoxBody(Packet):
  ''' Abstract basis for all Box bodies.
  '''

  PACKET_FIELDS = {}

  @classmethod
  def from_buffer(cls, bfr, box=None, **kw):
    ''' Create a BoxBody and fill it in via its `parse_buffer` method.

        Note that this function is expected to be called from
        `Box.from_buffer` and therefore that `bfr` is expected to
        be a bounded CornuCopyBuffer if the Box length is specified.
        Various BoxBodies gather some data "until the end of the
        Box", and we rely on this bound rather than keeping a close
        eye on some unsupplied "end offset" value.
    '''
    B = cls()
    B.box = box
    B.start_offset = bfr.offset
    B.parse_buffer(bfr, **kw)
    B.self_check()
    return B

  def parse_buffer(
      self, bfr, end_offset=None,
      discard_data=False, copy_boxes=None, **kw
  ):
    ''' Gather the Box body fields from `bfr`.

        A generic BoxBody has no additional fields. Subclasses call
        their superclass' `parse_buffer` and then gather their
        specific fields.
    '''
    if kw:
      raise ValueError("unexpected keyword arguments: %r" % (kw,))

  @classmethod
  def boxbody_type_from_klass(cls):
    ''' Compute the Box's 4 byte type field from the class name.
    '''
    class_name = cls.__name__
    if len(class_name) == 11 and class_name.endswith('BoxBody'):
      class_prefix = class_name[:4]
      if class_prefix.rstrip('_').isupper():
        return class_prefix.replace('_', ' ').lower().encode('ascii')
    raise AttributeError("no automatic box type for %s" % (cls,))

class Box(Packet):
  ''' Base class for all boxes - ISO14496 section 4.2.

      This has the following PacketFields:
      * `header`: a BoxHeader
      * `body`: a BoxBody instance, usually a specific subclass
      * `unparsed`: if there are unconsumed bytes from the Box they
        are stored as here as a BytesesField; note that this field
        is not present if there were no unparsed bytes
  '''

  PACKET_FIELDS = {
      'header': BoxHeader,
      'body': BoxBody,
      'unparsed': (True, (BytesesField, EmptyPacketField)),
  }

  def __init__(self, parent=None):
    super().__init__()
    self.parent = parent

  def __str__(self):
    type_name = self.box_type_path
    try:
      body = self.body
    except AttributeError:
      s = "%s:NO_BODY" % (type_name,)
    else:
      s = "%s:%s" % (type_name, body)
    unparsed = self.unparsed
    if unparsed:
      s += ":unparsed=%d" % (len(unparsed,))
    return s

  def transcribe(self):
    ''' Transcribe the Box.

        Before transcribing the data, we compute the length from the
        lengths of the current header, body and unparsed components,
        then set the header length if that has changed. Since setting
        the header length can change its representation we compute
        the length again and abort if it isn't stable. Otherwise
        we proceeed with a regular transcription.
    '''
    header = self.header
    length = header.length
    if length is Ellipsis:
      body = self.body
      unparsed = self.unparsed
      # Recompute the length from the header, body and unparsed
      # components, then set it on the header to get the prepare
      # transcription.
      new_length = len(header) + len(body) + len(unparsed)
      # set the header and then check that it matches
      header.length = new_length
      if new_length != len(header) + len(body) + len(unparsed):
        # the header has changed size because we changed the length, try again
        new_length = len(header) + len(body) + len(unparsed)
        # set the header and then check that it matches
        header.length = new_length
        if new_length != len(header) + len(body) + len(unparsed):
          # the header has changed size again, unstable, need better algorithm
          raise RuntimeError(
              "header size unstable, maybe we need a header mode to force the representation")
    return super().transcribe()

  def self_check(self):
    ''' Sanity check this Box.
    '''
    super().self_check()
    # sanity check the supplied box_type
    # against the box types this class supports
    with Pfx("%s", self):
      box_type = self.header.type
      try:
        BOX_TYPE = self.BOX_TYPE
      except AttributeError:
        try:
          BOX_TYPES = self.BOX_TYPES
        except AttributeError:
          if not isinstance(self, Box):
            raise RuntimeError(
                "no BOX_TYPE or BOX_TYPES to check in class %r"
                % (type(self),))
        else:
          if box_type not in BOX_TYPES:
            warning(
                "box_type should be in %r but got %r",
                BOX_TYPES, bytes(box_type))
      else:
        if box_type != BOX_TYPE:
          warning(
              "box_type should be %r but got %r",
              BOX_TYPE, box_type)
      parent = self.parent
      if parent is not None and not isinstance(parent, Box):
        warning( "parent should be a Box, but is %r", type(self))

  @classmethod
  def from_buffer(cls, bfr, discard_data=False, default_type=None, copy_boxes=None):
    ''' Decode a Box from `bfr` and return it.

        Parameters:
        * `bfr`: the input CornuCopyBuffer
        * `discard_data`: if false (default), keep the unparsed data portion as
          a list of data chunks in the field .unparsed; if true, discard the
          unparsed data
        * `default_type`: default Box body type if no class is
          registered for the header box type.
        * `copy_boxes`: optional callable for reporting new Box instances

        This provides the Packet.from_buffer method, but offloads
        the actual parse to the method `parse_buffer`, which is
        overridden by subclasses.
    '''
    B = cls()
    B.offset = bfr.offset
    try:
      B.parse_buffer(bfr, discard_data=discard_data, default_type=default_type)
    except EOFError as e:
      error("%s.parse_buffer: EOF parsing buffer: %s", type(B), e)
    B.end_offset = bfr.offset
    B.self_check()
    if copy_boxes:
      copy_boxes(B)
    return B

  def parse_buffer(
      self, bfr,
      default_type=None, copy_boxes=None,
      **kw
  ):
    ''' Parse the Box from `bfr`.

        Parameters:
        * `bfr`: the input CornuCopyBuffer
          unparsed data
        * `default_type`: default Box body type if no class is
          registered for the header box type.
        * `copy_boxes`: optional callable for reporting new Box instances
        Other parameters are passed to the inner parse calls.

        This method should be overridden by subclasses (if any,
        since the actual subclassing happens with the BoxBody base
        class).
    '''
    header = self.add_from_buffer('header', bfr, BoxHeader)
    bfr.report_offset(self.offset)
    length = header.length
    if length is Ellipsis:
      end_offset = Ellipsis
      bfr_tail = bfr
      warning("Box.parse_buffer: Box %s has no length", header)
    else:
      end_offset = self.offset + length
      bfr_tail = bfr.bounded(end_offset)
    body_class = pick_boxbody_class(header.type, default_type=default_type)
    with Pfx("parse(%s:%s)", body_class.__name__, self.box_type_s):
      try:
        self.add_from_buffer(
            'body', bfr_tail, body_class,
            box=self, copy_boxes=copy_boxes, end_offset=Ellipsis, **kw)
      except EOFError as e:
        # TODO: recover the data already collected but lost
        error("EOFError parsing %s: %s", body_class, e)
        self.add_field('body', EmptyField)
      # advance over the remaining data, optionally keeping it
      self.unparsed_offset = bfr_tail.offset
      if (
          not bfr_tail.at_eof()
          if end_offset is Ellipsis
          else end_offset > bfr_tail.offset
      ):
        # there are unparsed data, stash it away and emit a warning
        self.add_from_buffer(
            'unparsed', bfr_tail, BytesesField,
            end_offset=Ellipsis, **kw)
        warning(
            "%s:%s: unparsed data: %d bytes",
            type(self).__name__, self.box_type_s, len(self['unparsed']))
      else:
        self.add_field('unparsed', EmptyField)
      if bfr_tail is not bfr:
        bfr_tail.flush()

  def __getattr__(self, attr):
    # .TYPE - the sole item in self.boxes matching b'type'
    if len(attr) == 4 and all(c.isupper() for c in attr):
      box, = getattr(self, attr + 's')
      return box
    # .TYPEs - all items of self.boxes matching b'type'
    if len(attr) == 5 and attr.endswith('s'):
      attr4 = attr[:4]
      if all(c.isupper() for c in attr4):
        box_type = attr4.lower().encode('ascii')
        boxes = [ box for box in self.boxes if box.box_type == box_type ]
        return boxes
    return super().__getattr__(attr)

  @property
  def box_type(self):
    ''' The Box header type.
    '''
    return self.header.type

  @property
  def box_type_s(self):
    ''' The Box header type as a string.

        If the header type bytes decode as ASCII, return that,
        otherwise the header bytes' repr().
    '''
    box_type_b = bytes(self.box_type)
    try:
      box_type_name = box_type_b.decode('ascii')
    except UnicodeDecodeError:
      box_type_name = repr(box_type_b)
    return box_type_name

  @property
  def box_type_path(self):
    ''' The type path to this Box.
    '''
    types = [self.box_type_s]
    box = self.parent
    while box is not None:
      try:
        path_elem = box.box_type_s
      except AttributeError as e:
        raise RuntimeError(
            "%s.box_type_path: no .box_type_s on %r: %s"
            % (type(self).__name__, box, e))
      types.append(path_elem)
      box = box.parent
    return '.'.join(reversed(types))

  @property
  def user_type(self):
    ''' The header user_type.
    '''
    return self.header.user_type

  # NB: a @property instead of @prop to preserve AttributeError
  @property
  def BOX_TYPE(self):
    ''' The default .BOX_TYPE is inferred from the class name.
    '''
    return type(self).boxbody_type_from_klass()

  def dump(self, **kw):
    ''' Dump this Box.
    '''
    return dump_box(self, **kw)

# mapping of known box subclasses for use by factories
KNOWN_BOXBODY_CLASSES = {}

def add_body_class(klass):
  ''' Register a box body class in KNOWN_BOXBODY_CLASSES.
  '''
  global KNOWN_BOXBODY_CLASSES
  with Pfx("add_body_class(%s)", klass):
    try:
      box_types = klass.BOX_TYPES
    except AttributeError:
      box_type = klass.boxbody_type_from_klass()
      box_types = (box_type,)
    for box_type in box_types:
      if box_type in KNOWN_BOXBODY_CLASSES:
        raise TypeError("box_type %r already in KNOWN_BOXBODY_CLASSES as %s"
                        % (box_type, KNOWN_BOXBODY_CLASSES[box_type]))
      KNOWN_BOXBODY_CLASSES[box_type] = klass

def add_body_subclass(superclass, box_type, section, desc):
  ''' Create and register a new BoxBody class that is simply a subclass of
      another.  Returns the new class.
  '''
  if isinstance(box_type, bytes):
    classname = box_type.decode('ascii').upper() + 'BoxBody'
  else:
    classname = box_type.upper() + 'BoxBody'
    box_type = box_type.encode('ascii')
  K = type(classname, (superclass,), {})
  K.__doc__ = (
      "Box type %r %s box - ISO14496 section %s."
      % (box_type, desc, section)
  )
  add_body_class(K)
  return K

def pick_boxbody_class(box_type, default_type=None):
  ''' Infer a Python BoxBody subclass from the bytes `box_type`.

      * `box_type`: the 4 byte box type
      * `default_type`: the default BoxBody subclass if there is no
        specific mapping, default None; if None, use BoxBody.
  '''
  global KNOWN_BOXBODY_CLASSES
  if default_type is None:
    default_type = BoxBody
  return KNOWN_BOXBODY_CLASSES.get(box_type, default_type)

class SubBoxesField(ListField):
  ''' A field which is itself a list of Boxes.
  '''

  @classmethod
  def from_buffer(
      cls,
      bfr,
      end_offset=None, max_boxes=None,
      default_type=None,
      copy_boxes=None,
      parent=None,
      **kw
  ):
    ''' Read Boxes from `bfr`, return a new SubBoxesField instance.

        Parameters:
        * `bfr`: the buffer
        * `end_offset`: the ending offset of the input data, be an offset or
          `Ellipsis` indicating "consume to end of buffer"; default: Ellipsis
        * `max_boxes`: optional maximum number of Boxes to parse
        * `default`: a default Box subclass for box_types without a
          registered subclass
        * `copy_boxes`: optional callable to receive parsed Boxes
        * `parent`: optional parent Box to record against parsed Boxes
    '''
    if end_offset is None:
      raise ValueError("SubBoxesField.from_buffer: missing end_offset")
    boxes = []
    boxes_field = cls(boxes)
    while (
        (max_boxes is None or len(boxes) < max_boxes)
        and (end_offset is Ellipsis or bfr.offset < end_offset)
        and not bfr.at_eof()
    ):
      B = Box.from_buffer(bfr, default_type=default_type, copy_boxes=copy_boxes, **kw)
      B.parent = parent
      boxes.append(B)
    if end_offset is not Ellipsis and bfr.offset > end_offset:
      raise ValueError(
          "contained Boxes overran end_offset:%d by %d bytes"
          % (end_offset, bfr.offset - end_offset))
    return boxes_field

class OverBox(Packet):
  ''' A fictitious Box encompassing all the Boxes in an input buffer.
  '''

  PACKET_FIELDS = {
      'boxes': SubBoxesField,
  }

  @classmethod
  def from_buffer(cls, bfr, end_offset=None, **kw):
    ''' Parse all the Boxes from the input `bfr`.

        Parameters:
        * `end_offset`: optional ending offset for the parse
    '''
    if end_offset is None:
      end_offset = Ellipsis
    box = cls()
    box.add_from_buffer('boxes', bfr, SubBoxesField, end_offset=end_offset, **kw)
    box.self_check()
    return box

  def dump(self, **kw):
    ''' Dump this OverBox.
    '''
    return dump_box(self, **kw)

class FullBoxBody(BoxBody):
  ''' A common extension of a basic BoxBody, with a version and flags field.
      ISO14496 section 4.2.
  '''

  PACKET_FIELDS = dict(
      BoxBody.PACKET_FIELDS,
      version=UInt8,
      flags0=UInt8,
      flags1=UInt8,
      flags2=UInt8,
  )

  def parse_buffer(self, bfr, **kw):
    super().parse_buffer(bfr, **kw)
    self.add_field('version', UInt8.from_buffer(bfr))
    self.add_field('flags0', UInt8.from_buffer(bfr))
    self.add_field('flags1', UInt8.from_buffer(bfr))
    self.add_field('flags2', UInt8.from_buffer(bfr))

  @property
  def flags(self):
    ''' The flags value, computed from the 3 flag bytes.
    '''
    return (self.flags0<<16) | (self.flags1<<8) | self.flags2

class MDATBoxBody(BoxBody):
  ''' A Media Data Box - ISO14496 section 8.1.1.
  '''

  PACKET_FIELDS = dict(
      BoxBody.PACKET_FIELDS,
      data=BytesesField,
  )

  def parse_buffer(self, bfr, end_offset=Ellipsis, discard_data=False, **kw):
    ''' Gather all data to the end of the field.
    '''
    super().parse_buffer(bfr, **kw)
    self.add_from_buffer(
        'data', bfr, BytesesField,
        end_offset=end_offset, discard_data=discard_data)

add_body_class(MDATBoxBody)

class FREEBoxBody(BoxBody):
  ''' A 'free' or 'skip' box - ISO14496 section 8.1.2.
      Note the length and discard the data portion.
  '''

  PACKET_FIELDS = dict(
      BoxBody.PACKET_FIELDS,
      padding=BytesRunField,
  )

  BOX_TYPES = (b'free', b'skip')

  def parse_buffer(self, bfr, end_offset=Ellipsis, **kw):
    ''' Gather the `padding` field.
    '''
    super().parse_buffer(bfr, **kw)
    offset0 = bfr.offset
    self.add_from_buffer('padding', bfr, BytesRunField, end_offset=end_offset)
    self.free_size = bfr.offset - offset0

add_body_class(FREEBoxBody)

class FTYPBoxBody(BoxBody):
  ''' An 'ftyp' File Type box - ISO14496 section 4.3.
      Decode the major_brand, minor_version and compatible_brands.
  '''

  PACKET_FIELDS = dict(
      BoxBody.PACKET_FIELDS,
      major_brand=BytesField,
      minor_version=UInt32BE,
      brands_bs=BytesField,
  )

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `major_brand`, `minor_version` and `brand_bs` fields.
    '''
    super().parse_buffer(bfr, **kw)
    self.add_from_buffer('major_brand', bfr, 4)
    self.add_from_buffer('minor_version', bfr, UInt32BE)
    brands_bs = b''.join(bfr)
    self.add_field('brands_bs', BytesField(brands_bs))

  @property
  def compatible_brands(self):
    ''' The compatible brands as a list of 4 bytes bytes instances.
    '''
    return [
        self.brands_bs[offset:offset+4]
        for offset in range(0, len(self.brands_bs), 4)
    ]

add_body_class(FTYPBoxBody)

class PDINBoxBody(FullBoxBody):
  ''' An 'pdin' Progressive Download Information box - ISO14496 section 8.1.3.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      pdinfo=ListField,
  )

  # field names for the tuples in a PDINBoxBody
  PDInfo = structtuple('PDInfo', '>LL', 'rate initial_delay')

  def parse_buffer(self, bfr, **kw):
    ''' Gather the (rate, initial_delay) pairs of the data section as the `pdinfo` field.
    '''
    super().parse_buffer(bfr, **kw)
    # obtain box data after version and flags decode
    pdinfo = []
    while not bfr.at_eof():
      pdinfo.append(PDINBoxBody.PDInfo.from_buffer(bfr))
    self.add_field('pdinfo', ListField(pdinfo))

add_body_class(PDINBoxBody)

class ContainerBoxBody(BoxBody):
  ''' A base class for pure container boxes.
  '''

  PACKET_FIELDS = dict(
      BoxBody.PACKET_FIELDS,
      boxes=SubBoxesField,
  )

  def parse_buffer(self, bfr, default_type=None, copy_boxes=None, **kw):
    ''' Gather the `boxes` field.
    '''
    super().parse_buffer(bfr, copy_boxes=copy_boxes, **kw)
    self.add_from_buffer(
        'boxes', bfr, SubBoxesField,
        end_offset=Ellipsis, default_type=default_type, parent=self.box)

class MOOVBoxBody(ContainerBoxBody):
  ''' An 'moov' Movie box - ISO14496 section 8.2.1.
      Decode the contained boxes.
  '''
  pass
add_body_class(MOOVBoxBody)

class MVHDBoxBody(FullBoxBody):
  ''' An 'mvhd' Movie Header box - ISO14496 section 8.2.2.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      creation_time=(True, (UInt32BE, UInt64BE)),
      modification_time=(True, (UInt32BE, UInt64BE)),
      timescale=UInt32BE,
      duration=(True, (UInt32BE, UInt64BE)),
      rate_long=Int32BE,
      volume_short=Int16BE,
      reserved1=BytesField,
      matrix=PacketField,
      predefined1=BytesField,
      next_track_id=UInt32BE,
  )

  def parse_buffer(self, bfr, **kw):
    super().parse_buffer(bfr, **kw)
    # obtain box data after version and flags decode
    if self.version == 0:
      self.add_from_buffer('creation_time', bfr, UInt32BE)
      self.add_from_buffer('modification_time', bfr, UInt32BE)
      self.add_from_buffer('timescale', bfr, UInt32BE)
      self.add_from_buffer('duration', bfr, UInt32BE)
    elif self.version == 1:
      self.add_from_buffer('creation_time', bfr, UInt64BE)
      self.add_from_buffer('modification_time', bfr, UInt64BE)
      self.add_from_buffer('timescale', bfr, UInt32BE)
      self.add_from_buffer('duration', bfr, UInt64BE)
    else:
      raise ValueError("MVHD: unsupported version %d" % (self.version,))
    self.add_from_buffer('rate_long', bfr, Int32BE)
    self.add_from_buffer('volume_short', bfr, Int16BE)
    self.add_from_buffer('reserved1', bfr, 10)      # 2-reserved, 2x4 reserved
    self.add_from_buffer('matrix', bfr, multi_struct_field('>lllllllll'))
    self.add_from_buffer('predefined1', bfr, 24)    # 6x4 predefined
    self.add_from_buffer('next_track_id', bfr, UInt32BE)

  @prop
  def rate(self):
    ''' Rate field converted to float: 1.0 represents normal rate.
    '''
    rate_long = self.rate_long
    return (rate_long>>16) + (rate_long&0xffff)/65536.0

  @prop
  def volume(self):
    ''' Volume field converted to float: 1.0 represents full volume.
    '''
    volume_short = self.volume_short
    return (volume_short>>8) + (volume_short&0xff)/256.0

add_body_class(MVHDBoxBody)

add_body_subclass(ContainerBoxBody, 'trak', '8.3.1', 'Track')

class TKHDBoxBody(FullBoxBody):
  ''' An 'tkhd' Track Header box - ISO14496 section 8.2.2.
  '''
  TKHDMatrix = multi_struct_field('>lllllllll', class_name='TKHDMatrix')

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      creation_time=(True, (UInt32BE, UInt64BE)),
      modification_time=(True, (UInt32BE, UInt64BE)),
      track_id=UInt32BE,
      reserved1=UInt32BE,
      duration=(True, (UInt32BE, UInt64BE)),
      reserved2=UInt32BE,
      reserved3=UInt32BE,
      layer=Int16BE,
      alternate_group=Int16BE,
      volume=Int16BE,
      reserved4=UInt16BE,
      matrix=TKHDMatrix,
      width=UInt32BE,
      height=UInt32BE,
  )

  def parse_buffer(self, bfr, **kw):
    super().parse_buffer(bfr, **kw)
    # obtain box data after version and flags decode
    if self.version == 0:
      self.add_from_buffer('creation_time', bfr, UInt32BE)
      self.add_from_buffer('modification_time', bfr, UInt32BE)
      self.add_from_buffer('track_id', bfr, UInt32BE)
      self.add_from_buffer('reserved1', bfr, UInt32BE)
      self.add_from_buffer('duration', bfr, UInt32BE)
    elif self.version == 1:
      self.add_from_buffer('creation_time', bfr, UInt64BE)
      self.add_from_buffer('modification_time', bfr, UInt64BE)
      self.add_from_buffer('track_id', bfr, UInt32BE)
      self.add_from_buffer('reserved1', bfr, UInt32BE)
      self.add_from_buffer('duration', bfr, UInt64BE)
    else:
      raise ValueError("TRHD: unsupported version %d" % (self.version,))
    self.add_from_buffer('reserved2', bfr, UInt32BE)
    self.add_from_buffer('reserved3', bfr, UInt32BE)
    self.add_from_buffer('layer', bfr, Int16BE)
    self.add_from_buffer('alternate_group', bfr, Int16BE)
    self.add_from_buffer('volume', bfr, Int16BE)
    self.add_from_buffer('reserved4', bfr, UInt16BE)
    self.add_from_buffer('matrix', bfr, TKHDBoxBody.TKHDMatrix)
    self.add_from_buffer('width', bfr, UInt32BE)
    self.add_from_buffer('height', bfr, UInt32BE)

  @prop
  def track_enabled(self):
    ''' Test flags bit 0, 0x1, track_enabled.
    '''
    return (self.flags&0x1) != 0

  @prop
  def track_in_movie(self):
    ''' Test flags bit 1, 0x2, track_in_movie.
    '''
    return (self.flags&0x2) != 0

  @prop
  def track_in_preview(self):
    ''' Test flags bit 2, 0x4, track_in_preview.
    '''
    return (self.flags&0x4) != 0

  @prop
  def track_size_is_aspect_ratio(self):
    ''' Test flags bit 3, 0x8, track_size_is_aspect_ratio.
    '''
    return (self.flags&0x8) != 0

add_body_class(TKHDBoxBody)

##add_body_subclass(ContainerBoxBody, 'tref', '8.3.3', 'track Reference')

class TREFBoxBody(ContainerBoxBody):
  ''' Track Reference BoxBody, container for trackReferenceTypeBoxes - ISO14496 section 8.3.3.
  '''

  def parse_buffer(self, bfr, **kw):
    ''' Arrange that `default_type=TrackReferenceTypeBoxBody` in `TREFBoxBody` parses.
    '''
    super().parse_buffer(bfr, default_type=TrackReferenceTypeBoxBody, **kw)

add_body_class(TREFBoxBody)

class TrackReferenceTypeBoxBody(BoxBody):
  ''' A TrackReferenceTypeBoxBody contains references to other tracks - ISO14496 section 8.3.3.2.
  '''

  BOX_TYPES = (b'hint', b'cdsc', b'font', b'hind', b'vdep', b'vplx', b'subt')

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `track_ids` field.
    '''
    super().parse_buffer(bfr, **kw)
    track_ids = []
    while not bfr.at_eof():
      track_ids.append(UInt32BE.from_buffer(bfr))
    self.add_field('track_ids', ListField(track_ids))

add_body_class(TrackReferenceTypeBoxBody)
add_body_subclass(ContainerBoxBody, 'trgr', '8.3.4', 'Track Group')

class TrackGroupTypeBoxBody(FullBoxBody):
  ''' A TrackGroupTypeBoxBody contains a track group id - ISO14496 section 8.3.3.2.
  '''

  def __init__(self, box_type, box_data):
    FullBoxBody.__init__(self, box_type, box_data)

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `track_group_id` field.
    '''
    super().parse_buffer(bfr, **kw)
    self.add_from_buffer('track_group_id', bfr, UInt32BE)

add_body_subclass(TrackGroupTypeBoxBody, 'msrc', '8.3.4.3', 'Multi-source presentation Track Group')
add_body_subclass(ContainerBoxBody, 'mdia', '8.4.1', 'Media')

class MDHDBoxBody(FullBoxBody):
  ''' A MDHDBoxBody is a Media Header box - ISO14496 section 8.4.2.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      creation_time=(True, (UInt32BE, UInt64BE)),
      modification_time=(True, (UInt32BE, UInt64BE)),
      timescale=UInt32BE,
      duration=(True, (UInt32BE, UInt64BE)),
      language_short=UInt16BE,
      pre_defined=UInt16BE,
  )

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `creation_time`, `modification_time`, `timescale`,
        `duration` and `language_short` fields.
    '''
    super().parse_buffer(bfr, **kw)
    # obtain box data after version and flags decode
    if self.version == 0:
      self.add_from_buffer('creation_time', bfr, UInt32BE)
      self.add_from_buffer('modification_time', bfr, UInt32BE)
      self.add_from_buffer('timescale', bfr, UInt32BE)
      self.add_from_buffer('duration', bfr, UInt32BE)
    elif self.version == 1:
      self.add_from_buffer('creation_time', bfr, UInt64BE)
      self.add_from_buffer('modification_time', bfr, UInt64BE)
      self.add_from_buffer('timescale', bfr, UInt32BE)
      self.add_from_buffer('duration', bfr, UInt64BE)
    else:
      raise RuntimeError("unsupported version %d" % (self.version,))
    self.add_from_buffer('language_short', bfr, UInt16BE)
    self.add_from_buffer('pre_defined', bfr, UInt16BE)

  @prop
  def language(self):
    ''' The ISO 639‐2/T language code as decoded from the packed form.
    '''
    language_short = self.language_short
    return bytes([ x + 0x60
                   for x in ( (language_short>>10)&0x1f,
                              (language_short>>5)&0x1f,
                              language_short&0x1f
                            )
                 ]).decode('ascii')

add_body_class(MDHDBoxBody)

class HDLRBoxBody(FullBoxBody):
  ''' A HDLRBoxBody is a Handler Reference box - ISO14496 section 8.4.3.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      pre_defined=UInt32BE,
      handler_type_long=UInt32BE,
      reserved1=UInt32BE,
      reserved2=UInt32BE,
      reserved3=UInt32BE,
      name=UTF8NULField,
  )

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `handler_type_long` and `name` fields.
    '''
    super().parse_buffer(bfr, **kw)
    # NB: handler_type is supposed to be an unsigned long, but in
    # practice seems to be 4 ASCII bytes, so we load it as a string
    # for readability
    self.add_from_buffer('pre_defined', bfr, UInt32BE)
    self.add_from_buffer('handler_type_long', bfr, UInt32BE)
    self.add_from_buffer('reserved1', bfr, UInt32BE)
    self.add_from_buffer('reserved2', bfr, UInt32BE)
    self.add_from_buffer('reserved3', bfr, UInt32BE)
    self.add_from_buffer('name', bfr, UTF8NULField)

  @property
  def handler_type(self):
    ''' The handler_type as an ASCII string, its usual form.
    '''
    return bytes(self.handler_type_long).decode('ascii')

add_body_class(HDLRBoxBody)
add_body_subclass(ContainerBoxBody, b'minf', '8.4.4', 'Media Information')
add_body_subclass(FullBoxBody, 'nmhd', '8.4.5.2', 'Null Media Header')

class ELNGBoxBody(FullBoxBody):
  ''' A ELNGBoxBody is a Extended Language Tag box - ISO14496 section 8.4.6.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      extended_language=UTF8NULField,
  )

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `extended_language` field.
    '''
    super().parse_buffer(bfr, **kw)
    # extended language based on RFC4646
    self.add_from_buffer('extended_language', bfr, UTF8NULField)

add_body_class(ELNGBoxBody)
add_body_subclass(ContainerBoxBody, b'stbl', '8.5.1', 'Sample Table')

class _SampleTableContainerBoxBody(FullBoxBody):
  ''' An intermediate FullBoxBody subclass which contains more boxes.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      entry_count=UInt32BE,
      boxes=SubBoxesField,
  )

  def parse_buffer(self, bfr, copy_boxes=None, **kw):
    ''' Gather the `entry_count` and `boxes`.
    '''
    super().parse_buffer(bfr, copy_boxes=copy_boxes, **kw)
    # obtain box data after version and flags decode
    entry_count = self.add_from_buffer('entry_count', bfr, UInt32BE)
    boxes = self.add_from_buffer(
        'boxes', bfr, SubBoxesField,
        end_offset=Ellipsis,
        max_boxes=entry_count,
        parent=self.box,
        copy_boxes=copy_boxes)
    if len(boxes) != entry_count:
      raise ValueError(
          "expected %d contained Boxes but parsed %d"
          % (entry_count, len(boxes)))

add_body_subclass(_SampleTableContainerBoxBody, b'stsd', '8.5.2', 'Sample Description')

class _SampleEntry(BoxBody):
  ''' Superclass of Sample Entry boxes.
  '''

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `data_reference_inde` field.
    '''
    super().parse_buffer(bfr, **kw)
    self.add_from_buffer('reserved', bfr, BytesField, length=6)
    self.add_from_buffer('data_reference_index', bfr, UInt16BE)

class BTRTBoxBody(BoxBody):
  ''' BitRateBoxBody - section 8.5.2.2.
  '''

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `bufferSizeDB`, `maxBitrate` and `avgBitrate` fields.
    '''
    super().parse_buffer(bfr, **kw)
    self.add_from_buffer('bufferSizeDB', bfr, UInt32BE)
    self.add_from_buffer('maxBitrate', bfr, UInt32BE)
    self.add_from_buffer('avgBitRate', bfr, UInt32BE)

add_body_class(BTRTBoxBody)
add_body_subclass(_SampleTableContainerBoxBody, b'stdp', '8.5.3', 'Degradation Priority')

TTSB_Sample = namedtuple('TTSB_Sample', 'count delta')

def add_generic_sample_boxbody(
    box_type, section, desc,
    struct_format_v0,
    sample_fields,
    struct_format_v1=None,
    has_inferred_entry_count=False,
):
  ''' Create and add a specific Time to Sample box - section 8.6.1.
  '''
  if struct_format_v1 is None:
    struct_format_v1 = struct_format_v0
  class_name = box_type.decode('ascii').upper() + 'BoxBody'
  sample_class_name = class_name + 'Sample'
  sample_type_v0 = structtuple(
      sample_class_name + 'V0', struct_format_v0, sample_fields)
  sample_type_v1 = structtuple(
      sample_class_name + 'V1', struct_format_v1, sample_fields)
  class SpecificSampleBoxBody(FullBoxBody):
    ''' Time to Sample box - section 8.6.1.
    '''
    PACKET_FIELDS = dict(
        FullBoxBody.PACKET_FIELDS,
        entry_count=(False, UInt32BE),
        samples=ListField,
    )
    def parse_buffer(self, bfr, **kw):
      super().parse_buffer(bfr, **kw)
      if self.version == 0:
        sample_type = self.sample_type = sample_type_v0
      elif self.version == 1:
        sample_type = self.sample_type = sample_type_v1
      else:
        warning("unsupported version %d, treating like version 1", self.version)
        sample_type = self.sample_type = sample_type_v1
      self.has_inferred_entry_count = has_inferred_entry_count
      if has_inferred_entry_count:
        entry_count = Ellipsis
      else:
        entry_count = self.add_from_buffer('entry_count', bfr, UInt32BE)
      samples = []
      with Pfx("gather samples of type %s", sample_type):
        while entry_count is Ellipsis or entry_count > 0:
          if bfr.at_eof():
            if entry_count is not Ellipsis:
              error(
                  "expected %d more %r samples",
                  entry_count, sample_type.__name__)
            break
          try:
            samples.append(sample_type.from_buffer(bfr))
          except EOFError as e:
            error("incomplete %r samples: %s", sample_type.__name__, e)
            break
          if entry_count is not Ellipsis:
            entry_count -= 1
      self.add_field('samples', ListField(samples))
  SpecificSampleBoxBody.__name__ = class_name
  SpecificSampleBoxBody. __doc__ = (
      "Box type %r %s box - ISO14496 section %s."
      % (box_type, desc, section)
  )
  # we define these here because the names collide with the closure
  SpecificSampleBoxBody.struct_format_v0 = struct_format_v0
  SpecificSampleBoxBody.sample_type_v0 = sample_type_v0
  SpecificSampleBoxBody.struct_format_v1 = struct_format_v1
  SpecificSampleBoxBody.sample_type_v1 = sample_type_v1
  add_body_class(SpecificSampleBoxBody)
  return SpecificSampleBoxBody

def add_time_to_sample_boxbody(box_type, section, desc):
  ''' Add a Time to Sample box - section 8.6.1.
  '''
  return add_generic_sample_boxbody(
      box_type, section, desc,
      '>LL', 'count delta',
      has_inferred_entry_count=False,
  )

add_time_to_sample_boxbody(b'stts', '8.6.1.2.1', 'Time to Sample')

add_generic_sample_boxbody(
    b'ctts', '8.6.1.3', 'Composition Time to Sample',
    '>LL', 'count offset', '>Ll')

class CSLGBoxBody(FullBoxBody):
  ''' A 'cslg' Composition to Decode box - section 8.6.1.4.
  '''

  def parse_buffer(self, bfr, **kw):
    ''' Gather the compositionToDTSShift`, `leastDecodeToDisplayDelta`,
        `greatestDecodeToDisplayDelta`, `compositionStartTime` and
        `compositionEndTime` fields.
    '''
    super().parse_buffer(bfr, **kw)
    if self.version == 0:
      struct_format = '>lllll'
    elif self.version == 1:
      struct_format = '>qqqqq'
    else:
      warning("unsupported version %d, treating like version 1")
      struct_format = '>qqqqq'
    self.add_field(
        'fields',
        multi_struct_field(
            struct_format,
            (   'compositionToDTSShift',
                'leastDecodeToDisplayDelta',
                'greatestDecodeToDisplayDelta',
                'compositionStartTime',
                'compositionEndTime'
            )))

  @property
  def compositionToDTSShift(self):
    ''' Obtain the composition to DTSS shift.
    '''
    return self.fields.compositionToDTSShift

  @property
  def leastDecodeToDisplayDelta(self):
    ''' Obtain the least decode to display delta.
    '''
    return self.fields.leastDecodeToDisplayDelta

  @property
  def greatestDecodeToDisplayDelta(self):
    ''' Obtain the greatest decode to display delta.
    '''
    return self.fields.greatestDecodeToDisplayDelta

  @property
  def compositionStartTime(self):
    ''' Obtain the composition start time.
    '''
    return self.fields.compositionStartTime

  @property
  def compositionEndTime(self):
    ''' Obtain the composition end time.
    '''
    return self.fields.compositionEndTime

add_body_class(CSLGBoxBody)

add_generic_sample_boxbody(
    b'stss', '8.6.2', 'Sync Sample',
    '>L', 'number')

add_generic_sample_boxbody(
    b'stsh', '8.6.3', 'Shadow Sync Table',
    '>LL', 'shadowed_sample_number sync_sample_number')

add_generic_sample_boxbody(
    b'sdtp', '8.6.4', 'Independent and Disposable Samples',
    '>HHHH',
    'is_leading sample_depends_on sample_is_depended_on sample_has_redundancy',
    has_inferred_entry_count=True)

add_body_subclass(BoxBody, b'edts', '8.6.5.1', 'Edit')

add_generic_sample_boxbody(
    b'elst', '8.6.6', 'Edit List',
    '>Ll', 'segment_duration media_time', '>Qq')

class DINFBoxBody(BoxBody):
  ''' A 'dinf' Data Information BoxBody - section 8.7.1.
  '''

  PACKET_FIELDS = dict(
      BoxBody.PACKET_FIELDS,
      ##boxes=SubBoxesField,
  )

  def parse_buffer(self, bfr, **kw):
    ''' A DINF BoxBody may contain further Boxes.
    '''
    super().parse_buffer(bfr, **kw)
    ##self.add_from_buffer('boxes', bfr, SubBoxesField, **kw)

add_body_class(DINFBoxBody)

class URL_BoxBody(FullBoxBody):
  ''' An 'url ' Data Entry URL BoxBody - section 8.7.2.1.
  '''

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `location` field.
    '''
    super().parse_buffer(bfr, **kw)
    self.add_from_buffer('location', bfr, UTF8NULField)

add_body_class(URL_BoxBody)

class URN_BoxBody(FullBoxBody):
  ''' An 'urn ' Data Entry URL BoxBody - section 8.7.2.1.
  '''

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `name` and `location` fields.
    '''
    super().parse_buffer(bfr, **kw)
    self.add_from_buffer('name', bfr, UTF8NULField)
    self.add_from_buffer('location', bfr, UTF8NULField)

add_body_class(URN_BoxBody)

class STSZBoxBody(FullBoxBody):
  ''' A 'stsz' Sample Size box - section 8.7.3.2.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      sample_size=UInt32BE,
      sample_count=UInt32BE,
      entry_sizes=(False, ListField),
  )

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `sample_size`, `sample_count`, and `entry_sizes` fields.
    '''
    super().parse_buffer(bfr, **kw)
    sample_size = self.add_from_buffer('sample_size', bfr, UInt32BE)
    sample_count = self.add_from_buffer('sample_count', bfr, UInt32BE)
    if sample_size == 0:
      entry_sizes = []
      for _ in range(sample_count):
        entry_sizes.append(UInt32BE.from_buffer(bfr))
      self.add_field('entry_sizes', ListField(entry_sizes))

add_body_class(STSZBoxBody)

class STZ2BoxBody(FullBoxBody):
  ''' A 'stz2' Compact Sample Size box - section 8.7.3.3.
  '''

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `field_size`, `sample_count` and `entry_sizes` fields.
    '''
    super().parse_buffer(bfr, **kw)
    self.add_from_buffer('reserved', bfr, BytesField, length=3)
    field_size = self.add_from_buffer('field_size', bfr, UInt8)
    sample_count = self.add_from_buffer('sample_count', bfr, UInt32BE)
    entry_sizes = []
    if field_size == 4:
      # nybbles packed into bytes
      for i in range(sample_count):
        if i % 2 == 0:
          bs = bfr.take(1)
          entry_sizes.append(bs[0] >> 4)
        else:
          entry_sizes.append(bs[0] & 0x0f)
      self.add_field('entry_sizes', ListField(entry_sizes))
    elif field_size == 8:
      for _ in range(sample_count):
        entry_sizes.append(UInt8.from_buffer(bfr))
      self.add_field('entry_sizes', ListField(entry_sizes))
    elif field_size == 16:
      for _ in range(sample_count):
        entry_sizes.append(UInt16BE.from_buffer(bfr))
      self.add_field('entry_sizes', ListField(entry_sizes))
    else:
      warning("unhandled field_size=%s, not parsing entry_sizes", field_size)

class STSCBoxBody(FullBoxBody):
  ''' 'stsc' (Sample Table box - section 8.7.4.1.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      entry_count=UInt32BE,
      entries=ListField,
  )

  STSCEntry = structtuple(
      'STSCEntry', '>LLL',
      'first_chunk samples_per_chunk sample_description_index')

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `entry_count` and `entries` fields.
    '''
    super().parse_buffer(bfr, **kw)
    entry_count = self.add_from_buffer('entry_count', bfr, UInt32BE)
    entries = []
    for _ in range(entry_count):
      entries.append(STSCBoxBody.STSCEntry.from_buffer(bfr))
    self.add_field('entries', ListField(entries))

add_body_class(STSCBoxBody)

class STCOBoxBody(FullBoxBody):
  ''' A 'stco' Chunk Offset box - section 8.7.5.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      entry_count=UInt32BE,
      chunk_offsets=ListField,
  )

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `entry_count` and `chunk_offsets` fields.
    '''
    super().parse_buffer(bfr, **kw)
    entry_count = self.add_from_buffer('entry_count', bfr, UInt32BE)
    chunk_offsets = []
    for _ in range(entry_count):
      chunk_offsets.append(UInt32BE.from_buffer(bfr))
    self.add_field('chunk_offsets', ListField(chunk_offsets))

add_body_class(STCOBoxBody)

class CO64BoxBody(FullBoxBody):
  ''' A 'c064' Chunk Offset box - section 8.7.5.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      entry_count=UInt32BE,
      chunk_offsets=ListField,
  )

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `entry_count` and `chunk_offsets` fields.
    '''
    super().parse_buffer(bfr, **kw)
    entry_count = self.add_from_buffer('entry_count', bfr, UInt32BE)
    chunk_offsets = []
    for _ in range(entry_count):
      chunk_offsets.append(UInt64BE.from_buffer(bfr))
    self.add_field('chunk_offsets', ListField(chunk_offsets))

add_body_class(CO64BoxBody)

class DREFBoxBody(FullBoxBody):
  ''' A 'dref' Data Reference box containing Data Entry boxes - section 8.7.2.1.
  '''

  def parse_buffer(self, bfr, copy_boxes=None, **kw):
    ''' Gather the `entry_count` and `boxes` fields.
    '''
    super().parse_buffer(bfr, copy_boxes=copy_boxes, **kw)
    entry_count = self.add_from_buffer('entry_count', bfr, UInt32BE)
    self.add_from_buffer(
        'boxes', bfr, SubBoxesField,
        end_offset=Ellipsis, max_boxes=entry_count, parent=self.box,
        copy_boxes=copy_boxes)

add_body_class(DREFBoxBody)

add_body_subclass(ContainerBoxBody, b'udta', '8.10.1', 'User Data')

class METABoxBody(FullBoxBody):
  ''' A 'meta' Meta BoxBody - section 8.11.1.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      theHandler=Box,
      boxes=SubBoxesField,
  )

  def parse_buffer(self, bfr, copy_boxes=None, **kw):
    ''' Gather the `theHandler` Box and gather the following Boxes as `boxes`.
    '''
    super().parse_buffer(bfr, copy_boxes=copy_boxes, **kw)
    theHandler = self.add_field('theHandler', Box.from_buffer(bfr))
    theHandler.parent = self.box
    self.add_from_buffer(
        'boxes', bfr, SubBoxesField,
        end_offset=Ellipsis, parent=self.box,
        copy_boxes=copy_boxes)

add_body_class(METABoxBody)

class VMHDBoxBody(FullBoxBody):
  ''' A 'vmhd' Video Media Headerbox - section 12.1.2.
  '''

  OpColor = multi_struct_field('>HHH', class_name='OpColor')

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      graphicsmode=UInt16BE,
      opcolor=OpColor,
  )

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `graphicsmode` and `opcolor` fields.
    '''
    super().parse_buffer(bfr, **kw)
    self.add_from_buffer('graphicsmode', bfr, UInt16BE)
    self.add_from_buffer('opcolor', bfr, VMHDBoxBody.OpColor)

add_body_class(VMHDBoxBody)

class SMHDBoxBody(FullBoxBody):
  ''' A 'smhd' Sound Media Headerbox - section 12.2.2.
  '''

  PACKET_FIELDS = dict(
      FullBoxBody.PACKET_FIELDS,
      balance=Int16BE,
      reserved=UInt16BE,
  )

  def parse_buffer(self, bfr, **kw):
    ''' Gather the `balance` field.
    '''
    super().parse_buffer(bfr, **kw)
    self.add_from_buffer('balance', bfr, Int16BE)
    self.add_from_buffer('reserved', bfr, UInt16BE)

add_body_class(SMHDBoxBody)

def parse(o, **kw):
  ''' Return an OverBox source (str, int, file).
  '''
  close = None
  if isinstance(o, str):
    fd = os.open(o, os.O_RDONLY)
    over_box = parse_fd(fd, **kw)
    close = partial(os.close, fd)
  elif isinstance(o, int):
    over_box = parse_fd(o, **kw)
  else:
    over_box = parse_file(o, **kw)
  if close:
    close()
  return over_box

def parse_fd(fd, discard_data=False, **kw):
  ''' Parse an ISO14496 stream from the file descriptor `fd`, yield top level Boxes.
      `fd`: a file descriptor open for read
      `discard_data`: whether to discard unparsed data, default False
      `copy_offsets`: callable to receive BoxBody offsets
  '''
  if not discard_data and stat.S_ISREG(os.fstat(fd).st_mode):
    return parse_buffer(
        CornuCopyBuffer.from_mmap(fd),
        discard_data=False, **kw)
  return parse_buffer(
      CornuCopyBuffer.from_fd(fd),
      discard_data=discard_data, **kw)

def parse_file(fp, **kw):
  ''' Parse an ISO14496 stream from the file `fp`, yield top level Boxes.
      `fp`: a file open for read
      `discard_data`: whether to discard unparsed data, default False
      `copy_offsets`: callable to receive BoxBody offsets
  '''
  return parse_buffer(CornuCopyBuffer.from_file(fp), **kw)

def parse_chunks(chunks, **kw):
  ''' Parse an ISO14496 stream from the iterator of data `chunks`,
      yield top level Boxes.

      Parameters:
      * `chunks`: an iterator yielding bytes objects
      * `discard_data`: whether to discard unparsed data, default False
      * `copy_offsets`: callable to receive BoxBody offsets
  '''
  return parse_buffer(CornuCopyBuffer(chunks), **kw)

def parse_buffer(bfr, copy_offsets=None, **kw):
  ''' Parse an ISO14496 stream from the CornuCopyBuffer `bfr`,
      yield top level Boxes.

      Parameters:
      * `bfr`: a CornuCopyBuffer provided the stream data, preferably seekable
      * `discard_data`: whether to discard unparsed data, default False
      * `copy_offsets`: callable to receive Box offsets
  '''
  if copy_offsets is not None:
    bfr.copy_offsets = copy_offsets
  yield OverBox.from_buffer(bfr, **kw)

def dump_box(B, indent='', fp=None, crop_length=170):
  ''' Recursively dump a Box.
  '''
  if fp is None:
    fp = sys.stdout
  fp.write(indent)
  summary = str(B)
  if len(summary) > crop_length - len(indent):
    summary = summary[:crop_length - len(indent) - 4] + '...)'
  fp.write(summary)
  fp.write('\n')
  try:
    body = B.body
  except AttributeError:
    fp.write(indent)
    fp.write("NO BODY?")
    fp.write('\n')
  else:
    for field_name in body.field_names:
      field = body[field_name]
      if isinstance(field, SubBoxesField):
        fp.write(indent)
        fp.write('  ')
        fp.write(field_name)
        fp.write(':\n')
        for subbox in field.value:
          subbox.dump(indent=indent + '    ', fp=fp, crop_length=crop_length)
  try:
    boxes = B.boxes
  except AttributeError:
    pass
  else:
    fp.write(indent)
    fp.write('  boxes\n')
    for subbox in boxes:
      subbox.dump(indent=indent + '    ', fp=fp, crop_length=crop_length)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
