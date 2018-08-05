#!/usr/bin/env python3
#

''' Facilities associated with binary data.

    This module requires Python 3 and recommends Python 3.6+ because
    it uses abc.ABC, because a Python 2 bytes object is too weak
    (just a str) as also is my cs.py.bytes hack class and because
    the keyword based Packet initiialisation benefits from keyword
    argument ordering.

    * Cameron Simpson <cs@cskk.id.au> 22jul2018
'''

from __future__ import print_function
from abc import ABC, abstractmethod
from collections import namedtuple
from struct import Struct
import sys
from cs.buffer import CornuCopyBuffer

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.buffer'],
}

if sys.version_info[0] < 3:
  print(
      "WARNING: module %r requires Python 3 and recommends 3.6, but version_info=%r"
      % (__name__, sys.version_info),
      file=sys.stderr)
elif sys.version_info[1] < 6:
  print(
      "WARNING: module %r recommends Python 3.6, but version_info=%r"
      % (__name__, sys.version_info),
      file=sys.stderr)

def flatten(chunks):
  ''' Flatten `chunks` into an iterable of bytes instances.

      This exists to allow subclass methods to easily return ASCII
      strings or bytes or iterables or even None, in turn allowing
      them to simply return their superclass' chunks iterators
      directly instead of having to unpack them.
  '''
  if chunks is None:
    pass
  elif isinstance(chunks, (bytes, memoryview)):
    yield chunks
  elif isinstance(chunks, str):
    yield chunks.encode('ascii')
  else:
    for subchunk in chunks:
      for chunk in flatten(subchunk):
        yield chunk

class PacketField(ABC):
  ''' A record for an individual packet field.
  '''

  def __init__(self, value):
    self.value = value

  @property
  def value_s(self):
    ''' The preferred string representation of the value.
    '''
    return str(self.value)

  def __str__(self):
    return "%s(%s)" % (type(self).__name__, self.value_s)

  def __bytes__(self):
    return b''.join(flatten(self.transcribe()))

  def __len__(self):
    ''' Compute the length by running a transcription and measuring it.
    '''
    return sum(len(bs) for bs in flatten(self.transcribe()))

  @classmethod
  def from_bytes(cls, bs, offset=0, length=None):
    ''' Factory to return a PacketField instance from bytes.

        This relies on the class' from_buffer(CornuCopyBuffer) method.
    '''
    bfr = CornuCopyBuffer.from_bytes(bs, offset=offset, length=length)
    field = cls.from_buffer(bfr)
    post_offset = offset + bfr.offset
    return field, post_offset

  @classmethod
  @abstractmethod
  def from_buffer(cls, bfr, **kw):
    ''' Factory to return a PacketField instance from a CornuCopyBuffer.
    '''
    raise NotImplementedError("no from_buffer method")

  @abstractmethod
  def transcribe(self):
    ''' Return or yield the bytes transcription of this field.
    '''
    raise NotImplementedError("no transcribe method")

class EmptyPacketField(PacketField):
  ''' An empty data field, used as a placeholder for optional
      fields when they are not present.

      The singleton EmptyField is a predefined instance.
  '''

  def __init__(self):
    super().__init__(None)

  @classmethod
  def from_buffer(cls, bfr):
    return cls()

  def transcribe(self):
    pass

# singleton empty field
EmptyField = EmptyPacketField()

class UTF8NULField(PacketField):
  ''' A NUL terminated UTF-8 string.
  '''

  @classmethod
  def from_buffer(cls, bfr):
    ''' Read a NUL terminated UTF-8 string from `bfr`, return field.
    '''
    # probe for the terminating NUL
    bs_length = 1
    while True:
      bfr.extend(bs_length)
      nul_pos = bs_length - 1
      if bfr[nul_pos] == 0:
        break
      bs_length += 1
    if nul_pos == 0:
      utf8 = ''
    else:
      utf8_bs = bfr.take(nul_pos)
      if not isinstance(utf8_bs, bytes):
        # transmute memoryview to real bytes object
        utf8_bs = utf8_bs.tobytes()
      utf8 = utf8_bs.decode('utf-8')
    bfr.take(1)
    return cls(utf8)

  def transcribe(self):
    ''' Transcribe the `value` in UTF-8 with a terminating NUL.
    '''
    yield self.value.encode('utf-8')
    yield b'\0'

class BytesField(PacketField):
  ''' A field of bytes.
  '''

  @property
  def value_s(self):
    ''' The repr() of the bytes.
    '''
    bs = self.value
    if not isinstance(bs, bytes):
      bs = bytes(bs)
    return repr(bs)

  @classmethod
  def from_buffer(cls, bfr, length):
    ''' Parse a BytesField of length `length` from `bfr`.
    '''
    if length < 0:
      raise ValueError("length(%d) < 0" % (length,))
    return cls(bfr.take(length))

  def transcribe(self):
    ''' A BytesField is its own transcription.
    '''
    assert isinstance(self.value, (bytes, memoryview))
    return self.value

def fixed_bytes_field(length, class_name=None):
  ''' Factory for BytesField subclasses built from fixed length byte strings.
  '''
  if length < 1:
    raise ValueError("length(%d) < 1" % (length,))
  class FixedBytesField(BytesField):
    ''' A field whose value is simply a fixed length bytes chunk.
    '''
    @classmethod
    def from_buffer(cls, bfr):
      ''' Obtain fixed bytes from the buffer.
      '''
      return BytesField.from_buffer(bfr, length)
  if class_name is None:
    class_name = FixedBytesField.__name__ + '_' + str(length)
  FixedBytesField.__name__ = class_name
  FixedBytesField.__doc__ = (
      'A PacketField which fetches and transcribes a fixed with bytes chunk of length %d.'
      % (length,)
  )
  return FixedBytesField

class BytesesField(PacketField):
  ''' A field containing a list of bytes chunks.

      The following attributes are defined:
      * `value`: the gathered data as a list of bytes instances,
        or None if the field was gathered with `discard_data` true.
      * `offset`: the starting offset of the data.
      * `end_offset`: the ending offset of the data.

      The `offset` and `end_offset` values are recorded during the
      parse, and may become irrelevant if the field's contents are
      changed.
  '''

  def __str__(self):
    return "%s(%d:%d:%s)" % (
        type(self).__name__,
        self.offset,
        self.end_offset,
        "NO_DATA" if self.value is None else "bytes[%d]" % len(self.value))

  def __len__(self):
    return self.length

  @classmethod
  def from_buffer(cls, bfr, end_offset=None, discard_data=False, short_ok=False):
    ''' Gather from `bfr` until `end_offset`.

        Parameters:
        * `bfr`: the input buffer
        * `end_offset`: the ending buffer offset; if this is Ellipsis
          then all the remaining data in `bfr` will be collection
        * `discard_data`: discard the data, keeping only the offset information
        * `short_ok`: if true, do not raise EOFError if there are
          insufficient data; the field's .end_offset value will be
          less than `end_offset`; the default is False
    '''
    if end_offset is None:
      raise ValueError("missing end_offset")
    offset0 = bfr.offset
    if end_offset is Ellipsis:
      # special case: gather up all the remaining data
      if discard_data:
        byteses = None
        length = 0
        for bs in bfr:
          length += len(bs)
      else:
        byteses = list(bfr)
        length = sum( len(bs) for bs in byteses )
    else:
      # otherwise gather up a bounded range of bytes
      if end_offset < offset0:
        raise ValueError("end_offset(%d) < bfr.offset(%d)" % (end_offset, bfr.offset))
      byteses = None if discard_data else []
      bfr.skipto(
          end_offset,
          copy_skip=( None if discard_data else byteses.append ),
          short_ok=short_ok)
      length = end_offset - offset0
    field = cls(byteses)
    field.length = length
    field.offset = offset0
    field.end_offset = bfr.offset
    return field

  def transcribe(self):
    ''' Transcribe the bytes instances.

        *Warning*: this will raise an exception if the data have been discarded.
    '''
    for bs in self.value:
      yield bs

class BytesRunField(PacketField):
  ''' A field containing a continuous run of a single bytes value.

      The following attributes are defined:
      * `length`: the length of the run
      * `bytes_value`: the repeated bytes value

      The property `value` is computed on the fly on every reference
      and returns a value obeying the buffer protocol: a bytes or
      memoryview object.
  '''

  def __init__(self, length, bytes_value):
    if length < 0:
      raise ValueError("invalid length(%r), should be >= 0" % (length,))
    if len(bytes_value) != 1:
      raise ValueError(
          "only single byte bytes_value is supported, received: %r"
          % (bytes_value,))
    self.length = length
    self.bytes_value = bytes_value

  def __str__(self):
    return "%s(%d*%r)" % (
        type(self).__name__,
        self.length,
        self.bytes_value
    )

  # A cache of 256 length runs of assorted bytes values as memoryviews
  # as a mapping of bytes=>memoryview.
  # In normal use these will be based on single byte bytes values.
  _bytes_256s = {}

  @staticmethod
  def _bytes_256(bytes_value):
    bs = BytesRunField._bytes_256s.get(bytes_value)
    if bs is None:
      bs = BytesRunField._bytes_256s[bytes_value] = bytes_value * 256
    return bs

  @property
  def value(self):
    ''' The run of bytes, computed on the fly.

        Values where length <= 256 are cached.
    '''
    length = self.length
    if length == 0:
      return b''
    bytes_value = self.bytes_value
    if length == 1:
      return bytes_value
    if length <= 256:
      bs = self._bytes_256(bytes_value)
      if length < 256:
        bs = bs[:length]
      return bs
    return bytes_value * length

  @classmethod
  def from_buffer(cls, bfr, end_offset=None, bytes_value=b'\0'):
    ''' Parse a BytesRunField by just skipping the specified number of bytes.

        Note: this *does not* check that the skipped bytes contain `bytes_value`.

        Parameters:
        * `bfr`: the buffer to scan
        * `end_offset`: the end offset of the scan, which may be
          an int or Ellipsis to indicate a scan to the end of the
          buffer
        * `bytes_value`: the bytes value to replicate, default
          `b'\0'`; if this is an int then a single byte of that value
          is used
    '''
    if end_offset is None:
      raise ValueError("missing end_offset")
    if isinstance(bytes_value, int):
      bytes_value = bytes((bytes_value,))
    offset0 = bfr.offset
    if end_offset is Ellipsis:
      for _ in bfr:
        pass
    else:
      bfr.skipto(end_offset, discard_data=True)
    field = cls(bfr.offset - offset0, bytes_value)
    return field

  def transcribe(self):
    ''' Transcribe the BytesRunField in 256 byte chunks.
    '''
    length = self.length
    bytes_value = self.bytes_value
    bs256 = self._bytes_256(bytes_value)
    with length >= 256:
      yield bs256
      length -= 256
    if length > 0:
      yield bs256[:length]

_struct_fields = {}

def struct_field(struct_format, class_name):
  ''' Factory for PacketField subclasses built around a single struct format.

      Parameters:
      * `struct_format`: the struct format string, specifying a
        single struct field
      * `class_name`: the class name for the generated class

      Example:

        >>> UInt16BE = struct_field('>H', class_name='UInt16BE')
        >>> UInt16BE.__name__
        'UInt16BE'
        >>> UInt16BE.format
        '>H'
        >>> UInt16BE.struct   #doctest: +ELLIPSIS
        <Struct object at ...>
        >>> field, offset = UInt16BE.from_bytes(bytes((2,3,4)))
        >>> field
        UInt16BE(515)
        >>> offset
        2
        >>> field.value
        515
  '''
  key = (struct_format, class_name)
  StructField = _struct_fields.get(key)
  if not StructField:
    struct = Struct(struct_format)
    class StructField(PacketField):
      ''' A PacketField subclass using a struct.Struct for parse and transcribe.
      '''
      def __str__(self):
        return str(self.value)
      def __repr__(self):
        return "%s(%r)" % (type(self).__name__, self.value)
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
    StructField.__name__ = class_name
    StructField.__doc__ = (
        'A PacketField which parses and transcribes the struct format `%r`.'
        % (struct_format,)
    )
    StructField.struct = struct
    StructField.format = struct_format
    _struct_fields[key] = StructField
  return StructField

# various common values
UInt8 = struct_field('B', 'UInt8')
Int16BE = struct_field('>h', 'Int16BE')
Int16LE = struct_field('<h', 'Int16LE')
Int32BE = struct_field('>l', 'Int32BE')
Int32LE = struct_field('<l', 'Int32LE')
UInt16BE = struct_field('>H', 'UInt16BE')
UInt16LE = struct_field('<H', 'UInt16LE')
UInt32BE = struct_field('>L', 'UInt32BE')
UInt32LE = struct_field('<L', 'UInt32LE')
UInt64BE = struct_field('>Q', 'UInt64BE')
UInt64LE = struct_field('<Q', 'UInt64LE')

class ListField(PacketField):
  ''' A field which is a list of other fields.
  '''

  def __str__(self):
    value = self.value
    length = len(value)
    if length > 16:
      suffix = ',...'
      value = value[:16]
    else:
      suffix = ''
    return '[' + str(length) + ':' + ','.join(str(o) for o in value) + suffix + ']'

  @classmethod
  def from_buffer(cls, bfr):
    ''' ListFields do not know enough to parse a buffer.
    '''
    raise NotImplementedError(
        "%s cannot be parsd directly from a buffer"
        % (cls,))

  def transcribe(self):
    ''' Transcribe each item in the list.
    '''
    for item in self.value:
      yield item.transcribe()

_multi_struct_fields = {}

def multi_struct_field(struct_format, subvalue_names=None, class_name=None):
  ''' Factory for PacketField subclasses build around complex struct formats.

      Parameters:
      * `struct_format`: the struct format string
      * `subvalue_names`: an optional namedtuple field name list;
        if supplied then the field value will be a namedtuple with
        these names
      * `class_name`: option name for the generated class
  '''
  key = (struct_format, subvalue_names, class_name)
  MultiStructField = _struct_fields.get(key)
  if not MultiStructField:
    struct = Struct(struct_format)
    if subvalue_names:
      subvalues_type = namedtuple(
          class_name or "StructSubValues",
          subvalue_names)
    class MultiStructField(PacketField):
      ''' A struct field for a complex struct format.
      '''
      if subvalue_names:
        def __str__(self):
          return str(self.value)
      @classmethod
      def from_buffer(cls, bfr):
        ''' Parse via struct.unpack.
        '''
        bs = bfr.take(struct.size)
        values = struct.unpack(bs)
        if subvalue_names:
          # promote into a namedtuple
          values = subvalues_type(*values)
        return cls(values)
      def transcribe(self):
        ''' Transcribe via struct.pack.
        '''
        return struct.pack(*self.value)
    if class_name is not None:
      MultiStructField.__name__ = class_name
    if subvalue_names:
      MultiStructField.__doc__ = (
          ''' A PacketField which parses and transcribes the struct
              format `%r`, whose `.value` is a namedtuple with
              attributes %r.
          '''
          % (struct_format, subvalue_names)
      )
    else:
      MultiStructField.__doc__ = (
          ''' A PacketField which parses and transcribes the struct
              format `%r`, whose `.value` is a tuple of the struct values.
          '''
          % (struct_format,)
      )
    MultiStructField.struct = struct
    MultiStructField.format = struct_format
    if subvalue_names:
      MultiStructField.subvalue_names = subvalue_names
    _multi_struct_fields[key] = MultiStructField
  return MultiStructField

def structtuple(class_name, struct_format, subvalue_names):
  ''' Convenience wrapper for multi_struct_field.
  '''
  return multi_struct_field(
      struct_format,
      subvalue_names=subvalue_names,
      class_name=class_name)

class Packet(PacketField):
  ''' Base class for compound objects derived from binary data.
  '''

  def __init__(self, **fields):
    ''' Initialise the Packet.

        A Packet is its own `.value`.

        If any keyword arguments are provided, they are used as a
        mapping of `field_name` to `Field` instance, supporting
        direct construction of simple Packets. From Python 3.6
        onwards keyword arguments preserve the calling order; in
        Python versions earlier than this the caller should adjust
        the `Packet.field_names` list to the correct order after
        initialisation.
    '''
    # Packets are their own value
    PacketField.__init__(self, self)
    # start with no fields
    self.field_names = []
    self.fields = []
    self.field_map = {}
    for field_name, field in fields.items():
      self.add_field(field_name, field)

  def __str__(self):
    return "%s(%s)" % (
        type(self).__name__,
        ','.join(
            "%s=%s" % (field_name, self.field_map[field_name])
            for field_name in self.field_names
        )
    )

  def get_field(self, field_name):
    ''' Return the field named `field_name`.
    '''
    try:
      return self.field_map[field_name]
    except KeyError:
      raise ValueError("unknown field %r" % (field_name,))

  def set_field(self, field_name, new_field):
    ''' Replace the field named `field_name`.

        Note that this replaces the field, not its value.
    '''
    if field_name in self.field_map:
      self.field_name[new_field] = new_field
    else:
      raise ValueError("unknown field %r" % (field_name,))

  def self_check(self):
    ''' Internal self check.

        If the Packet has a `PACKET_FIELDS` attribute, normally a
        class attribute, then check the fields against it. The
        `PACKET_FIELDS` attribute is a mapping of `field_name` to
        a specification of `required` and `types`. The specification
        may take one of 2 forms:
        * a tuple of `(required, types)`
        * a single `type`; this is equivalent to `(True, (type,))`
        Their meanings are as follows:
        * `required`: a Boolean. If true, the field must be present
          in the packet `field_map`, otherwise it need not be present.
        * `types`: a tuple of acceptable field types

        There are some special semantics involved here.

        An implementation of a `Packet` may choose to make some
        fields plain instance attributes instead of `Field`s in the
        `field_map` mapping, particularly variable packets such as
        a `cs.iso14496.BoxHeader`, whose `.length` may be parsed
        directly from its binary form or computed from other fields
        depending on the `box_size` value. Therefore, checking for
        a field is first done via the `field_map` mapping, then by
        `getattr`, and as such the acceptable `types` may include
        non-`PacketField` types such as `int`.

        Here is the `BoxHeader.PACKET_FIELDS` definition as an example:

          PACKET_FIELDS = {
            'box_size': UInt32BE,
            'box_type': BytesField,
            'length': (
                True,
                (
                    type(Ellipsis),
                    UInt64BE,
                    UInt32BE,
                    int
                ),
            ),
          }

        Note that `length` includes some non-`PacketField` types,
        and that it is written as a tuple of `(True, types)` because
        it has more than one acceptable type.
    '''
    try:
      fields_spec = self.PACKET_FIELDS
    except AttributeError:
      print("self_check: warning: no PACKET_FIELDS for %s" % (self,), file=sys.stderr)
    else:
      for field_name, field_spec in fields_spec.items():
        if isinstance(field_spec, tuple):
          required, basetype = field_spec
        else:
          required, basetype = True, field_spec
        try:
          field = self.field_map[field_name]
        except KeyError:
          # Note: we fall back on getattr here instead of
          # self.fields[field_name] because sometimes an attribute might not
          # always be a field.
          # For an example see the length in a cs.iso14496.BoxHeader.
          field = getattr(self, field_name, None)
        if field is None:
          if required:
            raise ValueError("field %r missing" % (field_name,))
        else:
          if not isinstance(field, basetype):
            raise ValueError(
                "field %r should be an instance of %s:%s but is %s:%s: %s"
                % (
                    field_name,
                    'tuple' if isinstance(basetype, tuple) else basetype.__name__,
                    basetype,
                    type(field).__name__,
                    type(field),
                    field))
      for field_name in self.field_names:
        if field_name not in fields_spec:
          raise ValueError(
              "field %r is present but is not defined in self.PACKET_FIELDS: %r"
              % (field_name, sorted(fields_spec.keys())))

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

  def __getitem__(self, field_name):
    return self.field_map[field_name]

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

        Parameters:
        * `field_name`: the name for the new field; it must be new.
        * `bs`: the bytes containing the field data; a CornuCopyBuffer
          is made from this for the parse.
        * `factory`: a factory for parsing the field data, returning
          a PacketField. If `factory` is a class then its .from_buffer
          method is called, otherwise the factory is called directly.
        * `offset`: optional start offset of the field data within
          `bs`, default 0.
        * `length`: optional maximum number of bytes from `bs` to
          make available for the parse, default None meaning that
          everything from `offset` onwards is available.

        Additional keyword arguments are passed to the internal
        `.add_from_buffer` call.
    '''
    bfr = CornuCopyBuffer.from_bytes(bs, offset=offset, length=length)
    value = self.add_from_buffer(field_name, bfr, factory, **kw)
    return value, offset + bfr.offset

  def add_from_buffer(self, field_name, bfr, factory, **kw):
    ''' Add a new PacketField named `field_name` parsed from `bfr` using `factory`.
        Updates the internal field records.
        Returns the new PacketField's .value.

        Paramaters:
        * `field_name`: the name for the new field; it must be new.
        * `bfr`: a CornuCopyBuffer from which to parse the field data.
        * `factory`: a factory for parsing the field data, returning
          a PacketField. If `factory` is a class then its .from_buffer
          method is called, otherwise the factory is called directly.

        Additional keyword arguments are passed to the internal
        factory call.

        For convenience, `factory` may also be a str in which case
        it is taken to be a single struct format specifier.
        Alternatively, `factory` may be an integer in which case
        it is taken to be a fixed length bytes field.
    '''
    assert isinstance(field_name, str), "field_name not a str: %r" % (field_name,)
    assert isinstance(bfr, CornuCopyBuffer), "bfr not a CornuCopyBuffer: %r" % (bfr,)
    if isinstance(factory, str):
      from_buffer = struct_field(factory, 'struct_field').from_buffer
    elif isinstance(factory, int):
      from_buffer = fixed_bytes_field(factory).from_buffer
    elif isinstance(factory, type):
      from_buffer = factory.from_buffer
    else:
      from_buffer = factory
    field = from_buffer(bfr, **kw)
    self.add_field(field_name, field)
    return field.value

  def add_field(self, field_name, field):
    ''' Add a new PacketField `field` named `field_name`.
    '''
    if field_name in self.field_map:
      raise ValueError("field %r already in field_map" % (field_name,))
    self.field_names.append(field_name)
    self.fields.append(field)
    self.field_map[field_name] = field
    return None if field is None else field.value
