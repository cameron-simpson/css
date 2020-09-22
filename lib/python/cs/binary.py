#!/usr/bin/env python3
#

''' Facilities associated with binary data parsing and transcription.
    The classes in this module support easy parsing of binary data
    structures,
    returning instances with the binary data decoded into attributes
    and capable of transcribing themselves in binary form
    (trivially via `bytes(instance)` and also otherwise).

    See `cs.iso14496` for an ISO 14496 (eg MPEG4) parser
    built using this module.

    Terminology used below:
    * buffer:
      an instance of `cs.buffer.CornuCopyBuffer`,
      which presents an iterable of bytes-like values
      via various useful methods
      and with factory methods to make one from a variety of sources
      such as bytes, iterables, binary files, `mmap`ped files,
      TCP data streams, etc.
    * chunk:
      a piece of binary data obeying the buffer protocol,
      almost always a `bytes` instance or a `memoryview`,
      but in principle also things like `bytearray`.

    Note: this module requires Python 3 and recommends Python 3.6+
    because it uses `abc.ABC`, because a Python 2 bytes object is too
    weak (just a `str`) as also is my `cs.py3.bytes` hack class and
    because the keyword based `Packet` initiialisation benefits from
    keyword argument ordering.

    The functions and classes in this module include the following:

    The two base classes for binary data:
    * `PacketField`: an abstract class for a binary field, with a
      factory method to parse it, a transcription method to transcribe
      it back out in binary form and usually a `.value` attribute
      holding the parsed value.
    * `Packet`: a `PacketField` subclass for parsing multiple
      `PacketField`s into a larger structure with ordered named
      fields.
      The fields themselves may be `Packet`s for complex structures.

    There are several presupplied subclasses for common basic types
    such as `UInt32BE` (an unsigned 32 bit big endian integer).

    Classes built from `struct` format strings:
    * `structtuple`: a factory for `PacketField` subclasses
      for `struct` formats with multiple value fields.
      These classes are `namedtuple` subclasses
      supporting trivial access to the parsed values.
    * `SingleStructBinary`: a factory for making `PacketField` classes
      from `struct` formats with a single value field.

    Here's an example of a `structtuple`:

        # a "cut" record from the .cuts file
        Enigma2Cut = structtuple('Enigma2Cut', '>QL', 'pts type')

    which makes the `Enigma2Cut` class for a `'>QL'` `struct` definition.
    Like every such class, it comes with all the parsing and transcription
    methods from `PacketField`.
    Here's some code to read these from a file:

        yield from Enigma2Cut.parse_file(pathname)

    You don't need to make fields only from binary source data;
    because `PacketField.__init__` takes a post parse value,
    you can also construct `PacketField`s from scratch
    with their values and later transcribe the resulting binary form.

    Each `PacketField` subclass has the following methods:
    * `__bytes__`:
      return an instance as a `bytes` object.
    * `transcribe`: easily return the binary transcription of this field,
      either directly as a chunk (or for convenience, also `None` or
      an ASCII `str`) or by yielding successive binary data.
    * `from_buffer`: a factory to parse this field from a
      `cs.buffer.CornuCopyBuffer`.
    * `from_bytes`: a factory to parse this field from a chunk with
      an optional starting offset; this is a convenience wrapper for
      `from_buffer`.

    That may sound a little arcane, but we also supply:
    * `flatten`: a recursive function to take the return from any
      `transcribe` method and yield chunks, so copying a packet to
      a file or elsewhere can always be done by iterating over
      `flatten(field.transcribe())` or via the convenience
      `field.transcribe_flat()` method which calls `flatten` itself.
    * a `CornuCopyBuffer` is an easy to use wrapper for parsing any
      iterable of chunks, which may come from almost any source.
      It has a bunch of convenient factories including:
      `from_bytes`, make a buffer from a chunk;
      `from_fd`, make a buffer from a file descriptor;
      `from_file`, make a buffer from a file-like object;
      `from_mmap`, make a buffer from a file descriptor using a
      memory map (the `mmap` module) of the file, so that chunks
      can use the file itself as backing store instead of allocating
      and copying memory.
      See the `cs.buffer` module for further detail.

    When parsing a complex structure
    one must choose between subclassing `PacketField` or `Packet`.
    An effective guideline is the degree of substructure.

    A `Packet` is designed for deeper structures;
    all of its attributes are themselves `PacketField`s
    (or `Packet`s, which are `PacketField` subclasses).
    The leaves of this hierarchy will be `PacketField`s,
    whose attributes are ordinary Python types.

    By contrast, a `PacketField`'s attributes are expected to be "flat" values
    i.e. the plain post-parse value such as a `str` or an `int`
    or some other conventional Python type.

    The base case for `PacketField`
    is a single such value, named `.value`,
    and the natural implementation
    is to provide a `.value_from_buffer` method
    which returns the basic single value
    and the corresponding `.transcribe_value` method
    to return or yield its binary form
    (directly or in pieces respectively).

    However,
    you can handle multiple attributes with this class
    by instead implementing:
    * `__init__`: to compose an instance from post-parse values
      (and thus from scratch rather than parsed from existing binary data)
    * `from_buffer`: class method to parse the values
      from a `CornuCopyBuffer` and call the class constructor
    * `transcribe`: to return or yield the binary form of the attributes

    Cameron Simpson <cs@cskk.id.au> 22jul2018
'''

from __future__ import print_function
from abc import ABC, abstractmethod, abstractclassmethod
from collections import namedtuple
from struct import Struct
import sys
from cs.buffer import CornuCopyBuffer

__version__ = '20200229'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.buffer'],
}

# maybe monkey patch this with cs.logutils.warning if importable
def warning(msg, *a, f=None):
  ''' Issue a formatted warning message.
  '''
  if f is None:
    f = sys.stderr
  if a:
    msg = msg % a
  print('WARNING:', msg, file=f)

if sys.version_info[0] < 3:
  warning(
      "module %r requires Python 3 and recommends 3.6, but version_info=%r",
      __name__, sys.version_info
  )
elif sys.version_info[0] == 3 and sys.version_info[1] < 6:
  warning(
      "module %r recommends Python 3.6, but version_info=%r", __name__,
      sys.version_info
  )

def flatten(chunks):
  ''' Flatten `chunks` into an iterable of `bytes` instances.

      This exists to allow subclass methods to easily return ASCII
      strings or bytes or iterables or even `None`, in turn allowing
      them simply to return their superclass' chunks iterators
      directly instead of having to unpack them.
  '''
  if chunks is None:
    pass
  elif isinstance(chunks, (bytes, memoryview)):
    if chunks:
      yield chunks
  elif isinstance(chunks, str):
    yield chunks.encode('ascii')
  else:
    for subchunk in chunks:
      for chunk in flatten(subchunk):
        yield chunk

class BinaryMixin:
  ''' Presupplied helper methods for binary objects.
  '''

  def __bytes__(self):
    ''' The binary transcription as a single `bytes` object.
    '''
    return b''.join(flatten(self.transcribe()))

  def __len__(self):
    ''' Compute the length by running a transcription and measuring it.
    '''
    return sum(len(bs) for bs in flatten(self.transcribe()))

  @classmethod
  def scan_with_offsets(cls, bfr):
    ''' Function to scan the buffer `bfr` for repeated instances of `cls`
        until end of input,
        yielding `(offset,instance,post_offset)` tuples
        where `offset` if the buffer offset where the instance commenced
        and `post_offset` is the buffer offset after the instance.
    '''
    offset = bfr.offset
    while not bfr.at_eof():
      post_offset = bfr.offset
      yield offset, cls.from_buffer(bfr), post_offset
      offset = post_offset

  @classmethod
  def scan(cls, bfr, **kw):
    ''' Function to scan the buffer `bfr` for repeated instances of `cls`
        until end of input,
        yielding instances of `cls`.
    '''
    return map(lambda offset, obj, post_offset: obj, cls.parse_buffer_with_offsets(bfr, **kw))

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
  def from_bytes(cls, bs, offset=0, length=None):
    ''' Factory to parse an instance from the
        bytes `bs` starting at `offset`.
        Returns the new instance.

        Raises `ValueError` if `bs` is not enitrely consumed.
        Raises `EOFError` if `bs` has insufficient data.

        The parameters `offset` and `length` are passed to the
        `CornuCopyBuffer.from_bytes` factory.

        This relies on the `cls.parse` method for the parse.
    '''
    bfr = CornuCopyBuffer.from_bytes(bs, offset=offset, length=length)
    instance = cls.parse(bfr)
    if not bfr.at_eof():
      raise ValueError("unparsed data at offset %d" % (bfr.offset,))
    return instance

  @classmethod
  def from_file(cls, f, offset=0, length=None):
    ''' Factory to parse an instance from the binary file `f`.
        Returns the new instance.

        Raises `ValueError` if `f` is not enitrely consumed.
        Raises `EOFError` if `f` has insufficient data.

        This relies on the `cls.parse` method for the parse.
    '''
    bfr = CornuCopyBuffer.from_file(f)
    instance = cls.parse(bfr)
    if not bfr.at_eof():
      raise ValueError("unparsed data at offset %d" % (bfr.offset,))
    return instance

class AbstractBinary(ABC, BinaryMixin):
  
  @abstractclassmethod
  def parse(cls, bfr):
    ''' Parse an instance of `cls` from the buffrr `bfr`.
    '''
    raise NotImplementedError("parse")

  @abstractmethod
  def transcribe(self):
    ''' Return or yield `bytes`, `None` or iterables
        comprising the binary form of this instance.
    '''
    raise NotImplementedError("transcribe")

class SingleValueBinary(AbstractBinary):
  ''' A representation of a single value as the attribute `.value`.

      Subclasses must implement:
      * `parse` or `value_from_buffer`
      * `transcribe` or `transcribe_value`
  '''

  def __init__(self, value):
    self.value = value

  def __str__(self):
    return str(self.value)

  def __repr__(self):
    return "%s(%r)" % (type(self).__name__, self.value)

  @classmethod
  def parse(cls, bfr):
    value = cls.value_from_buffer(buf)
    return cls(value)

  @classmethod
  def value_from_buffer(cls, bfr):
    return cls.parse(bfr).value

  def transcribe(self):
    return self.transcribe_value(self.value)

  @classmethod
  def transcribe_value(value):
    return cls(value).transcribe()

class PacketField(ABC):
  ''' A record for an individual packet field.

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

  @staticmethod
  def transcribe_value(value, encoding='utf-16'):
    ''' Transcribe `value` in UTF-16 with a terminating NUL.
    '''
    yield value.encode(encoding)
    yield b'\0\0'

class BytesField(PacketField):
  ''' A field of bytes.
  '''

  TEST_CASES = (
      ##(b'1234', {'length': 4}, b'1234'),
  )

  @property
  def value_s(self):
    ''' The repr() of the bytes.
    '''
    bs = self.value
    if not isinstance(bs, bytes):
      bs = bytes(bs)
    return repr(bs)

  @classmethod
  def value_from_buffer(cls, bfr, length=None):
    ''' Parse a `BytesField` of length `length` from `bfr`.
    '''
    if length < 0:
      raise ValueError("length(%d) < 0" % (length,))
    return bfr.take(length)

  def transcribe(self):
    ''' A `BytesField` is its own transcription.
    '''
    assert isinstance(self.value, (bytes, memoryview))
    return self.value

def fixed_bytes_field(length, class_name=None):
  ''' Factory for `BytesField` subclasses built from fixed length byte strings.
  '''
  if length < 1:
    raise ValueError("length(%d) < 1" % (length,))

  class FixedBytesField(BytesField):
    ''' A field whose value is simply a fixed length bytes chunk.
    '''

    @classmethod
    def value_from_buffer(cls, bfr):
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

_SingleStructBinarys = {}

def SingleStructBinary(struct_format, class_name):
  ''' Factory for `SingleValueBinary` subclasses built around a single struct format.

      Parameters:
      * `struct_format`: the struct format string, specifying a
        single struct field
      * `class_name`: the class name for the generated class

      Example:

          >>> UInt16BE = SingleStructBinary('>H', class_name='UInt16BE')
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
  StructField = _SingleStructBinarys.get(key)
  if not StructField:
    struct = Struct(struct_format)

    class StructField(SingleValueBinary):
      ''' A `PacketField` subclass using a `struct.Struct` for parse and transcribe.
      '''

      length = struct.size

      @classmethod
      def value_from_buffer(cls, bfr):
        ''' Parse a value from the bytes `bs` at `offset`, default 0.
            Return a `PacketField` instance and the new offset.
        '''
        bs = bfr.take(struct.size)
        value, = struct.unpack(bs)
        return value

      @staticmethod
      def transcribe_value(value):
        ''' Transcribe a value back into bytes.
        '''
        return struct.pack(value)

    StructField.__name__ = class_name
    StructField.__doc__ = (
        'A `PacketField` which parses and transcribes the struct format `%r`.'
        % (struct_format,)
    )
    StructField.struct = struct
    StructField.format = struct_format
    _SingleStructBinarys[key] = StructField
  return StructField

SingleStructBinary = SingleStructBinary

# various common values
UInt8 = SingleStructBinary('B', 'UInt8')
UInt8.TEST_CASES = (
    (0, b'\0'),
    (65, b'A'),
)
Int16BE = SingleStructBinary('>h', 'Int16BE')
Int16BE.TEST_CASES = (
    (0, b'\0\0'),
    (1, b'\0\1'),
    (32767, b'\x7f\xff'),
    (-1, b'\xff\xff'),
    (-32768, b'\x80\x00'),
)
Int16LE = SingleStructBinary('<h', 'Int16LE')
Int16LE.TEST_CASES = (
    (0, b'\0\0'),
    (1, b'\1\0'),
    (32767, b'\xff\x7f'),
    (-1, b'\xff\xff'),
    (-32768, b'\x00\x80'),
)
Int32BE = SingleStructBinary('>l', 'Int32BE')
Int32BE.TEST_CASES = (
    (0, b'\0\0\0\0'),
    (1, b'\0\0\0\1'),
    (2147483647, b'\x7f\xff\xff\xff'),
    (-1, b'\xff\xff\xff\xff'),
    (-2147483648, b'\x80\x00\x00\x00'),
)
Int32LE = SingleStructBinary('<l', 'Int32LE')
Int32LE.TEST_CASES = (
    (0, b'\0\0\0\0'),
    (1, b'\1\0\0\0'),
    (2147483647, b'\xff\xff\xff\x7f'),
    (-1, b'\xff\xff\xff\xff'),
    (-2147483648, b'\x00\x00\x00\x80'),
)
UInt16BE = SingleStructBinary('>H', 'UInt16BE')
UInt16BE.TEST_CASES = (
    (0, b'\0\0'),
    (1, b'\0\1'),
    (32767, b'\x7f\xff'),
    (32768, b'\x80\x00'),
    (65535, b'\xff\xff'),
)
UInt16LE = SingleStructBinary('<H', 'UInt16LE')
UInt16LE.TEST_CASES = (
    (0, b'\0\0'),
    (1, b'\1\0'),
    (32767, b'\xff\x7f'),
    (32768, b'\x00\x80'),
    (65535, b'\xff\xff'),
)
UInt32BE = SingleStructBinary('>L', 'UInt32BE')
UInt32BE.TEST_CASES = (
    (0, b'\0\0\0\0'),
    (1, b'\0\0\0\1'),
    (2147483647, b'\x7f\xff\xff\xff'),
    (2147483648, b'\x80\x00\x00\x00'),
    (4294967294, b'\xff\xff\xff\xfe'),
    (4294967295, b'\xff\xff\xff\xff'),
)
UInt32LE = SingleStructBinary('<L', 'UInt32LE')
UInt32LE.TEST_CASES = (
    (0, b'\0\0\0\0'),
    (1, b'\1\0\0\0'),
    (2147483647, b'\xff\xff\xff\x7f'),
    (2147483648, b'\x00\x00\x00\x80'),
    (4294967294, b'\xfe\xff\xff\xff'),
    (4294967295, b'\xff\xff\xff\xff'),
)
UInt64BE = SingleStructBinary('>Q', 'UInt64BE')
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
UInt64LE = SingleStructBinary('<Q', 'UInt64LE')
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
Float64BE = SingleStructBinary('>d', 'Float64BE')
Float64BE.TEST_CASES = (
    (0.0, b'\0\0\0\0\0\0\0\0'),
    (1.0, b'?\xf0\x00\x00\x00\x00\x00\x00'),
)
Float64LE = SingleStructBinary('<d', 'Float64LE')
Float64LE.TEST_CASES = (
    (0.0, b'\0\0\0\0\0\0\0\0'),
    (1.0, b'\x00\x00\x00\x00\x00\x00\xf0?'),
)

class BSUInt(SingleValueBinary):
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
  def value_from_buffer(bfr):
    ''' Parse an extensible byte serialised unsigned int from a buffer.

        Continuation octets have their high bit set.
        The value is big-endian.

        This is the go for reading from a stream. If you already have
        a bare bytes instance then `cs.serialise.get_uint` may be better.
    '''
    n = 0
    b = 0x80
    while b & 0x80:
      bs = bfr.take(1)
      b = bs[0]
      n = (n << 7) | (b & 0x7f)
    return n

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

class BSData(SingleValueBinary):
  ''' A run length encoded data chunk, with the length encoded as a `BSUInt`.
  '''

  TEST_CASES = (
      (b'', b'\x00'),
      (b'A', b'\x01A'),
  )

  @property
  def data(self):
    ''' An alias for the `v.alue` attribute.
    '''
    return self.value

  @property
  def data_offset(self):
    ''' The length of the length indicator,
        useful for computing the location of the raw data.
    '''
    return len(BSUInt(len(self.data)))

  @classmethod
  def value_from_buffer(cls, bfr):
    data_length = BSUInt.value_from_buffer(bfr)
    data = bfr.take(data_length)
    return cls(data)

  def transcribe(self):
    ''' Transcribe the payload length and then the payload.
    '''
    data = self.data
    yield BSUInt.transcribe_value(len(data))
    yield data

  @staticmethod
  def data_offset_for(bs):
    ''' Compute the `data_offset` which would obtain for the bytes `bs`.
    '''
    return BSData(bs).data_offset

class BSString(SingleValueBinary):
  ''' A run length encoded string, with the length encoded as a BSUInt.
  '''

  TEST_CASES = (
      ('', b'\x00'),
      ('A', b'\x01A'),
  )

  def __init__(self, s, encoding='utf-8'):
    super().__init__(s)
    self.encoding = encoding

  @staticmethod
  def value_from_buffer(bfr, encoding='utf-8', errors='strict'):
    ''' Parse a run length encoded string from `bfr`.
    '''
    strlen = BSUInt.value_from_buffer(bfr)
    bs = bfr.take(strlen)
    if isinstance(bs, memoryview):
      bs = bs.tobytes()
    return bs.decode(encoding=encoding, errors=errors)

  @staticmethod
  def transcribe_value(s, encoding='utf-8'):
    ''' Transcribe a string.
    '''
    payload = s.encode(encoding)
    return b''.join((BSUInt.transcribe_value(len(payload)), payload))

class BSSFloat(PacketField):
  ''' A float transcribed as a BSString of str(float).
  '''

  TEST_CASES = (
      (0.0, b'\x030.0'),
      (0.1, b'\x030.1'),
  )

  @classmethod
  def value_from_buffer(cls, bfr, **kw):
    ''' Parse a BSSFloat from a buffer and return the float.
    '''
    s = BSString.value_from_buffer(bfr, **kw)
    return float(s)

  @staticmethod
  def transcribe_value(f):
    ''' Transcribe a float.
    '''
    return BSString.transcribe_value(str(f))

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

  @classmethod
  def from_buffer(cls, bfr):
    ''' ListFields do not know enough to parse a buffer.
    '''
    raise NotImplementedError(
        "%s cannot be parsed directly from a buffer" % (cls,)
    )

  @staticmethod
  def transcribe_value(value):
    ''' Transcribe each item in `value`.
    '''
    for item in value:
      yield item.transcribe()

_multi_struct_fields = {}

def multi_struct_field(struct_format, subvalue_names=None, class_name=None):
  ''' A class factory for `PacketField` subclasses built around complex `struct` formats.

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
  MultiStructField = _SingleStructBinarys.get(key)
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

      if subvalue_names:

        def __str__(self):
          return str(self.value)

      length = struct.size

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
      except Exception as e:
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
      from_buffer = SingleStructBinary(factory, 'SingleStructBinary').parse
    elif isinstance(factory, int):
      from_buffer = fixed_bytes_field(factory).from_buffer
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
