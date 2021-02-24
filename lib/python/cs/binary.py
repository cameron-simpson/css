#!/usr/bin/env python3
#
# pylint: disable=too-many-lines
#

''' Facilities associated with binary data parsing and transcription.
    The classes in this module support easy parsing of binary data
    structures,
    returning instances with the binary data decoded into attributes
    and capable of transcribing themselves in binary form
    (trivially via `bytes(instance)` and also otherwise).

    Note: this module requires Python 3.6+ because various default
    behaviours rely on `dict`s preserving their insert order.

    See `cs.iso14496` for an ISO 14496 (eg MPEG4) parser
    built using this module.

    **Deprecation**: the `Packet` and `PacketField` classes
    were unnecessarily hard to use and are deprecated
    in favour of the `Binary`* suite of classes and factories.
    All the *`Field` classes and other subclasses
    derived from `Packet` and `PacketField` are also deprecated.

    Terminology used below:
    * buffer:
      an instance of `cs.buffer.CornuCopyBuffer`,
      which presents an iterable of bytes-like values
      via various useful methods;
      it also has a few factory methods to make one from a variety of sources
      such as bytes, iterables, binary files, `mmap`ped files,
      TCP data streams, etc.
    * chunk:
      a piece of binary data obeying the buffer protocol,
      almost always a `bytes` instance or a `memoryview`,
      but in principle also things like `bytearray`.

    There are 4 main classes on which an implementor should base their data structures:
    * `BinarySingleStruct`: a factory for classes based
      on a `struct.struct` format string with a single value;
      this builds a `namedtuple` subclass
    * `BinaryMultiStruct`: a factory for classes based
      on a `struct.struct` format string with multiple values;
      this also builds a `namedtuple` subclass
    * `BinarySingleValue`: a base class for subclasses
      parsing and transcribing a single value
    * `BinaryMultiValue`: a base class for subclasses
      parsing and transcribing multiple values

    The `BinaryMultiValue` base class is what should be used
    for complex structures with varying or recursive subfields.

    All the classes derived from the above inherit all the methods
    of `BinaryMixin`.
    Amongst other things, this means that the binary transcription
    can be had simply from `bytes(instance)`,
    although there are more transcription methods provided
    for when greater flexibility is desired.
    It also means that all classes have `parse`* methods
    for parsing binary data streams.

    You can also instantiate objects directly;
    there's no requirement for the source information to be binary.

    There are several presupplied subclasses for common basic types
    such as `UInt32BE` (an unsigned 32 bit big endian integer).
'''

from abc import ABC, abstractmethod, abstractclassmethod
from collections import namedtuple
from struct import Struct
import sys
from types import SimpleNamespace
from cs.buffer import CornuCopyBuffer
from cs.gimmicks import warning
from cs.lex import cropped, cropped_repr, typed_str as s
from cs.pfx import Pfx, pfx_method
from cs.seq import Seq

__version__ = '20200229'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Programming Language :: Python :: 3",
    ],
    'install_requires':
    ['cs.buffer', 'cs.gimmicks', 'cs.lex', 'cs.pfx', 'cs.seq'],
    'python_requires':
    '>=3.6',
}

if (sys.version_info.major < 3
    or (sys.version_info.major == 3 and sys.version_info.minor < 6)):
  warning(
      "module %r requires Python 3.6 for reliable field ordering but version_info=%s",
      __name__, sys.version_info
  )

def flatten(chunks):
  ''' Flatten `chunks` into an iterable of `bytes` instances.

      This exists to allow subclass methods to easily return
      transcribeable things (having a `.transcribe` method), ASCII
      strings or bytes or iterables or even `None`, in turn allowing
      them simply to return their superclass' chunks iterators
      directly instead of having to unpack them.

      An example from the `cs.iso14496.METABoxBody` class:

          def transcribe(self):
              yield super().transcribe()
              yield self.theHandler
              yield self.boxes

      The binary classes `flatten` the result of the `.transcribe`
      method to obtain `bytes` insteances for the object's bnary
      transcription.
  '''
  if chunks is None:
    pass
  elif hasattr(chunks, 'transcribe'):
    yield from flatten(chunks.transcribe())
  elif isinstance(chunks, (bytes, memoryview)):
    if chunks:
      yield chunks
  elif isinstance(chunks, str):
    yield chunks.encode('ascii')
  else:
    for subchunk in chunks:
      yield from flatten(subchunk)

_pt_spec_seq = Seq()

def pt_spec(pt, name=None):
  ''' Convert a parse/transcribe specification `pt`
      into an `AbstractBinary` subclass.

      This is largely used to provide flexibility
      in the specifications for the `BinaryMultiValue` factory
      but can be used as a factory for other simple classes.

      If the specification `pt` is a subclass of `AbstractBinary`
      this is returned directly.

      If `pt` is a 2-tuple of `str`
      the values are presumed to be a format string for `struct.struct`
      and filed names separated by spaces;
      a new `BinaryMultiStruct` class is created from these and returned.

      Otherwise two functions
      `f_parse_value(bfr)` and `f_transcribe_value(value)`
      are obtained and used to construct a new `BinarySingleValue` class
      as follows:

      If `pt` has `.parse_value` and `.transcribe_value` callable attributes,
      use those for `f_parse_value` and `f_transcribe_value` respectively.

      Otherwise, if `pt` is an `int`
      define `f_parse_value` to obtain exactly that many bytes from a buffer
      and `f_transcribe_value` to return those bytes directly.

      Otherwise presume `pt` is a 2-tuple of `(f_parse_value,f_transcribe_value)`.
  '''
  # AbstractBinary subclasses are returned directly
  try:
    if issubclass(pt, AbstractBinary):
      return pt
  except TypeError:
    pass
  # other specifications construct a class
  try:
    f_parse_value = pt.parse_value
    f_transcribe_value = pt.transcribe_value
  except AttributeError:
    if isinstance(pt, int):
      f_parse_value = lambda bfr: bfr.take(pt)
      f_transcribe_value = lambda value: value
    else:
      pt0, pt1 = pt
      if isinstance(pt0, str) and isinstance(pt1, str):
        # struct format and field names
        return BinaryMultiStruct(
            '_'.join(
                (
                    name or "PTStruct", str(next(_pt_spec_seq)),
                    pt1.replace(' ', '__')
                )
            ), pt0, pt1
        )
      f_parse_value = pt0
      f_transcribe_value = pt1

  class PTValue(BinarySingleValue):  # pylint: disable=used-before-assignment
    ''' A `BinarySingleValue` subclass
        made from `f_parse_value` and `f_transcribe_value`.
    '''

    @staticmethod
    def parse_value(bfr):
      ''' Parse value form buffer.
      '''
      return f_parse_value(bfr)

    @staticmethod
    def transcribe_value(value):
      ''' Transcribe the value.
      '''
      return f_transcribe_value(value)

  if name is not None:
    PTValue.__name__ = name

  PTValue.__name__ += '_' + str(next(_pt_spec_seq))
  return PTValue

class BinaryMixin:
  ''' Presupplied helper methods for binary objects.
  '''

  @pfx_method
  def self_check(self):
    ''' Internal self check. Returns `True` if passed.

        If the structure has a `FIELD_TYPES` attribute, normally a
        class attribute, then check the fields against it. The
        `FIELD_TYPES` attribute is a mapping of `field_name` to
        a specification of `required` and `types`. The specification
        may take one of 2 forms:
        * a tuple of `(required,types)`
        * a single `type`; this is equivalent to `(True,(type,))`
        Their meanings are as follows:
        * `required`: a Boolean. If true, the field must be present
          in the packet `field_map`, otherwise it need not be present.
        * `types`: a tuple of acceptable field types

        There are some special semantics involved here.

        An implementation of a structure may choose to make some
        fields plain instance attributes instead of binary objects
        in the `field_map` mapping, particularly variable structures
        such as a `cs.iso14496.BoxHeader`, whose `.length` may be parsed
        directly from its binary form or computed from other fields
        depending on the `box_size` value. Therefore, checking for
        a field is first done via the `field_map` mapping, then by
        `getattr`, and as such the acceptable `types` may include
        nonstructure types such as `int`.

        Here is the `BoxHeader.FIELD_TYPES` definition as an example:

            FIELD_TYPES = {
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

        Note that `length` includes some nonstructure types,
        and that it is written as a tuple of `(True,types)` because
        it has more than one acceptable type.
    '''
    with Pfx(s(self)):
      ok = True
      try:
        fields_spec = self.FIELD_TYPES
      except AttributeError:
        warning("no FIELD_TYPES")
        ##ok = False
      else:
        for field_name, field_spec in fields_spec.items():
          with Pfx(".%s=%s", field_name, field_spec):
            if isinstance(field_spec, tuple):
              required, basetype = field_spec
            else:
              required, basetype = True, field_spec
            try:
              field = getattr(self, field_name)
            except AttributeError:
              if required:
                warning("missing: __dict__=%s", cropped_repr(self.__dict__))
                ok = False
            else:
              if not isinstance(field, basetype):
                warning(
                    "should be an instance of %s:%s but is %s", (
                        'tuple'
                        if isinstance(basetype, tuple) else basetype.__name__
                    ), basetype, s(field)
                )
                ok = False
        for field_name in self.__dict__:
          if field_name not in fields_spec:
            warning(
                "field %s.%s is present but is not defined in self.FIELD_TYPES: %r",
                type(self).__name__, field_name, sorted(fields_spec.keys())
            )
            ok = False
      return ok

  def __bytes__(self):
    ''' The binary transcription as a single `bytes` object.
    '''
    return b''.join(flatten(self.transcribe()))

  def __len__(self):
    ''' Compute the length by running a transcription and measuring it.
    '''
    return sum(map(len, flatten(self.transcribe())))

  @classmethod
  def scan_with_offsets(cls, bfr, count=None):
    ''' Function to scan the buffer `bfr` for repeated instances of `cls`
        until end of input,
        yielding `(offset,instance,post_offset)` tuples
        where `offset` is the buffer offset where the instance commenced
        and `post_offset` is the buffer offset after the instance.
    '''
    scanned = 0
    offset = bfr.offset
    while (count is None or scanned < count) and not bfr.at_eof():
      instance = cls.parse(bfr)
      post_offset = bfr.offset
      yield offset, instance, post_offset
      offset = post_offset
      scanned += 1

  @classmethod
  def scan(cls, bfr, **kw):
    ''' Function to scan the buffer `bfr` for repeated instances of `cls`
        until end of input,
        yielding instances of `cls`.
    '''
    return map(lambda scan3: scan3[1], cls.scan_with_offsets(bfr, **kw))

  @classmethod
  def scan_file(cls, f):
    ''' Function to scan the file `f` for repeated instances of `cls`
        until end of input,
        yields instances of `f`.

        Parameters:
        * `f`: the binary file object to parse;
          if `f` is a string, that pathname is opened for binary read.
    '''
    if isinstance(f, str):
      with open(f, 'rb') as f2:
        yield from cls.scan_file(f2)
    else:
      yield from cls.scan(CornuCopyBuffer.from_file(f))

  def transcribe_flat(self):
    ''' Return a flat iterable of chunks transcribing this field.
    '''
    return flatten(self.transcribe())

  @classmethod
  def parse_bytes(cls, bs, offset=0, length=None, **kw):
    ''' Factory to parse an instance from the
        bytes `bs` starting at `offset`.
        Returns `(instance,offset)` being the new instance and the post offset.

        Raises `EOFError` if `bs` has insufficient data.

        The parameters `offset` and `length` are passed to the
        `CornuCopyBuffer.from_bytes` factory.

        Other keyword parameters are passed to the `.parse` method.

        This relies on the `cls.parse` method for the parse.
    '''
    bfr = CornuCopyBuffer.from_bytes(bs, offset=offset, length=length)
    instance = cls.parse(bfr, **kw)
    return instance, bfr.offset

  @classmethod
  def from_bytes(cls, bs, **kw):
    ''' Factory to parse an instance from the
        bytes `bs` starting at `offset`.
        Returns the new instance.

        Raises `ValueError` if `bs` is not entirely consumed.
        Raises `EOFError` if `bs` has insufficient data.

        Keyword parameters are passed to the `.parse_bytes` method.

        This relies on the `cls.parse` method for the parse.
    '''
    instance, offset = cls.parse_bytes(bs, **kw)
    if offset < len(bs):
      raise ValueError(
          "unparsed data at offset %d: %r" % (offset, bs[offset:])
      )
    return instance

class AbstractBinary(ABC, BinaryMixin):
  ''' Abstract class for all `Binary`* implementations,
      specifying the `parse` and `transcribe` methods
      and providing the methods from `BinaryMixin`.
  '''

  @abstractclassmethod
  def parse(cls, bfr):
    ''' Parse an instance of `cls` from the buffer `bfr`.
    '''
    raise NotImplementedError("parse")

  @abstractmethod
  def transcribe(self):
    ''' Return or yield `bytes`, ASCII string, `None` or iterables
        comprising the binary form of this instance.

        This aims for maximum convenience
        when transcribing a data structure.

        This may be implemented as a generator, yielding parts of the structure.

        This may be implemented as a normal function, returning:
        * `None`: no bytes of data,
          for example for an omitted or empty structure
        * a `bytes`-like object: the full data bytes for the structure
        * an ASCII compatible string:
          this will be encoded with the `'ascii'` encoding to make `bytes`
        * an iterable:
          the components of the structure,
          including substranscriptions which themselves
          adhere to this protocol - they may be `None`, `bytes`-like objects,
          ASCII compatible strings or iterables.
          This supports directly returning or yielding the result of a field's
          `.transcribe` method.
    '''
    raise NotImplementedError("transcribe")

class SimpleBinary(SimpleNamespace, AbstractBinary):
  ''' Abstract binary class based on a `SimpleNamespace`,
      thus providing a nice `__str__` and a keyword based `__init__`.
      Implementors must still define `.parse` and `.transcribe`.

      To constraint the arguments passed to `__init__`,
      define an `__init__` which accepts specific keyword arguments
      and pass through to `super().__init__()`. Example:

          def __init__(self, *, field1=None, field2):
              """ Accept only `field1` (optional)
                  and `field2` (mandatory).
              """
              super().__init__(field1=field1, field2=field2)
  '''

class BinarySingleValue(AbstractBinary):
  ''' A representation of a single value as the attribute `.value`.

      Subclasses must implement:
      * `parse` or `parse_value`
      * `transcribe` or `transcribe_value`
  '''

  def __init__(self, value):
    self.value = value

  def __repr__(self):
    return "%s(%r)" % (type(self).__name__, self.value)

  def __str__(self):
    return str(self.value)

  def __int__(self):
    return int(self.value)

  def __float__(self):
    return float(self.value)

  def __eq__(self, other):
    return self.value == other.value

  @classmethod
  def scan_values(cls, bfr, **kw):
    ''' Scan `bfr`, yield values.
    '''
    return map(lambda instance: instance.value, cls.scan(bfr, **kw))

  @classmethod
  def parse(cls, bfr):
    ''' Parse an instance from `bfr`.

        Subclasses must implement this method or `parse_value`.
    '''
    value = cls.parse_value(bfr)
    return cls(value)

  @classmethod
  def parse_value(cls, bfr):
    ''' Parse a value from `bfr` based on this class.

        Subclasses must implement this method or `parse`.
    '''
    return cls.parse(bfr).value

  @classmethod
  def parse_value_from_bytes(cls, bs, offset=0, length=None, **kw):
    ''' Parse a value from the bytes `bs` based on this class.
        Return `(value,offset)`.
    '''
    instance, offset = cls.parse_bytes(bs, offset=offset, length=length, **kw)
    return instance.value, offset

  def transcribe(self):
    ''' Transcribe this instance as bytes.

        Subclasses must implement this method or `transcribe_value`.
    '''
    return self.transcribe_value(self.value)

  @classmethod
  def transcribe_value(cls, value):
    ''' Transcribe `value` as bytes based on this class.

        Subclasses must implement this method or `transcribe`.
    '''
    return cls(value).transcribe()

class BinaryByteses(AbstractBinary):
  ''' A list of `bytes` parsed directly from the native iteration of the buffer.
  '''

  def __init__(self):
    self.values = []

  def __repr__(self):
    return "%s:%r" % (type(self).__name__, self.values)

  @classmethod
  def parse(cls, bfr):
    self = cls()
    self.values.extend(bfr)
    return self

  def transcribe(self):
    yield from iter(self.values)

class BinaryListValues(AbstractBinary):
  ''' A list of values with a common parse specification,
      such as sample or Boxes in an ISO14496 Box structure.
  '''

  def __init__(self):
    self.values = []

  def __str__(self):
    return "%s%r" % (type(self).__name__, self.values)

  __repr__ = __str__

  def __iter__(self):
    return iter(self.values)

  # pylint: disable=arguments-differ
  @classmethod
  def parse(
      cls,
      bfr,
      count=None,
      *,
      end_offset=None,
      min_count=None,
      max_count=None,
      pt,
  ):
    ''' Read values from `bfr`.
        Return a `BinaryListValue` containing the values.

        Parameters:
        * `count`: optional count of values to read;
          if specified, exactly this many values are expected.
        * `end_offset`: an optional bounding end offset of the buffer.
        * `min_count`: the least acceptable number of values.
        * `max_count`: the most acceptable number of values.
        * `pt`: a parse/transcribe specification
          as accepted by the `pt_spec()` factory.
          The values will be returned by its parse function.
    '''
    if end_offset is not None:
      with bfr.subbuffer(end_offset) as subbfr:
        return cls.parse(
            subbfr,
            count=count,
            min_count=min_count,
            max_count=max_count,
            pt=pt
        )
    if count is not None:
      if min_count is None:
        min_count = count
      elif min_count < count:
        raise ValueError("min_count(%s) < count(%s)" % (min_count, count))
      if max_count is None:
        max_count = count
      elif max_count > count:
        raise ValueError("max_count(%s) > count(%s)" % (max_count, count))
    if (min_count is not None and max_count is not None
        and min_count > max_count):
      raise ValueError(
          "min_count(%s) > max_count(%s)" % (min_count, max_count)
      )
    self = cls()
    values = self.values
    func_parse = pt_spec(pt).parse
    while max_count is None or len(values) < max_count:
      try:
        instance = func_parse(bfr)
      except EOFError:
        break
      values.append(instance)
    if min_count is not None and len(values) < min_count:
      warning(
          "%s.parse: insufficient instances of %r found: required at least %s, found %d",
          type(self).__name__, pt, min_count, len(values)
      )
    return self

  def transcribe(self):
    ''' Transcribe all the values.
    '''
    return map(
        lambda value: value
        if isinstance(value, bytes) else value.transcribe(), self.values
    )

_binary_multi_struct_classes = {}

def BinaryMultiStruct(class_name: str, struct_format: str, field_names: str):
  ''' A class factory for `AbstractBinary` `namedtuple` subclasses
      built around complex `struct` formats.

      Parameters:
      * `class_name`: name for the generated class
      * `struct_format`: the `struct` format string
      * `field_names`: field name list,
        a space separated string or an interable of strings
  '''
  with Pfx("BinaryMultiStruct(%r,%r,%r)", class_name, struct_format,
           field_names):
    if isinstance(field_names, str):
      field_names = field_names.split()
    if not isinstance(field_names, tuple):
      field_names = tuple(field_names)
    if len(set(field_names)) != len(field_names):
      raise ValueError("field names not unique")
    # we memoise the class definitions
    key = (struct_format, field_names, class_name)
    struct_class = _binary_multi_struct_classes.get(key)
    if struct_class:
      return struct_class
    # construct new class
    struct = Struct(struct_format)
    for field_name in field_names:
      with Pfx(field_name):
        if (field_name in ('length', 'struct', 'format')
            or hasattr(AbstractBinary, field_name)):
          raise ValueError(
              "field name conflicts with AbstractBinary.%s" % (field_name,)
          )
    tuple_type = namedtuple(class_name or "StructSubValues", field_names)

    # pylint: disable=function-redefined
    class struct_class(tuple_type, AbstractBinary):
      ''' A struct field for a complex struct format.
      '''

      @classmethod
      def parse(cls, bfr):
        ''' Parse from `bfr` via `struct.unpack`.
        '''
        bs = bfr.take(struct.size)
        values = struct.unpack(bs)
        return cls(*values)

      def transcribe(self):
        ''' Transcribe via `struct.pack`.
        '''
        return struct.pack(*self)

      if len(field_names) == 1:

        def __int__(self):
          return int(self[0])

        def __float__(self):
          return float(self[0])

        @classmethod
        def parse_value(cls, bfr):
          ''' Parse a value from `bfr`, return the value.
          '''
          bs = bfr.take(struct.size)
          value, = struct.unpack(bs)
          return value

        @staticmethod
        def transcribe_value(value):
          ''' Transcribe a value back into bytes.
          '''
          return struct.pack(value)

    struct_class.__name__ = class_name
    struct_class.__doc__ = (
        ''' An `AbstractBinary` `namedtuple` which parses and transcribes
            the struct format `%r` and presents the attributes %r.
        ''' % (struct_format, field_names)
    )
    struct_class.struct = struct
    struct_class.format = struct_format
    struct_class.length = struct.size
    _binary_multi_struct_classes[key] = struct_class
    return struct_class

def BinarySingleStruct(class_name, struct_format, field_name=None):
  ''' A convenience wrapper for `BinaryMultiStruct`
      for `struct_format`s with a single field.

      Parameters:
      * `class_name`: the class name for the generated class
      * `struct_format`: the struct format string, specifying a
        single struct field
      * `field_name`: optional field name for the value,
        default `'value'`

      Example:

          >>> UInt16BE = BinarySingleStruct('UInt16BE', '>H')
          >>> UInt16BE.__name__
          'UInt16BE'
          >>> UInt16BE.format
          '>H'
          >>> UInt16BE.struct   #doctest: +ELLIPSIS
          <_struct.Struct object at ...>
          >>> field = UInt16BE.from_bytes(bytes((2,3)))
          >>> field
          UInt16BE(value=515)
          >>> field.value
          515
  '''
  if field_name is None:
    field_name = 'value'
  return BinaryMultiStruct(class_name, struct_format, field_name)

# various common values
UInt8 = BinarySingleStruct('UInt8', 'B')
UInt8.TEST_CASES = (
    (0, b'\0'),
    (65, b'A'),
)
Int16BE = BinarySingleStruct('Int16BE', '>h')
Int16BE.TEST_CASES = (
    (0, b'\0\0'),
    (1, b'\0\1'),
    (32767, b'\x7f\xff'),
    (-1, b'\xff\xff'),
    (-32768, b'\x80\x00'),
)
Int16LE = BinarySingleStruct('Int16LE', '<h')
Int16LE.TEST_CASES = (
    (0, b'\0\0'),
    (1, b'\1\0'),
    (32767, b'\xff\x7f'),
    (-1, b'\xff\xff'),
    (-32768, b'\x00\x80'),
)
Int32BE = BinarySingleStruct('Int32BE', '>l')
Int32BE.TEST_CASES = (
    (0, b'\0\0\0\0'),
    (1, b'\0\0\0\1'),
    (2147483647, b'\x7f\xff\xff\xff'),
    (-1, b'\xff\xff\xff\xff'),
    (-2147483648, b'\x80\x00\x00\x00'),
)
Int32LE = BinarySingleStruct('Int32LE', '<l')
Int32LE.TEST_CASES = (
    (0, b'\0\0\0\0'),
    (1, b'\1\0\0\0'),
    (2147483647, b'\xff\xff\xff\x7f'),
    (-1, b'\xff\xff\xff\xff'),
    (-2147483648, b'\x00\x00\x00\x80'),
)
UInt16BE = BinarySingleStruct('UInt16BE', '>H')
UInt16BE.TEST_CASES = (
    (0, b'\0\0'),
    (1, b'\0\1'),
    (32767, b'\x7f\xff'),
    (32768, b'\x80\x00'),
    (65535, b'\xff\xff'),
)
UInt16LE = BinarySingleStruct('UInt16LE', '<H')
UInt16LE.TEST_CASES = (
    (0, b'\0\0'),
    (1, b'\1\0'),
    (32767, b'\xff\x7f'),
    (32768, b'\x00\x80'),
    (65535, b'\xff\xff'),
)
UInt32BE = BinarySingleStruct('UInt32BE', '>L')
UInt32BE.TEST_CASES = (
    (0, b'\0\0\0\0'),
    (1, b'\0\0\0\1'),
    (2147483647, b'\x7f\xff\xff\xff'),
    (2147483648, b'\x80\x00\x00\x00'),
    (4294967294, b'\xff\xff\xff\xfe'),
    (4294967295, b'\xff\xff\xff\xff'),
)
UInt32LE = BinarySingleStruct('UInt32LE', '<L')
UInt32LE.TEST_CASES = (
    (0, b'\0\0\0\0'),
    (1, b'\1\0\0\0'),
    (2147483647, b'\xff\xff\xff\x7f'),
    (2147483648, b'\x00\x00\x00\x80'),
    (4294967294, b'\xfe\xff\xff\xff'),
    (4294967295, b'\xff\xff\xff\xff'),
)
UInt64BE = BinarySingleStruct('UInt64BE', '>Q')
UInt64BE.TEST_CASES = (
    (0, b'\0\0\0\0\0\0\0\0'),
    (1, b'\0\0\0\0\0\0\0\1'),
    (2147483647, b'\0\0\0\0\x7f\xff\xff\xff'),
    (2147483648, b'\0\0\0\0\x80\x00\x00\x00'),
    (4294967295, b'\0\0\0\0\xff\xff\xff\xff'),
    (4294967296, b'\0\0\0\1\x00\x00\x00\x00'),
    (9223372036854775807, b'\x7f\xff\xff\xff\xff\xff\xff\xff'),
    (9223372036854775808, b'\x80\x00\x00\x00\x00\x00\x00\x00'),
    (18446744073709551614, b'\xff\xff\xff\xff\xff\xff\xff\xfe'),
    (18446744073709551615, b'\xff\xff\xff\xff\xff\xff\xff\xff'),
)
UInt64LE = BinarySingleStruct('UInt64LE', '<Q')
UInt64LE.TEST_CASES = (
    (0, b'\0\0\0\0\0\0\0\0'),
    (1, b'\1\0\0\0\0\0\0\0'),
    (2147483647, b'\xff\xff\xff\x7f\0\0\0\0'),
    (2147483648, b'\0\0\0\x80\0\0\0\0'),
    (4294967295, b'\xff\xff\xff\xff\0\0\0\0'),
    (4294967296, b'\0\0\0\0\1\0\0\0'),
    (9223372036854775807, b'\xff\xff\xff\xff\xff\xff\xff\x7f'),
    (9223372036854775808, b'\x00\x00\x00\x00\x00\x00\x00\x80'),
    (18446744073709551614, b'\xfe\xff\xff\xff\xff\xff\xff\xff'),
    (18446744073709551615, b'\xff\xff\xff\xff\xff\xff\xff\xff'),
)
Float64BE = BinarySingleStruct('Float64BE', '>d')
Float64BE.TEST_CASES = (
    (0.0, b'\0\0\0\0\0\0\0\0'),
    (1.0, b'?\xf0\x00\x00\x00\x00\x00\x00'),
)
Float64LE = BinarySingleStruct('Float64LE', '<d')
Float64LE.TEST_CASES = (
    (0.0, b'\0\0\0\0\0\0\0\0'),
    (1.0, b'\x00\x00\x00\x00\x00\x00\xf0?'),
)

class BSUInt(BinarySingleValue):
  ''' A binary serialised unsigned int.

      This uses a big endian byte encoding where continuation octets
      have their high bit set. The bits contributing to the value
      are in the low order 7 bits.
  '''

  TEST_CASES = (
      (0, b'\0'),
      (1, b'\1'),
      (127, b'\x7f'),
      (128, b'\x81\00'),
      (255, b'\x81\x7f'),
      (16383, b'\xff\x7f'),
      (16384, b'\x81\x80\x00'),
  )

  @staticmethod
  def parse_value(bfr):
    ''' Parse an extensible byte serialised unsigned int from a buffer.

        Continuation octets have their high bit set.
        The value is big-endian.

        This is the go for reading from a stream. If you already have
        a bare bytes instance then the `.parse_bytes` static method
        is probably more direct.
    '''
    n = 0
    b = 0x80
    while b & 0x80:
      bs = bfr.take(1)
      b = bs[0]
      n = (n << 7) | (b & 0x7f)
    return n

  @staticmethod
  # pylint: disable=arguments-differ
  def parse_bytes(data, offset=0):
    ''' Read an extensible byte serialised unsigned int from `data` at `offset`.
        Return value and new offset.

        Continuation octets have their high bit set.
        The octets are big-endian.

        If you just have a `bytes` instance, this is the go. If you're
        reading from a stream you're better off with `cs.binary.BSUInt`.

        Examples:

            >>> BSUInt.parse_bytes(b'\\0')
            (0, 1)
    '''
    n = 0
    b = 0x80
    while b & 0x80:
      b = data[offset]
      offset += 1
      n = (n << 7) | (b & 0x7f)
    return n, offset

  # pylint: disable=arguments-differ
  @staticmethod
  def transcribe_value(n):
    ''' Encode an unsigned int as an entensible byte serialised octet
        sequence for decode. Return the bytes object.
    '''
    bs = [n & 0x7f]
    n >>= 7
    while n > 0:
      bs.append(0x80 | (n & 0x7f))
      n >>= 7
    return bytes(reversed(bs))

class BSData(BinarySingleValue):
  ''' A run length encoded data chunk, with the length encoded as a `BSUInt`.
  '''

  TEST_CASES = (
      (b'', b'\x00'),
      (b'A', b'\x01A'),
  )

  @property
  def data(self):
    ''' An alias for the `.value` attribute.
    '''
    return self.value

  @property
  def data_offset(self):
    ''' The length of the length indicator,
        useful for computing the location of the raw data.
    '''
    return len(BSUInt(len(self.value)))

  @classmethod
  def parse_value(cls, bfr):
    ''' Parse the data from `bfr`.
    '''
    data_length = BSUInt.parse_value(bfr)
    data = bfr.take(data_length)
    return data

  # pylint: disable=arguments-differ
  @staticmethod
  def transcribe_value(data):
    ''' Transcribe the payload length and then the payload.
    '''
    yield BSUInt.transcribe_value(len(data))
    yield data

  @staticmethod
  def data_offset_for(bs):
    ''' Compute the `data_offset` which would obtain for the bytes `bs`.
    '''
    return BSData(bs).data_offset

class BSString(BinarySingleValue):
  ''' A run length encoded string, with the length encoded as a BSUInt.
  '''

  TEST_CASES = (
      ('', b'\x00'),
      ('A', b'\x01A'),
  )

  def __init__(self, s, encoding='utf-8'):
    super().__init__(s)
    self.encoding = encoding

  # pylint: disable=arguments-differ
  @staticmethod
  def parse_value(bfr, encoding='utf-8', errors='strict'):
    ''' Parse a run length encoded string from `bfr`.
    '''
    strlen = BSUInt.parse_value(bfr)
    bs = bfr.take(strlen)
    if isinstance(bs, memoryview):
      bs = bs.tobytes()
    return bs.decode(encoding=encoding, errors=errors)

  # pylint: disable=arguments-differ
  @staticmethod
  def transcribe_value(s, encoding='utf-8'):
    ''' Transcribe a string.
    '''
    payload = s.encode(encoding)
    return b''.join((BSUInt.transcribe_value(len(payload)), payload))

class BSSFloat(BinarySingleValue):
  ''' A float transcribed as a BSString of str(float).
  '''

  TEST_CASES = (
      (0.0, b'\x030.0'),
      (0.1, b'\x030.1'),
  )

  @classmethod
  def parse_value(cls, bfr):
    ''' Parse a BSSFloat from a buffer and return the float.
    '''
    s = BSString.parse_value(bfr)
    return float(s)

  # pylint: disable=arguments-differ
  @staticmethod
  def transcribe_value(f):
    ''' Transcribe a float.
    '''
    return BSString.transcribe_value(str(f))

class _BinaryMultiValue_Field(namedtuple('_BinaryMultiValue_Field',
                                         'name spec cls parse transcribe')):
  ''' A `namedtuple` supporting `BinaryMultiValue` with the following attributes:
      * `name`: the field name
      * `spec`: the original specification passed to `pt_spec()`
      * `cls`: the class associated with the field
      * `parse`: a `parse(bfr)` function returning the value for the field
      * `transcribe`: a `transcribe(field_value)` function transcribing the field
  '''

class _BinaryMultiValue_Base(SimpleBinary):
  ''' The base class underlying classes constructed by `BinaryMultiValue`.
      This is used for compound objects whose components
      may themselves be `AbstractBinary` instances.

      The `parse`, `parse_field`, `transcribe` and `transcribe_field` methods
      supplied by this base class rely on the class attributes:
      * `FIELD_ORDER`: a list of field names to parse or transcribe
        by the defaule `parse` and `transcribe` methods
      * `FIELDS`: a mapping of field names to `_BinaryMultiValue_Field` instances

      These are _not_ defined on this base class
      and must be defined on the subclass
      in order that subclasses to have their own mappings.
      That is done by the `BinaryMultiValue` class factory.
  '''

  def s(self, *, crop_length=64, choose_name=None):
    ''' Common implementation of `__str__` and `__repr__`.
        Transcribe type and attributes, cropping long values
        and omitting private values.

        Parameters:
        * `crop_length`: maximum length of values before cropping, default `32`
        * `choose_name`: test for names to include, default excludes `_`*
    '''
    if choose_name is None:
      choose_name = getattr(
          self, 'S_CHOOSE_NAME', lambda name: not name.startswith('_')
      )
    return "%s(%s)" % (
        type(self).__name__,
        cropped(
            ','.join(
                [
                    "%s=%s" % (k, cropped_repr(v, max_length=crop_length))
                    for k, v in sorted(self.__dict__.items())
                    if choose_name(k)
                ]
            ),
            max_length=crop_length,
            roffset=0
        )
    )

  __str__ = s
  ##__repr__ = s

  @classmethod
  def parse(cls, bfr):
    ''' Default parse: parse each predefined field from the buffer in order
        and set the associated attributes.

        Subclasses might override this if they have a flexible structure
        where not all fields necessarily appear.
        A `parse(bfr)` method for a flexible structure
        may expect some subfields only in certain circumstances
        and use `parse_field` to parse them as required.
        This requires a matching `transcribe()` method.

        Example:

            def parse(cls, bfr):
              """ Read a leading unsigned 8 bit integer
                  holding a structure version.
                  If the version is 0,
                  read into the `.v0data` field;
                  if the version is 1,
                  read into the `.v1data` field;
                  otherwise raise a `ValueError` for an unsupported version byte.

                  The data formats for `.v0data` and `.v1data`
                  come from the specification provided to `BinaryMultiValue()`.
              """
              self = cls()
              self.version = UInt8.parse_value(bfr)
              if self.version == 0:
                self.parse_field('v0data', bfr)
              elif self.version == 1:
                self.parse_field('v1data', bfr)
              else:
                raise ValueError("unsupported version %d" % (self.version,))
              return self
    '''
    self = cls()
    for field_name in cls.FIELD_ORDER:
      with Pfx(field_name):
        self.parse_field(field_name, bfr)
    return self

  def parse_field(self, field_name, bfr, **kw):
    ''' Parse `bfr` for the data for `field_name`
        and set the associated attribute.
    '''
    field = self.FIELDS[field_name]
    value = field.parse(bfr, **kw)
    setattr(self, field_name, value)

  def transcribe(self):
    ''' Default transcribe: yield each field's transcription in order
        using the `transcribe_field` method.

        If a subclass overrides `parse(bfr)` to implement a flexible structure
        the must also override this method to match.
        See the notes in the default `parse()` method.
    '''
    for field_name in self.FIELD_ORDER:
      with Pfx(field_name):
        transcription = self.transcribe_field(field_name)
      # outside Pfx because this is a generator :-(
      yield transcription

  def transcribe_field(self, field_name):
    ''' Return the transcription of `field_name`.
    '''
    field = self.FIELDS[field_name]
    field_value = getattr(self, field_name)
    with Pfx("transcribe %s", field_value):
      return field.transcribe(field_value)

def BinaryMultiValue(class_name, field_map, field_order=None):
  ''' Construct a `SimpleBinary` subclass named `class_name`
      whose fields are specified by the mapping `field_map`.

      The `field_map` is a mapping of field name to buffer parsers and transcribers.

      *Note*:
      if `field_order` is not specified
      it is constructed by iterating over `field_map`.
      Prior to Python 3.6, `dict`s do not provide a reliable order
      and should be accompanied by an explicit `field_order`.
      From 3.6 onward a `dict` is enough and its insertion order
      will dictate the default `field_order`.

      For a fixed record structure
      the default `.parse` and `.transcribe` methods will suffice;
      they parse or transcribe each field in turn.
      Subclasses with variable records should override
      the `.parse` and `.transcribe` methods
      accordingly.

      The `field_map` is a mapping of field name
      to a class returned by the `pt_spec()` function.

      If the class has both `parse_value` and `transcribe_value` methods
      then the valueitself will be directly stored.
      Otherwise the class it presumed to be more complex some `AbstractBinary` subclass
      and the instance is stored.

      Here is an example exhibiting various ways of defining each field:
      * `n1`: defined with the *`_value` methods of `UInt8`,
        which return or transcribe the `int` from an unsigned 8 bit value;
        this stores a `BinarySingleValue` whole `.value` is an `int`
      * `n2`: defined from the `UInt8` class,
        which parses an unsigned 8 bit value;
        this stores an `UInt8` instance
        (also a `BinarySingleValue` whole `.value` is an `int`)
      * `n3`: like `n2`
      * `data1`: defined with the *`_value` methods of `BSData`,
        which return or transcribe the data `bytes`
        from a run length encoded data chunk;
        this stores a `BinarySingleValue` whole `.value` is a `bytes`
      * `data2`: defined from the `BSData` class
        which parses a run length encoded data chunk;
        this stores a `BSData` instance
        (also a `BinarySingleValue` whole `.value` is a `bytes`)

          >>> class BMV(BinaryMultiValue("BMV", {
          ...         'n1': (UInt8.parse_value, UInt8.transcribe_value),
          ...         'n2': UInt8,
          ...         'n3': UInt8,
          ...         'nd': ('>H4s', 'short bs'),
          ...         'data1': (
          ...             BSData.parse_value,
          ...             BSData.transcribe_value,
          ...         ),
          ...         'data2': BSData,
          ... })):
          ...     pass
          >>> BMV.FIELD_ORDER
          ['n1', 'n2', 'n3', 'nd', 'data1', 'data2']
          >>> bmv = BMV.from_bytes(b'\\x11\\x22\\x77\\x81\\x82zyxw\\x02AB\\x04DEFG')
          >>> bmv.n1  #doctest: +ELLIPSIS
          17
          >>> bmv.n2
          34
          >>> bmv  #doctest: +ELLIPSIS
          BMV(n1=17, n2=34, n3=119, nd=nd_1_short_bs(short=33154, bs=b'zyxw'), data1=b'AB', data2=b'DEFG')
          >>> bmv.nd  #doctest: +ELLIPSIS
          nd_1_short_bs(short=33154, bs=b'zyxw')
          >>> bmv.nd.bs
          b'zyxw'
          >>> bytes(bmv.nd)
          b'\x81\x82zyxw'
          >>> bmv.data1  #doctest: +ELLIPSIS
          b'AB'
          >>> bmv.data2
          b'DEFG'
          >>> bytes(bmv)
          b'\\x11"w\\x81\\x82zyxw\\x02AB\\x04DEFG'
          >>> list(bmv.transcribe_flat())
          [b'\\x11', b'"', b'w', b'\\x81\\x82zyxw', b'\\x02', b'AB', b'\\x04', b'DEFG']
  '''  # pylint: disable=line-too-long
  with Pfx("BinaryMultiValue(%r,...)", class_name):
    if field_order is None:
      field_order = tuple(field_map)
      if (sys.version_info.major, sys.version_info.minor) < (3, 6):
        warning(
            "Python version %s < 3.6: dicts are not ordered,"
            " and the inferred field order may not be correct: %r",
            sys.version, field_order
        )
    else:
      field_order = tuple(
          field_order.split() if isinstance(field_order, str) else field_order
      )

    class bmv_class(_BinaryMultiValue_Base):
      ''' `_BinaryMultiValue_Base` subclass implementation.
      '''

      # collate the parse-transcribe functions for each predefined field
      FIELD_ORDER = list(field_order)
      FIELDS = {}

    # set up the field mappings outside the class
    # to prevent the work variables leaking into the class attributes
    for field_name in bmv_class.FIELD_ORDER:
      spec = field_map[field_name]
      cls = pt_spec(spec, name=field_name)
      try:
        parse = cls.parse_value
        transcribe = cls.transcribe_value
      except AttributeError:
        parse = cls.parse
        transcribe = cls.transcribe
      field = _BinaryMultiValue_Field(
          name=field_name,
          spec=spec,
          cls=cls,
          parse=parse,
          transcribe=transcribe,
      )
      bmv_class.FIELDS[field_name] = field

    bmv_class.__name__ = class_name
    bmv_class.__doc__ = (
        ''' An `SimpleBinary` which parses and transcribes
            the fields `%r`.
        ''' % (field_order,)
    )
    return bmv_class

def BinaryFixedBytes(class_name, length: int):
  ''' Factory for an `AbstractBinary` subclass matching `length` bytes of data.
      The bytes are saved as the attribute `.data`.
  '''
  return BinarySingleStruct(class_name, f'>{length}s', 'data')

class BinaryUTF8NUL(BinarySingleValue):
  ''' A NUL terminated UTF-8 string.
  '''

  FIELD_TYPES = dict(value=str)

  TEST_CASES = (
      b'123\0',
      ('123', {}, b'123\0'),
  )

  @staticmethod
  def parse_value(bfr):
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
    return utf8

  # pylint: disable=arguments-differ
  @staticmethod
  def transcribe_value(s):
    ''' Transcribe the `value` in UTF-8 with a terminating NUL.
    '''
    yield s.encode('utf-8')
    yield b'\0'

class BinaryUTF16NUL(BinarySingleValue):
  ''' A NUL terminated UTF-16 string.
  '''

  FIELD_TYPES = dict(encoding=str, value=str)

  TEST_CASES = (
      ('abc', {
          'encoding': 'utf_16_le'
      }, b'a\x00b\x00c\x00\x00\x00'),
      ('abc', {
          'encoding': 'utf_16_be'
      }, b'\x00a\x00b\x00c\x00\x00'),
  )

  VALID_ENCODINGS = ('utf_16_le', 'utf_16_be')

  # pylint: disable=super-init-not-called
  def __init__(self, value, *, encoding):
    if encoding not in self.VALID_ENCODINGS:
      raise ValueError(
          'unexpected encoding %r, expected one of %r' %
          (encoding, self.VALID_ENCODINGS)
      )
    self.encoding = encoding
    self.value = value

  # pylint: disable=arguments-differ
  @classmethod
  def parse(cls, bfr, *, encoding):
    ''' Parse the encoding and value and construct an instance.
    '''
    value = cls.parse_value(bfr, encoding=encoding)
    return cls(value, encoding=encoding)

  # pylint: disable=arguments-differ
  @staticmethod
  def parse_value(bfr, *, encoding):
    ''' Read a NUL terminated UTF-16 string from `bfr`, return a `UTF16NULField`..
        The mandatory parameter `encoding` specifies the UTF16 encoding to use
        (`'utf_16_be'` or `'utf_16_le'`).
    '''
    # probe for the terminating NUL
    bs_length = 2
    while True:
      bfr.extend(bs_length)
      nul_pos = bs_length - 2
      if bfr[nul_pos] == 0 and bfr[nul_pos + 1] == 0:
        break
      bs_length += 2
    if nul_pos == 0:
      utf16 = ''
    else:
      utf16_bs = bfr.take(nul_pos)
      utf16 = utf16_bs.decode(encoding)
    bfr.take(2)
    return utf16

  def transcribe(self):
    ''' Transcribe `self.value` in UTF-16 with a terminating NUL.
    '''
    yield from self.transcribe_value(self.value, encoding=self.encoding)

  # pylint: disable=arguments-differ
  @staticmethod
  def transcribe_value(value, encoding='utf-16'):
    ''' Transcribe `value` in UTF-16 with a terminating NUL.
    '''
    yield value.encode(encoding)
    yield b'\0\0'

#############################################################################
## DEPRECATED CLASSES BELOW.
##

class PacketField(ABC):
  ''' A record for an individual packet field.

      *DEPRECATED*:
      please adopt one of the `BinarySingle`* classes instead.

      This normally holds a single value,
      for example an int of a particular size or a string.

      There are 2 basic ways to implement a `PacketField` subclass:
      * simple: implement `value_from_buffer` and `transcribe_value`
      * complex: implement `from_buffer` and `transcribe`

      In the simple case subclasses should implement two methods:
      * `value_from_buffer`:
        parse the value from a `CornuCopyBuffer` and return the parsed value
      * `transcribe_value`:
        transcribe the value as bytes

      In the more complex case,
      sometimes a `PacketField` may not warrant (or perhaps fit)
      the formality of a `Packet` with its multifield structure.

      One example is the `cs.iso14496.UTF8or16Field` class.

      `UTF8or16Field` supports an ISO14496 UTF8 or UTF16 string field,
      as as such has 2 attributes:
      * `value`: the string itself
      * `bom`: a UTF16 byte order marker or `None`;
        `None` indicates that the string should be encoded as UTF-8
        and otherwise the BOM indicates UTF16 big endian or little endian.

      To make this subclass it defines these methods:
      * `from_buffer`:
        to read the optional BOM and then the following encoded string;
        it then returns the new `UTF8or16Field`
        initialised from these values via `cls(text, bom=bom)`.
      * `transcribe`:
        to transcribe the optional BOM and suitably encoded string.
      The instance method `transcribe` is required because the transcription
      requires knowledge of the BOM, an attribute of an instance.
  '''

  def __init__(self, value=None):
    ''' Initialise the `PacketField`.
        If omitted the inial field `value` will be `None`.
    '''
    self.value = value

  @property
  def value_s(self):
    ''' The preferred string representation of the value.
    '''
    return str(self.value)

  def __str__(self):
    return "%s(%s)" % (type(self).__name__, self.value_s)

  def __eq__(self, other):
    return type(self) is type(other) and self.value == other.value

  def __bytes__(self):
    return b''.join(flatten(self.transcribe()))

  def __len__(self):
    ''' Compute the length by running a transcription and measuring it.
    '''
    return sum(len(bs) for bs in flatten(self.transcribe()))

  @classmethod
  def from_bytes(cls, bs, offset=0, length=None, **kw):
    ''' Factory to return a `PacketField` instance parsed from the
        bytes `bs` starting at `offset`.
        Returns the new `PacketField` and the post parse offset.

        The parameters `offset` and `length` are as for the
        `CornuCopyBuffer.from_bytes` factory.

        This relies on the `cls.from_buffer` method for the parse.
    '''
    bfr = CornuCopyBuffer.from_bytes(bs, offset=offset, length=length)
    field = cls.from_buffer(bfr, **kw)
    post_offset = offset + bfr.offset
    return field, post_offset

  @classmethod
  def value_from_bytes(cls, bs, offset=0, length=None, **kw):
    ''' Return a value parsed from the bytes `bs` starting at `offset`.
        Returns the new value and the post parse offset.

        The parameters `offset` and `length` are as for the
        `CornuCopyBuffer.from_bytes` factory.

        This relies on the `cls.from_bytes` method for the parse.
    '''
    field, offset = cls.from_bytes(bs, offset=offset, length=length, **kw)
    return field.value, offset

  @classmethod
  def from_buffer(cls, bfr, **kw):
    ''' Factory to return a `PacketField` instance from a `CornuCopyBuffer`.

        This default implementation is for single value `PacketField`s
        and instantiates the instance from the value returned
        by `cls.value_from_buffer(bfr, **kw)`;
        implementors should implement `value_from_buffer`.
    '''
    value = cls.value_from_buffer(bfr, **kw)
    return cls(value)

  @classmethod
  def value_from_buffer(cls, bfr, **kw):
    ''' Function to parse and return the core value from a `CornuCopyBuffer`.

        For single value fields it is enough to implement this method.

        For multiple value fields it is better to implement `cls.from_buffer`.
    '''
    packet = cls.from_buffer(bfr, **kw)
    return packet.value

  @classmethod
  def parse_buffer_with_offsets(cls, bfr, **kw):
    ''' Function to parse repeated instances of `cls` from the buffer `bfr`
        until end of input.
        Yields `(offset,instance,post_offset)`
        where `offset` if the buffer offset where the instance commenced
        and `post_offset` is the buffer offset after the instance.
    '''
    offset = bfr.offset
    while not bfr.at_eof():
      post_offset = bfr.offset
      yield offset, cls.from_buffer(bfr, **kw), post_offset
      offset = post_offset

  @classmethod
  def parse_buffer(cls, bfr, **kw):
    ''' Function to parse repeated instances of `cls` from the buffer `bfr`
        until end of input.
    '''
    for _, obj, _ in cls.parse_buffer_with_offsets(bfr, **kw):
      yield obj

  @classmethod
  def parse_buffer_values(cls, bfr, **kw):
    ''' Function to parse repeated instances of `cls.value`
        from the buffer `bfr` until end of input.
    '''
    for _, obj, _ in cls.parse_buffer_with_offsets(bfr, **kw):
      yield obj.value

  @classmethod
  def parse_file(cls, f, **kw):
    ''' Function to parse repeated instances of `cls` from the file `f`
        until end of input.

        Parameters:
        * `f`: the binary file object to parse;
          if `f` is a string, that pathname is opened for binary read.
    '''
    if isinstance(f, str):
      with open(f, 'rb') as f2:
        yield from cls.parse_file(f2, **kw)
    else:
      yield from cls.parse_buffer(CornuCopyBuffer.from_file(f), **kw)

  def transcribe(self):
    ''' Return or yield the bytes transcription of this field.

        This may directly return:
        * a `bytes` or `memoryview` holding the binary data
        * `None`: indicating no binary data
        * `str`: indicating the ASCII encoding of the string
        * an iterable of these things (including further iterables)
          to support trivially transcribing via other fields'
          `transcribe` methods

        Callers will usually call `flatten` on the output of this
        method, or use the convenience `transcribe_flat` method
        which calls `flatten` for them.

        This default implementation is for single value fields and
        just calls `self.transcribe_value(self.value)`.
    '''
    yield self.transcribe_value(self.value)

  @classmethod
  def transcribe_value(cls, value, **kw):
    ''' For simple `PacketField`s, return a bytes transcription of a
        value suitable for the `.value` attribute.

        For example, the `BSUInt` subclass stores a `int` as its
        `.value` and exposes its serialisation method, suitable for
        any `int`, as `transcribe_value`.

        Note that this calls the class `transcribe` method, which
        may return an iterable.
        Use the `value_as_bytes` method to get a single flat `bytes` result.
    '''
    return cls(value, **kw).transcribe()

  @classmethod
  def value_as_bytes(cls, value, **kw):
    ''' For simple `PacketField`s, return a transcription of a
        value suitable for the `.value` attribute
        as a single `bytes` value.

        This flattens and joins the transcription returned by
        `transcribe_value`.
    '''
    return b''.join(flatten(cls.transcribe_value(value, **kw)))

  def transcribe_flat(self):
    ''' Return a flat iterable of chunks transcribing this field.
    '''
    return flatten(self.transcribe())

  @classmethod
  def transcribe_value_flat(cls, value):
    ''' Return a flat iterable of chunks transcribing `value`.
    '''
    return flatten(cls.transcribe_value(value))

class EmptyPacketField(PacketField):
  ''' An empty data field, used as a placeholder for optional
      fields when they are not present.

      The singleton `EmptyField` is a predefined instance.
  '''

  TEST_CASES = (
      b'',
      ({}, b''),
  )

  def __init__(self):
    super().__init__(None)

  # pylint: disable=arguments-differ
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

  TEST_CASES = (
      b'123\0',
      ('123', {}, b'123\0'),
  )

  # pylint: disable=arguments-differ
  @classmethod
  def value_from_buffer(cls, bfr):
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
    return utf8

  # pylint: disable=arguments-differ
  @staticmethod
  def transcribe_value(s):
    ''' Transcribe the `value` in UTF-8 with a terminating NUL.
    '''
    yield s.encode('utf-8')
    yield b'\0'

class UTF16NULField(PacketField):
  ''' A NUL terminated UTF-16 string.
  '''

  TEST_CASES = (
      ('abc', {
          'encoding': 'utf_16_le'
      }, b'a\x00b\x00c\x00\x00\x00'),
      ('abc', {
          'encoding': 'utf_16_be'
      }, b'\x00a\x00b\x00c\x00\x00'),
  )

  VALID_ENCODINGS = ('utf_16_le', 'utf_16_be')

  # pylint: disable=super-init-not-called
  def __init__(self, value, *, encoding):
    ''' Initialise the `PacketField`.
        If omitted the inial field `value` will be `None`.
    '''
    if encoding not in self.VALID_ENCODINGS:
      raise ValueError(
          'unexpected encoding %r, expected one of %r' %
          (encoding, self.VALID_ENCODINGS)
      )
    self.encoding = encoding
    self.value = value

  # pylint: disable=arguments-differ
  @classmethod
  def from_buffer(cls, bfr, encoding):
    ''' Read a NUL terminated UTF-16 string from `bfr`, return a `UTF16NULField`..
        The mandatory parameter `encoding` specifies the UTF16 encoding to use
        (`'utf_16_be'` or `'utf_16_le'`).
    '''
    # probe for the terminating NUL
    bs_length = 2
    while True:
      bfr.extend(bs_length)
      nul_pos = bs_length - 2
      if bfr[nul_pos] == 0 and bfr[nul_pos + 1] == 0:
        break
      bs_length += 2
    if nul_pos == 0:
      utf16 = ''
    else:
      utf16_bs = bfr.take(nul_pos)
      utf16 = utf16_bs.decode(encoding)
    bfr.take(2)
    return cls(utf16, encoding=encoding)

  def transcribe(self):
    yield from self.transcribe_value(self.value, encoding=self.encoding)

  # pylint: disable=arguments-differ
  @staticmethod
  def transcribe_value(value, encoding='utf-16'):
    ''' Transcribe `value` in UTF-16 with a terminating NUL.
    '''
    yield value.encode(encoding)
    yield b'\0\0'

class BytesField(BinarySingleValue):
  ''' A field of bytes.
  '''

  TEST_CASES = (
      ##(b'1234', {'length': 4}, b'1234'),
  )

  @property
  def data(self):
    ''' Alias for the `.value` attribute.
    '''
    return self.value

  @property
  def length(self):
    ''' Convenient length attribute.
    '''
    return len(self.value)

  def __len__(self):
    ''' The length is the length of the data.
    '''
    return len(self.value)

  @classmethod
  def value_from_buffer(cls, bfr, length=None):
    ''' Parse a `BytesField` of length `length` from `bfr`.
    '''
    if length < 0:
      raise ValueError("length(%d) < 0" % (length,))
    return bfr.take(length)

  @staticmethod
  def transcribe_value(value):
    ''' A `BytesField` is its own transcription.
    '''
    return value

def fixed_bytes_field(length, class_name=None):
  ''' Factory for `BytesField` subclasses built from fixed length byte strings.
  '''
  if length < 1:
    raise ValueError("length(%d) < 1" % (length,))

  class FixedBytesField(BytesField):
    ''' A field whose value is simply a fixed length bytes chunk.
    '''

    @classmethod
    def parse_value(cls, bfr):
      ''' Obtain fixed bytes from the buffer.
      '''
      return bfr.take(length)

  if class_name is None:
    class_name = FixedBytesField.__name__ + '_' + str(length)
  FixedBytesField.__name__ = class_name
  FixedBytesField.__doc__ = (
      'A `PacketField` which fetches and transcribes a fixed with bytes chunk of length %d.'
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
        type(self).__name__, self.offset, self.end_offset,
        "NO_DATA" if self.value is None else "bytes[%d]" % len(self.value)
    )

  def __len__(self):
    return self.length

  def __iter__(self):
    yield from self.value

  # pylint: disable=arguments-differ
  @classmethod
  def from_buffer(
      cls, bfr, end_offset=None, discard_data=False, short_ok=False
  ):
    ''' Create a new `BytesesField` from a buffer
        by gathering from `bfr` until `end_offset`.

        Parameters:
        * `bfr`: the input buffer
        * `end_offset`: the ending buffer offset; if this is Ellipsis
          then all the remaining data in `bfr` will be collected
        * `discard_data`: discard the data, keeping only the offset information
        * `short_ok`: if true, do not raise EOFError if there are
          insufficient data; the field's .end_offset value will be
          less than `end_offset`; the default is False

        Note that this method also sets the following attributes
        on the new `BytesesField`:
        * `offset`: the starting offset of the gathered bytes
        * `end_offset`: the ending offset after the gathered bytes
        * `length`: the length of the data
    '''
    if end_offset is None:
      raise ValueError("missing end_offset")
    offset0 = bfr.offset
    byteses = None if discard_data else []
    if end_offset is Ellipsis:
      # special case: gather up all the remaining data
      bfr_end_offset = bfr.end_offset
      if discard_data:
        if bfr_end_offset is not None:
          # we can skip to the end
          bfr.skipto(bfr_end_offset)
        else:
          # TODO: try hinting in increasing powers of 2?
          for _ in bfr:
            pass
      else:
        # gather up all the data left in the buffer
        if bfr_end_offset is not None:
          bfr.hint(bfr_end_offset - bfr.offset)
        byteses.extend(bfr)
    else:
      # otherwise gather up a bounded range of bytes
      if end_offset < offset0:
        raise ValueError(
            "end_offset(%d) < bfr.offset(%d)" % (end_offset, bfr.offset)
        )
      bfr.skipto(
          end_offset,
          copy_skip=(None if discard_data else byteses.append),
          short_ok=short_ok
      )
    offset = bfr.offset
    if end_offset is not Ellipsis and offset < end_offset and not short_ok:
      raise EOFError(
          "%s.from_buffer: insufficient input data: end_offset=%d"
          " but final bfr.offset=%d" % (cls, end_offset, bfr.offset)
      )
    field = cls(byteses)
    # pylint: disable=attribute-defined-outside-init
    field.offset = offset0
    field.end_offset = offset
    field.length = offset - offset0
    return field

  transcribe = __iter__

class BytesRunField(PacketField):
  ''' A field containing a continuous run of a single bytes value.

      The following attributes are defined:
      * `length`: the length of the run
      * `bytes_value`: the repeated bytes value

      The property `value` is computed on the fly on every reference
      and returns a value obeying the buffer protocol: a bytes or
      memoryview object.
  '''

  # pylint: disable=super-init-not-called
  def __init__(self, length, bytes_value):
    if length < 0:
      raise ValueError("invalid length(%r), should be >= 0" % (length,))
    if len(bytes_value) != 1:
      raise ValueError(
          "only single byte bytes_value is supported, received: %r" %
          (bytes_value,)
      )
    self.length = length
    self.bytes_value = bytes_value

  def __str__(self):
    return "%s(%d*%r)" % (type(self).__name__, self.length, self.bytes_value)

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

  # pylint: disable=arguments-differ
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
    while length >= 256:
      yield bs256
      length -= 256
    if length > 0:
      yield bs256[:length]

class ListField(PacketField):
  ''' A field which is itself a list of other `PacketField`s.
  '''

  def __str__(self):
    value = self.value
    length = len(value)
    if length > 16:
      suffix = ',...'
      value = value[:16]
    else:
      suffix = ''
    return '[' + str(length) + ':' + ','.join(
        str(o) for o in value
    ) + suffix + ']'

  def __iter__(self):
    ''' Iterating over a `ListField` iterates over its `.value`.
    '''
    return iter(self.value)

  # pylint: disable=arguments-differ
  @classmethod
  def from_buffer(cls, bfr):
    ''' ListFields do not know enough to parse a buffer.
    '''
    raise NotImplementedError(
        "%s cannot be parsed directly from a buffer" % (cls,)
    )

  # pylint: disable=arguments-differ
  @staticmethod
  def transcribe_value(value):
    ''' Transcribe each item in `value`.
    '''
    for item in value:
      yield item.transcribe()

_multi_struct_fields = {}

def multi_struct_field(struct_format, subvalue_names=None, class_name=None):
  ''' A class factory for `PacketField` subclasses built around complex `struct` formats.

      **Deprecated**: see the `BinaryMultiValue` factory instead.

      See also the convenience class factory `structtuple`
      which is usually easier to work with.

      Parameters:
      * `struct_format`: the `struct` format string
      * `subvalue_names`: an optional field name list;
        if supplied then the field value will be a `namedtuple` with
        these names
      * `class_name`: option name for the generated class
  '''
  # we memoise the class definitions
  key = (struct_format, subvalue_names, class_name)
  MultiStructField = _multi_struct_fields.get(key)
  if not MultiStructField:
    # new class
    struct = Struct(struct_format)
    if subvalue_names:
      if 'length' in subvalue_names:
        warning(
            "conflicting field 'length' in multi_struct_field(class_name=%s) subvalue_names %r",
            class_name, subvalue_names
        )
      subvalues_type = namedtuple(
          class_name or "StructSubValues", subvalue_names
      )

    class MultiStructField(PacketField):
      ''' A struct field for a complex struct format.
      '''

      def __str__(self):
        return str(self.value)

      if subvalue_names:

        def _asdict(self):
          return self.value._asdict()
      else:

        def _asdict(self):
          return dict(value=self.value)

      length = struct.size

      # pylint: disable=arguments-differ
      @classmethod
      def from_buffer(cls, bfr):
        ''' Parse via `struct.unpack`.
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
          ''' A `PacketField` which parses and transcribes the struct
              format `%r`, whose `.value` is a `namedtuple` with
              attributes %r.
          ''' % (struct_format, subvalue_names)
      )
    else:
      MultiStructField.__doc__ = (
          ''' A `PacketField` which parses and transcribes the struct
              format `%r`, whose `.value` is a `tuple` of the struct values.
          ''' % (struct_format,)
      )
    MultiStructField.struct = struct
    MultiStructField.format = struct_format
    if subvalue_names:
      MultiStructField.subvalue_names = subvalue_names
    _multi_struct_fields[key] = MultiStructField
  return MultiStructField

def structtuple(class_name, struct_format, subvalue_names):
  ''' Convenience wrapper for `multi_struct_field`.

      Example:

          Enigma2Cut = structtuple('Enigma2Cut', '>QL', 'pts type')

      which is a record with big-endian unsigned 64 and 32 fields
      named `pts` and `type`.
  '''
  return multi_struct_field(
      struct_format, subvalue_names=subvalue_names, class_name=class_name
  )

_TestStructTuple = structtuple(
    '_TestStructTuple', '>hHlLqQ', 'short ushort long ulong quad uquad'
)
_TestStructTuple.TEST_CASES = (
    b'\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0',
    ((-1, 2, -2, 4, -3, 8),),
    ##    ({  'short': -1,
    ##        'ushort': 2,
    ##        'long': -2,
    ##        'ulong': 4,
    ##        'quad': -3,
    ##        'uquad': 8,
    ##    },),
)

class Packet(PacketField):
  ''' Base class for compound objects derived from binary data.

      *DEPRECATED*:
      please adopt one of the `BinaryMutli`* classes instead.
  '''

  def __init__(self, **fields):
    ''' Initialise the `Packet`.

        A `Packet` is its own `.value`.

        If any keyword arguments are provided, they are used as a
        mapping of `field_name` to `Field` instance, supporting
        direct construction of simple `Packet`s.
        From Python 3.6 onwards keyword arguments preserve the calling order;
        in Python versions earlier than this the caller should
        adjust the `Packet.field_names` list to the correct order after
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

  def __str__(self, skip_fields=None):
    return "%s(%s)" % (
        type(self).__name__, ','.join(
            "%s=%s" % (field_name, self.field_map[field_name])
            for field_name in self.field_names
            if skip_fields is None or field_name not in skip_fields
        )
    )

  def get_field(self, field_name):
    ''' Return the field named `field_name`.
    '''
    try:
      return self.field_map[field_name]
    except KeyError:
      # pylint: disable=raise-missing-from
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
    ''' Internal self check. Returns `True` if passed.

        If the `Packet` has a `PACKET_FIELDS` attribute, normally a
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

    def w(msg, *a):
      type_name = type(self).__name__
      try:
        packet_str = str(self)
      except Exception as e:  # pylint: disable=broad-except
        warning("%s.self_check: str(self) fails: %s", type_name, e)
        packet_str = "%d:no-str()" % (id(self),)
      return warning(
          "%s.self_check: " + msg + " [%s]",
          type(self).__name__, *a, packet_str
      )

    ok = True
    try:
      fields_spec = self.PACKET_FIELDS
    except AttributeError:
      w("no PACKET_FIELDS")
      ok = False
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
            w("field %r missing", field_name)
            ok = False
        else:
          if not isinstance(field, basetype):
            w(
                "field %r should be an instance of %s:%s but is %s:%s: %s",
                field_name,
                'tuple' if isinstance(basetype, tuple) else basetype.__name__,
                basetype,
                type(field).__name__, type(field), field
            )
            ok = False
      for field_name in self.field_names:
        if field_name not in fields_spec:
          w(
              "field %r is present but is not defined in self.PACKET_FIELDS: %r",
              field_name, sorted(fields_spec.keys())
          )
          ok = False
    return ok

  def __getattr__(self, attr):
    ''' Unknown attributes may be field names; return their value.
    '''
    try:
      field = self.field_map[attr]
    except KeyError:
      # pylint: disable=raise-missing-from
      raise AttributeError(
          "%s.%s (field_map has %r)" %
          (type(self), attr, sorted(self.field_map.keys()))
      )
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
        yield field.transcribe()

  # pylint: disable=too-many-arguments
  def add_from_bytes(
      self, field_name, bs, factory, offset=0, length=None, **kw
  ):
    ''' Add a new `PacketField` named `field_name` parsed from the
        bytes `bs` using `factory`. Updates the internal field
        records.
        Returns the new `PacketField`'s .value and the new parse
        offset within `bs`.

        Parameters:
        * `field_name`: the name for the new field; it must be new.
        * `bs`: the bytes containing the field data; a `CornuCopyBuffer`
          is made from this for the parse.
        * `factory`: a factory for parsing the field data, returning
          a `PacketField`. If `factory` is a class then its .from_buffer
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
    ''' Add a new `PacketField` named `field_name` parsed from `bfr` using `factory`.
        Updates the internal field records.
        Returns the new `PacketField`'s .value.

        Parameters:
        * `field_name`: the name for the new field; it must be new.
        * `bfr`: a `CornuCopyBuffer` from which to parse the field data.
        * `factory`: a factory for parsing the field data, returning
          a `PacketField`. If `factory` is a class then its .from_buffer
          method is called, otherwise the factory is called directly.

        Additional keyword arguments are passed to the internal
        factory call.

        For convenience, `factory` may also be a str in which case
        it is taken to be a single struct format specifier.
        Alternatively, `factory` may be an integer in which case
        it is taken to be a fixed length bytes field.
    '''
    assert isinstance(field_name,
                      str), "field_name not a str: %r" % (field_name,)
    assert isinstance(bfr, CornuCopyBuffer
                      ), "bfr not a CornuCopyBuffer: %r" % (bfr,)
    if isinstance(factory, str):
      from_buffer = BinarySingleStruct('BinarySingleStruct', factory).parse
    elif isinstance(factory, int):
      from_buffer = fixed_bytes_field(factory).parse
    elif issubclass(factory, AbstractBinary):
      from_buffer = factory.parse
    elif isinstance(factory, type):
      from_buffer = factory.from_buffer
    else:
      from_buffer = factory
    field = from_buffer(bfr, **kw)
    self.add_field(field_name, field)
    return field.value

  def add_from_value(self, field_name, value, transcribe_value_fn):
    ''' Add a new field named `field_name` with `.value=value`.
        Return the new field.
    '''

    class ValueField(PacketField):
      ''' A `PacketField` with a single `.value` and no parser.
      '''

      # pylint: disable=arguments-differ
      @staticmethod
      def transcribe_value(value):
        ''' Transcribe the value as bytes.
        '''
        return transcribe_value_fn(value)

    field = ValueField(value)
    self.add_field(field_name, field)
    return field

  def add_field(self, field_name, field):
    ''' Add a new `PacketField` `field` named `field_name`.
    '''
    if field_name in self.field_map:
      raise ValueError("field %r already in field_map" % (field_name,))
    self.field_names.append(field_name)
    self.fields.append(field)
    self.field_map[field_name] = field
    return None if field is None else field.value

  def remove_field(self, field_name):
    ''' Remove the field `field_name`. Return the field.
    '''
    field = self.field_map.pop(field_name)
    self.field_names.remove(field_name)
    return field

  def pop_field(self):
    ''' Remove the last field, return `(field_name,field)`.
    '''
    field_name = self.field_names[-1]
    field = self.remove_field(field_name)
    return field_name, field

  def add_deferred_field(self, attr_name, bfr, length):
    ''' Store the unparsed data for attribute `attr_name`
        comprising the next `length` bytes from `bfr`.
    '''
    setattr(self, '_' + attr_name + '__raw_data', bfr.take(length))

  @staticmethod
  def deferred_field(from_buffer):
    ''' A decorator for a field property.

        Usage:

            @deferred_field
            def (self, bfr):
                ... parse value from `bfr`, return value
    '''
    attr_name = from_buffer.__name__
    _attr_name = '_' + attr_name

    def field_property(self):
      ''' Boilerplate for the property: test for parsed value, parse
          from raw data if not yet present.
      '''
      attr_value = getattr(self, _attr_name, None)
      if attr_value is None:
        raw_data = getattr(self, _attr_name + '__raw_data')
        attr_value = from_buffer(self, CornuCopyBuffer.from_bytes(raw_data))
        setattr(self, _attr_name, attr_value)
      return attr_value

    return property(field_property)

deferred_field = Packet.deferred_field
