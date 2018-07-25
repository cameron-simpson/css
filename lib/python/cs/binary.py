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

# a big endian unsigned 32 bit integer
UInt32 = struct_field('>L')

# a big endian unsigned 64 bit integer
UInt64 = struct_field('>Q')

class Packet(PacketField):
  ''' Base class for compound objects derived from binary data.
  '''

  def __init__(self):
    # Packets are their own value
    PacketField.__init__(self, self)
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

  def add_from_bytes(self, field_name, bs, factory, offset=0, length=None, **kw):
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
        Additional keyword arguments are passed to the internal
        .add_from_buffer call.
    '''
    bfr = CornuCopyBuffer.from_bytes(bs, offset=offset, length=length)
    field = self.add_from_buffer(field_name, bfr, factory, **kw)
    return field, offset + bfr.offset

  def add_from_buffer(self, field_name, bfr, factory, **kw):
    ''' Add a new PacketField named `field_name` parsed from `bfr` using `factory`.
        Updates the internal field records.
        Returns the new PacketField's .value.

        `field_name`: the name for the new field; it must be new.
        `bfr`: a CornuCopyBuffer from which to parse the field data.
        `factory`: a factory for parsing the field data, returning
          a PacketField. If `factory` is a class then its .from_buffer
          method is called, otherwise the factory is called directly.
        Additional keyword arguments are passed to the internal
        factory call.
    '''
    assert isinstance(field_name, str), "field_name not a str: %r" % (field_name,)
    assert isinstance(bfr, CornuCopyBuffer), "bfr not a CornuCopyBuffer: %r" % (bfr,)
    if field_name in self.field_map:
      raise ValueError("field %r already in field_map" % (field_name,))
    if isinstance(factory, type):
      from_buffer = factory.from_buffer
    else:
      from_buffer = factory
    X("add_from_buffer: from_buffer=%s", from_buffer)
    field = from_buffer(bfr, **kw)
    self.add_field(field_name, field)
    return field.value

  def add_field(self, field_name, field):
    ''' Add a new PacketField `field` named `field_name`.
    '''
    self.field_names.append(field_name)
    self.fields.append(field)
    self.field_map[field_name] = field
    return None if field is None else field.value
