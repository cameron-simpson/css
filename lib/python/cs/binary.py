#!/usr/bin/python
#

''' Facilities associated with binary data.
    - Cameron Simpson <cs@cskk.id.au> 22jul2018
'''

from struct import Struct
from cs.buffer import CornuCopyBuffer

def flatten(chunks):
  ''' Flatten `chunks` into an iterable of bytes instances.
      This exists to allow subclass methods to easily return ASCII
      strings or bytes or iterables, in turn allowing them to
      simply return their superclass' chunks iterators directly
      instead of having to unpack them.
  '''
  if isinstance(chunks, bytes):
    yield chunks
  elif isinstance(chunks, str):
    yield chunks.encode('ascii')
  else:
    for subchunk in chunks:
      for chunk in flatten(subchunk):
        yield chunk

class PacketField(object):
  ''' A record for an individual packet field.
  '''

  def __init__(self, value):
    self.value = value
  def __str__(self):
    return "%s(%s)" % (type(self).__name__, self.value)
  @classmethod
  def from_bytes(cls, bs, offset=0, length=None):
    ''' Factory to return an PacketField instance from bytes.
        This relies on the class' from_bfr(CornuCopyBuffer) method.
    '''
    bfr = CornuCopyBuffer.from_bytes(bs, offset=offset, length=length)
    field = cls.from_buffer(bfr)
    post_offset = offset + bfr.offset
    return field, post_offset

def fixed_bytes_field(length, class_name=None):
  ''' Factory for PacketField subclasses built off fixed length byte strings.
  '''
  if length < 1:
    raise ValueError("length(%d) < 1" % (length,))
  class FixedBytesField(PacketField):
    ''' A field whose value is simply a fixed length bytes chunk.
    '''
    @classmethod
    def from_buffer(cls, bfr):
      ''' Obtain fixed bytes from the buffer.
      '''
      return cls(bfr.take(length))
    def transcribe(self):
      ''' Transcribe the fixed bytes.
      '''
      return self.value
  if class_name is None:
    class_name = FixedBytesField.__name__ + '_' + str(length)
  FixedBytesField.__name__ = class_name
  return FixedBytesField

def struct_field(format, class_name=None):
  ''' Factory for PacketField subclasses built around a single struct format.
  '''
  struct = Struct(format)
  class StructField(PacketField):
    ''' A PacketField subclass using a struct.Struct for parse and transcribe.
    '''
    @classmethod
    def from_buffer(cls, bfr):
      ''' Parse a value from the bytes `bs` at `offset`, default 0.
          Return a PacketField instance and the new offset.
      '''
      bs = bfr.take(struct.size)
      value, = struct.unpack(bs)
      return cls(value)
    def transcribe(self):
      ''' Transcribe the value back into bytes.
      '''
      return struct.pack(self.value)
  if class_name is not None:
    StructField.__name__ = class_name
  StructField.struct = struct
  StructField.format = format
  return StructField

class Packet(PacketField):
  ''' Base class for compound objects derived from binary data.
  '''

  def __init__(self, value):
    # Packets are their own value
    PacketField.__init__(value)
    # start with no fields
    self.field_names = []
    self.fields = []
    self.field_map = {}

  def __str__(self):
    return "%s(%s)" % (
        type(self).__name__,
        ','.join(
            "%s=%s" % (field_name, self.field_map[field_name])
            for field_name in self.field_names
        )
    )

  def __getattr__(self, attr):
    ''' Unknown attributes may be field names; return their value.
    '''
    try:
      field = self.field_map[attr]
    except KeyError:
      raise AttributeError(attr)
    if field is None:
      return None
    return field.value

  def transcribe(self):
    ''' Yield a sequence of bytes objects for this instance.
    '''
    for field in self.fields:
      if field is not None:
        for bs in flatten(field.transcribe()):
          yield bs

  def add_from_bytes(self, field_name, bs, factory, offset=0, length=None):
    ''' Add a new PacketField named `field_name` parsed from the
        bytes `bs` using `factory`. Updates the internal field
        records.
        Returns the new PacketField's .value and the new parse
        offset within `bs`.

        `field_name`: the name for the new field; it must be new.
        `bs`: the bytes containing the field data; a CornuCopyBuffer
          is made from this for the parse.
        `factory`: a factory for parsing the field data, returning
          a PacketField. If `factory` is a class then its .from_buffer
          method is called, otherwise the factory is called directly.
        `offset`: optional start offset of the field data within
          `bs`, default 0.
        `length`: optional maximum number of bytes from `bs` to
          make available for the parse, default None meaning that
          everything from `offset` onwards is available.
    '''
    bfr = CornuCopyBuffer.from_bytes(bs, offset=offset, length=length)
    field = self.from_buffer(field_name, bfr, factory)
    return field, offset + bfr.offset

  def add_from_buffer(self, field_name, bfr, factory):
    ''' Add a new PacketField named `field_name` parsed from `bfr` using `factory`.
        Updates the internal field records.
        Returns the new PacketField's .value.

        `field_name`: the name for the new field; it must be new.
        `bfr`: a CornuCopyBuffer from which to parse the field data.
        `factory`: a factory for parsing the field data, returning
          a PacketField. If `factory` is a class then its .from_buffer
          method is called, otherwise the factory is called directly.
    '''
    if field_name in self.field_map:
      raise ValueError("field %r already in field_map" % (field_name,))
    if isinstance(factory, type):
      from_buffer = factory.from_buffer
    else:
      from_buffer = factory
    field = from_buffer(bfr)
    self.add_field(field_name, field)
    return field.value

  def add_field(self, field_name, field):
    ''' Add a new PacketField `field` named `field_name`.
    '''
    self.field_names.append(field_name)
    self.fields.append(field)
    self.field_map[field_name] = field

UInt32 = struct_field('>L')
UInt64 = struct_field('>Q')

class BoxHeader(Packet):
  ''' An ISO14496 Box header packet.
  '''

  @classmethod
  def from_buffer(cls, bfr):
    ''' Decode a box header from the CornuCopyBuffer `bfr`.
        Return (box_header, new_buf, new_offset) or None at end of input.
    '''
    packet = cls()
    # note start point
    offset0 = bfr.offset
    box_size = packet.add_from_buffer('box_size', bfr, UInt32)
    box_type = packet.add_field('box_type', PacketField(bfr.take(4)))
    if box_size == 0:
      # box extends to end of data/file
      length = packet.add_field('length', None)
    elif box_size == 1:
      # 64 bit length
      length = packet.add_from_buffer('length', bfr, UInt64)
    else:
      length = packet.add_field('length', bfr, PacketField(box_size))
    if box_type == b'uuid':
      # user supplied 16 byte type
      packet.add_field('user_type', bfr.take(16))
    else:
      packet.add_field('user_type', None)
    offset = bfr.offset
    if length is not None and offset0 + length < offset:
      raise ValueError(
          "box length:%d is less than the box header size:%d"
          % (length, offset-offset0))
    packet.type = box_type
    packet.header_length = offset - offset0
    return packet
