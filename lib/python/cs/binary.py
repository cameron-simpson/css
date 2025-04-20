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

    See `cs.iso14496` for an ISO 14496 (eg MPEG4) parser
    built using this module.

    Note: this module requires Python 3.6+ because various default
    behaviours rely on `dict`s preserving their insert order.

    Terminology used below:
    * buffer:
      an instance of `cs.buffer.CornuCopyBuffer`,
      which manages an iterable of bytes-like values
      and has various useful methods;
      it also has a few factory methods to make one from a variety of sources
      such as bytes, iterables, binary files, `mmap`ped files,
      TCP data streams, etc.
    * chunk:
      a piece of binary data obeying the buffer protocol,
      almost always a `bytes` instance or a `memoryview`,
      but in principle also things like `bytearray`.

    There are 5 main classes on which an implementor should base their data structures:
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
      with no variation
    * `SimpleBinary`: a base class for subclasses
      with custom `.parse` and `.transcribe` methods,
      for structures with variable fields

    Any the classes derived from the above inherit all the methods
    of `AbstractBinary`.
    Amongst other things, this means that the binary transcription
    can be had simply from `bytes(instance)`,
    although there are more transcription methods provided
    for when greater flexibility is desired.
    It also means that all classes have `parse`* and `scan`* methods
    for parsing binary data streams.

    You can also instantiate objects directly;
    there's no requirement for the source information to be binary.

    There are several presupplied subclasses for common basic types
    such as `UInt32BE` (an unsigned 32 bit big endian integer).

    # Two Examples

    Here are two examples drawn from `cs.iso14496`.
    Like all the `Binary*` subclasses, parsing an instance from a
    stream can be done like this:

        m9 = Matrix9Long.parse(bfr)
        print("m9.v3", m9.v3)

        edit_list = ELSTBoxBody.parse(bfr)
        print("edit list: entry_count =", edit_list.entry_count)

    and writing its binary form to a file like this:

        f.write(bytes(m9))
        f.write(bytes(edit_list))

    A simple `struct` style definitiion for 9 longs:

        Matrix9Long = BinaryMultiStruct(
            'Matrix9Long', '>lllllllll', 'v0 v1 v2 v3 v4 v5 v6 v7 v8'
        )

    An MP4 ELST box:

        class ELSTBoxBody(FullBoxBody):
          """ An 'elst' Edit List FullBoxBody - section 8.6.6.
          """

          V0EditEntry = BinaryMultiStruct(
              'ELSTBoxBody_V0EditEntry', '>Llhh',
              'segment_duration media_time media_rate_integer media_rate_fraction'
          )
          V1EditEntry = BinaryMultiStruct(
              'ELSTBoxBody_V1EditEntry', '>Qqhh',
              'segment_duration media_time media_rate_integer media_rate_fraction'
          )

          @property
          def entry_class(self):
            """ The class representing each entry.
            """
            return self.V1EditEntry if self.version == 1 else self.V0EditEntry

          @property
          def entry_count(self):
            """ The number of entries.
            """
            return len(self.entries)

          def parse_fields(self, bfr: CornuCopyBuffer):
            """ Parse the fields of an `ELSTBoxBody`.
            """
            super().parse_fields(bfr)
            assert self.version in (0, 1)
            entry_count = UInt32BE.parse_value(bfr)
            self.entries = list(self.entry_class.scan(bfr, count=entry_count))

          def transcribe(self):
            """ Transcribe an `ELSTBoxBody`.
            """
            yield super().transcribe()
            yield UInt32BE.transcribe_value(self.entry_count)
            yield map(self.entry_class.transcribe, self.entries)

    A Edit List box comes in a version 0 and version 1 form, differing
    in the field sizes in the edit entries.  This defines two
    flavours of edit entry structure and a property to return the
    suitable class based on the version field.  The `parse_fields()`
    method is called from the base `BoxBody` class' `parse()` method
    to collect addition fields for any box.  For this box it collectsa
    32 bit `entry_count` and then a list of that many edit entries.
    The transcription yields corresponding values.

    # Module Contents
'''

from abc import ABC, abstractmethod, abstractclassmethod
from collections import namedtuple
try:
  from collections.abc import Buffer
except ImportError:
  from typing import ByteString as Buffer
from dataclasses import dataclass, fields
from inspect import signature, Signature
from struct import Struct  # pylint: disable=no-name-in-module
import sys
from types import SimpleNamespace
from typing import (
    Any,
    Callable,
    Iterable,
    List,
    Mapping,
    Optional,
    Tuple,
    Union,
)

from typeguard import typechecked

from cs.buffer import CornuCopyBuffer
from cs.deco import OBSOLETE, decorator, promote, Promotable, strable
from cs.gimmicks import warning, debug
from cs.lex import cropped, cropped_repr, r, stripped_dedent, typed_str
from cs.pfx import Pfx, pfx, pfx_method, pfx_call
from cs.seq import Seq

__version__ = '20240630-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.buffer',
        'cs.deco',
        'cs.gimmicks',
        'cs.lex',
        'cs.pfx',
        'cs.seq',
    ],
    'python_requires':
    '>=3.6',
}

if sys.version_info < (3, 6):
  warning(
      "module %r requires Python 3.6 for reliable field ordering but version_info=%s",
      __name__, sys.version_info
  )

def flatten(chunks) -> Iterable[bytes]:
  ''' Flatten `chunks` into an iterable of `bytes`-like instances.
      None of the `bytes` instances will be empty.

      This exists to allow subclass methods to easily return
      transcribable things (having a `.transcribe` method), ASCII
      strings or bytes or iterables or even `None`, in turn allowing
      them simply to return their superclass' chunks iterators
      directly instead of having to unpack them.

      An example from the `cs.iso14496.METABoxBody` class:

          def transcribe(self):
              yield super().transcribe()
              yield self.theHandler
              yield self.boxes

      The binary classes `flatten` the result of the `.transcribe`
      method to obtain `bytes` instances for the object's binary
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
    if chunks:
      yield chunks.encode('ascii')
  else:
    for subchunk in chunks:
      yield from flatten(subchunk)

@decorator
def parse_offsets(parse, report=False):
  ''' Decorate `parse` (usually an `AbstractBinary` class method)
      to record the buffer starting offset as `self.offset`
      and the buffer post parse offset as `self.end_offset`.
      If the decorator parameter `report` is true,
      call `bfr.report_offset()` with the starting offset at the end of the parse.
  '''

  @trace
  def parse_wrapper(cls, bfr: CornuCopyBuffer, **parse_kw):
    offset = bfr.offset
    self = parse(cls, bfr, **parse_kw)
    self.offset = offset
    self.end_offset = bfr.offset
    if report:
      bfr.report_offset(offset)
    return self

  return parse_wrapper

_pt_spec_seq = Seq()

def pt_spec(pt, name=None, value_type=None, as_repr=None, as_str=None):
  ''' Convert a parse/transcribe specification `pt`
      into an `AbstractBinary` subclass.

      This is largely used to provide flexibility
      in the specifications for the `BinaryMultiValue` factory
      but can also be used as a factory for other simple classes.

      If the specification `pt` is a subclass of `AbstractBinary`
      this is returned directly.

      If `pt` is a (str,str) 2-tuple
      the values are presumed to be a format string for `struct.struct`
      and field names separated by spaces;
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
    # an object with .parse_value and .transcribe_value attributes
    f_parse_value = pt.parse_value
    f_transcribe_value = pt.transcribe_value
  except AttributeError:
    # an int number of bytes
    if isinstance(pt, int):
      # pylint: disable=unnecessary-lambda-assignment
      f_parse_value = lambda bfr: bfr.take(pt)
      f_transcribe_value = lambda value: value
      if value_type is None:
        value_type = Buffer
      elif not issubclass(value_type, Buffer):
        raise TypeError(f'supplied {value_type=} is not a subclass of Buffer')
    else:
      struct_format, struct_fields = pt
      if isinstance(struct_format, str) and isinstance(struct_fields, str):
        # a struct (format,fields) 2-tuple
        # struct format and field names
        if name is None:
          name = f'PTStruct_{next(_pt_spec_seq)}__{struct_fields.replace(" ", "__")}'
        return BinaryMultiStruct(name, struct_format, struct_fields)
      # otherwise a parse/transcribe pair
      f_parse_value, f_transcribe_value = pt

  if value_type is None:
    sig = signature(f_parse_value)
    value_type = sig.return_annotation
    if value_type is Signature.empty:
      raise ValueError(f'no return type annotation on {f_parse_value=}')

  class PTValue(BinarySingleValue, value_type=value_type):  # pylint: disable=used-before-assignment
    ''' A `BinarySingleValue` subclass
        made from `f_parse_value` and `f_transcribe_value`.
    '''

    if as_str:
      __str__ = as_str
    if as_repr:
      __repr__ = as_repr

    @staticmethod
    def parse_value(bfr: CornuCopyBuffer) -> value_type:
      ''' Parse value from buffer.
      '''
      return f_parse_value(bfr)

    @staticmethod
    def transcribe_value(value):
      ''' Transcribe the value.
      '''
      return f_transcribe_value(value)

  PTValue.__name__ = name or f'PTValue_{next(_pt_spec_seq)}'
  PTValue.__doc__ = stripped_dedent(
      f'''{name}, a `BinarySingleValue` subclass
          made from {f_parse_value=} and {f_transcribe_value=}.
      '''
  )
  return PTValue

class bs(bytes):
  ''' A `bytes subclass with a compact `repr()`.
  '''

  def __repr__(self):
    return cropped(super().__repr__())

  def join(self, chunks):
    ''' `bytes.join` but returning a `bs`.
    '''
    return self.__class__(super().join(chunks))

  @classmethod
  def promote(cls, obj):
    ''' Promote `bytes` or `memoryview` to a `bs`.
    '''
    if isinstance(obj, cls):
      return obj
    if isinstance(obj, (bytes, memoryview)):
      return cls(obj)
    raise TypeError(f'{cls.__name__}.promote({obj.__class__}): cannot promote')

class AbstractBinary(Promotable, ABC):
  ''' Abstract class for all `Binary`* implementations,
      specifying the abstract `parse` and `transcribe` methods
      and providing various helper methods.

      Naming conventions:
      - `parse`* methods parse a single instance from a buffer
      - `scan`* methods are generators yielding successive instances from a buffer
  '''

  def __str__(self, attr_names=None, attr_choose=None, str_func=None):
    ''' The string summary of this object.
        If called explicitly rather than via `str()` the following
        optional parametsrs may be supplied:
        * `attr_names`: an iterable of `str` naming the attributes to include;
          the default if the keys of `self.__dict__`
        * `attr_choose`: a callable to select amongst the attribute names names;
          the default is to choose names which do not start with an underscore
        * `str_func`: a callable returning the string form of an attribute value;
          the default returns `cropped_repr(v)` where `v` is the value's `.value`
          attribute for single value objects otherwise the object itself
    '''
    if attr_names is None:
      attr_names = self._field_names
    if attr_choose is None:
      # pylint: disable=unnecessary-lambda-assignment
      attr_choose = lambda attr: not attr.startswith('_')
    elif attr_choose is True:
      attr_choose = lambda: True
    if str_func is None:
      str_func = lambda obj: (
          cropped_repr(obj.value)
          if is_single_value(obj) else cropped_repr(obj)
      )
    attr_values = [
        (attr, getattr(self, attr, None))
        for attr in attr_names
        if attr_choose(attr)
    ]
    return "%s(%s)" % (
        type(self).__name__, ','.join(
            ("%s=%s" % (attr, str_func(obj)) for attr, obj in attr_values)
        )
    )

  def __repr__(self):
    return "%s(%s)" % (
        self.__class__.__name__, ",".join(
            "%s=%s:%s" % (attr, type(value).__name__, cropped_repr(value))
            for attr, value in self.__dict__.items()
        )
    )

  @property
  def _field_names(self):
    return self.__dict__.keys()

  # pylint: disable=deprecated-decorator
  @abstractclassmethod
  def parse(cls, bfr: CornuCopyBuffer):
    ''' Parse an instance of `cls` from the buffer `bfr`.
    '''
    raise NotImplementedError("parse")

  @abstractmethod
  def transcribe(self):
    ''' Return or yield `bytes`, ASCII string, `None` or iterables
        comprising the binary form of this instance.

        This aims for maximum convenience when transcribing a data structure.

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

  @pfx_method
  def self_check(self):
    ''' Internal self check. Returns `True` if passed.

        If the structure has a `FIELD_TYPES` attribute, normally a
        class attribute, then check the fields against it.

        The `FIELD_TYPES` attribute is a mapping of `field_name` to
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

        Here is the `cs.iso14496` `Box.FIELD_TYPES` definition as an example:

            FIELD_TYPES = {
                'header': BoxHeader,
                'body': BoxBody,
                'unparsed': list,
                'offset': int,
                'unparsed_offset': int,
                'end_offset': int,
            }

        Note that `length` includes some nonstructure types,
        and that it is written as a tuple of `(True,types)` because
        it has more than one acceptable type.
    '''
    ok = True
    try:
      fields_spec = self.FIELD_TYPES
    except AttributeError:
      warning("no FIELD_TYPES")
      ##ok = False
    else:
      # check fields against self.FIELD_TYPES
      # TODO: call self_check on members with a .self_check() method
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
              warning(
                  "missing required field %s.%s: __dict__=%s",
                  type(self).__name__, field_name, cropped_repr(self.__dict__)
              )
              ok = False
          else:
            if not isinstance(field, basetype):
              warning(
                  "should be an instance of %s:%s but is %s", (
                      'tuple'
                      if isinstance(basetype, tuple) else basetype.__name__
                  ), basetype, typed_str(field, max_length=64)
              )
              ok = False
    return ok

  def __bytes__(self):
    ''' The binary transcription as a single `bytes` object.
    '''
    return b''.join(flatten(self.transcribe()))

  def transcribed_length(self):
    ''' Compute the length by running a transcription and measuring it.
    '''
    return sum(map(len, flatten(self.transcribe())))

  # also available as len() by default
  __len__ = transcribed_length

  @classmethod
  @promote
  def scan(
      cls,
      bfr: CornuCopyBuffer,
      count=None,
      *,
      min_count=None,
      max_count=None,
      with_offsets=False,
      **parse_kw,
  ):
    ''' Function to scan the buffer `bfr` for repeated instances of `cls`
        until end of input and yield them.

        Parameters:
        * `bfr`: the buffer to scan, or any object suitable for `CornuCopyBuffer.promote`
        * `count`: the required number of instances to scan,
          equivalent to setting `min_count=count` and `max_count=count`
        * `min_count`: the minimum number of instances to scan
        * `max_count`: the maximum number of instances to scan
        * `with_offsets`: optional flag, default `False`;
          if true yield `(pre_offset,obj,post_offset)`, otherwise just `obj`
        It is in error to specify both `count` and one of `min_count` or `max_count`.

        Other keyword arguments are passed to `self.parse()`.

        Scanning stops after `max_count` instances (if specified).
        If fewer than `min_count` instances (if specified) are scanned
        a warning is issued.
        This is to accomodate nonconformant streams
        without raising exceptions.
        Callers wanting to validate `max_count` may want to probe `bfr.at_eof()`
        after return.
        Callers not wanting a warning over `min_count` should not specify it,
        and instead check the number of instances returned themselves.
    '''
    if count is None:
      if min_count is None:
        min_count = 0
      else:
        if min_count < 0:
          raise ValueError(
              "min_count must be >=0 if specified, got: %r" % (min_count,)
          )
      if max_count is not None:
        if max_count < 0:
          raise ValueError(
              "max_count must be >=0 if specified, got: %r" % (max_count,)
          )
        if max_count < min_count:
          raise ValueError(
              "max_count must be >= min_count, got: min_count=%r, max_count=%rr"
              % (min_count, max_count)
          )
    else:
      if min_count is not None or max_count is not None:
        raise ValueError(
            "scan_with_offsets: may not combine count with either min_count or max_count"
        )
      if count < 0:
        raise ValueError("count must be >=0 if specified, got: %r" % (count,))
      min_count = max_count = count
    scanned = 0
    while (max_count is None or scanned < max_count) and not bfr.at_eof():
      pre_offset = bfr.offset
      obj = cls.parse(bfr, **parse_kw)
      if with_offsets:
        yield pre_offset, obj, bfr.offset
      else:
        yield obj
      scanned += 1
    if min_count is not None and scanned < min_count:
      warning(
          "fewer than min_count=%s instances scanned, only %d found",
          min_count, scanned
      )

  @classmethod
  @OBSOLETE(suggestion="AbstractBinary.scan")
  def scan_with_offsets(
      cls, bfr: CornuCopyBuffer, count=None, min_count=None, max_count=None
  ):
    ''' Wrapper for `scan()` which yields `(pre_offset,instance,post_offset)`
        indicating the start and end offsets of the yielded instances.
        All parameters are as for `scan()`.

        *Deprecated; please just call `scan` with the `with_offsets=True` parameter.
    '''
    return cls.scan(
        bfr,
        count=count,
        min_count=min_count,
        max_count=max_count,
        with_offsets=True
    )

  @classmethod
  @OBSOLETE(suggestion="AbstractBinary.scan")
  def scan_fspath(cls, fspath: str, *, with_offsets=False, **kw):
    ''' Open the file with filesystenm path `fspath` for read
        and yield from `self.scan(..,**kw)` or
        `self.scan_with_offsets(..,**kw)` according to the
        `with_offsets` parameter.

        *Deprecated; please just call `scan` with a filesystem pathname.*

        Parameters:
        * `fspath`: the filesystem path of the file to scan
        * `with_offsets`: optional flag, default `False`;
          if true then scan with `scan_with_offsets` instead of
          with `scan`
        Other keyword parameters are passed to `scan` or
        `scan_with_offsets`.
    '''
    with open(fspath, 'rb') as f:
      bfr = CornuCopyBuffer.from_file(f)
      if with_offsets:
        yield from cls.scan_with_offsets(bfr, **kw)
      else:
        yield from cls.scan(bfr, **kw)

  def transcribe_flat(self):
    ''' Return a flat iterable of chunks transcribing this field.
    '''
    return flatten(self.transcribe())

  @classmethod
  def parse_bytes(cls, bs, offset=0, length=None, **parse_kw):
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
    instance = cls.parse(bfr, **parse_kw)
    return instance, bfr.offset

  @classmethod
  def from_bytes(cls, bs, **parse_bytes_kw):
    ''' Factory to parse an instance from the
        bytes `bs` starting at `offset`.
        Returns the new instance.

        Raises `ValueError` if `bs` is not entirely consumed.
        Raises `EOFError` if `bs` has insufficient data.

        Keyword parameters are passed to the `.parse_bytes` method.

        This relies on the `cls.parse` method for the parse.
    '''
    instance, offset = cls.parse_bytes(bs, **parse_bytes_kw)
    if offset < len(bs):
      raise ValueError(
          "unparsed data at offset %d: %r" % (offset, bs[offset:])
      )
    return instance

  @classmethod
  def load(cls, f):
    ''' Load an instance from the file `f`
        which may be a filename or an open file as for `AbstractBinary.scan`.
        Return the instance or `None` if the file is empty.
    '''
    for instance in cls.scan(f):
      return instance
    return None

  @strable(open_func=lambda fspath: pfx_call(open, fspath, 'wb'))
  def save(self, f):
    ''' Save this instance to the file `f`
        which may be a filename or an open file.
        Return the length of the transcription.
    '''
    length = 0
    for bs in self.transcribe_flat():
      while bs:
        written = f.write(bs)
        length += written
        if written < len(bs):
          bs = bs[written:]
        else:
          break
    return length

  def write(self, file, *, flush=False):
    ''' Write this instance to `file`, a file-like object supporting
        `.write(bytes)` and `.flush()`.
        Return the number of bytes written.
    '''
    length = 0
    for bs in self.transcribe_flat():
      bslen = len(bs)
      assert bslen > 0
      offset = 0
      while offset < bslen:
        written = file.write(bs[offset:])
        if written == 0:
          raise RuntimeError(f'wrote 0 bytes to {file}')
        offset += written
      length += bslen
    if flush:
      file.flush()
    return length

def is_single_value(obj):
  ''' Test whether `obj` is a single value binary object.

      This currently recognises `BinarySingleValue` instances
      and tuple based `AbstractBinary` instances of length 1.
  '''
  return True if isinstance(obj, BinarySingleValue) else (
      len(obj.binary_fields) == 1 if isinstance(obj, BaseMultiValueBinary) else
      (
          tuple.__len__(obj) == 1 if isinstance(obj, AbstractBinary)
          and isinstance(obj, tuple) else False
      )
  )

class SimpleBinary(SimpleNamespace, AbstractBinary):
  ''' Abstract binary class based on a `SimpleNamespace`,
      thus providing a nice `__str__` and a keyword based `__init__`.
      Implementors must still define `.parse` and `.transcribe`.

      To constrain the arguments passed to `__init__`,
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

  @classmethod
  def __init_subclass__(cls, *, value_type, **isc_kw):
    if not isinstance(value_type, type) and not get_origin(value_type):
      raise TypeError(
          f'{cls.__name__}.__init_subclass__: value_type={r(value_type)} is not a type'
      )
    super().__init_subclass__(**isc_kw)
    cls.VALUE_TYPE = value_type

  def __init__(self, value):
    if not isinstance(value, self.__class__.VALUE_TYPE):
      raise TypeError(
          f'{self.__class__}: value is not an instance of {self.__class__.VALUE_TYPE}: {r(value)}'
      )
    self.value = value

  def __repr__(self):
    return "%s(%r)" % (
        type(self).__name__, getattr(self, 'value', '<no-value>')
    )

  def __str__(self):
    return str(self.value)

  def __int__(self):
    return int(self.value)

  def __float__(self):
    return float(self.value)

  def __eq__(self, other):
    return self.value == other.value

  @classmethod
  def scan_values(cls, bfr: CornuCopyBuffer, **kw):
    ''' Scan `bfr`, yield values.
    '''
    return map(lambda instance: instance.value, cls.scan(bfr, **kw))

  @classmethod
  def parse(cls, bfr: CornuCopyBuffer):
    ''' Parse an instance from `bfr`.

        Subclasses must implement this method or `parse_value`.
    '''
    value = cls.parse_value(bfr)
    return cls(value)

  @classmethod
  def parse_value(cls, bfr: CornuCopyBuffer):
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

  @classmethod
  def value_from_bytes(cls, bs, **from_bytes_kw):
    ''' Decode an instance from `bs` using `.from_bytes`
        and return the `.value` attribute.
        Keyword arguments are passed to `cls.from_bytes`.
    '''
    instance = cls.from_bytes(bs, **from_bytes_kw)
    return instance.value

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

  @classmethod
  def promote(cls, obj):
    ''' Promote `obj` to an instance of this `BinarySingleValue` subclass.
    '''
    if isinstance(obj, cls):
      return obj
    try:
      # see if there are type specific ._from_blah methods
      return super().promote(obj)
    except TypeError:
      return cls(obj)

class BinaryByteses(
    BinarySingleValue,
    value_type=Union[Buffer, Iterable[Buffer]],
):
  ''' A list of `bytes` parsed directly from the native iteration of the buffer.
      Subclasses are initialised with a `consume=` class parameter
      indicating how many bytes to console on parse; the default
      is `...` meaning to consume the entire remaining buffer, but
      a positive integer can also be supplied to consume exactly
      that many bytes.
  '''

  def __init_subclass__(cls, *, consume=..., **bsv_kw):
    if consume is not Ellipsis:
      if not isinstance(consume, int) or consume < 1:
        raise ValueError(
            f'class {cls.__name__}: consume should be Ellipsis or a positive int, got {r(consume)}'
        )
    super().__init_subclass__(value_type=Buffer, **bsv_kw)
    cls.PARSE_SIZE = consume

  def __repr__(self):
    cls = self.__class__
    return f'{cls.__name__}[cls.PARSE_SIZE]:{self._bufs}'

  @property
  def value(self):
    ''' The internal list of `bytes` instances joined together.
        This is a property and may be expensive to compute for a large list.
    '''
    return b''.join(self._bufs)

  @value.setter
  def value(self, bss: Union[Buffer, Iterable[Buffer]]):
    ''' Set the value from a `bytes` or iterable of `bytes`.
    '''
    if isinstance(bss, Buffer):
      bss = [bss]
    self._bufs = list(bss)

  @classmethod
  def parse(cls, bfr: CornuCopyBuffer):
    ''' Consume `cls.PARSE_SIZE` bytes from the buffer and instantiate a new instance.
    '''
    return cls(bfr.takev(cls.PARSE_SIZE))

  def transcribe(self):
    ''' Transcribe each value.
    '''
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
      bfr: CornuCopyBuffer,
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

def struct_field_types(
    struct_format: str,
    field_names: Union[str, Iterable[str]],
) -> Mapping[str, type]:
  ''' Construct a `dict` mapping field names to struct return types.

      Example:

          >>> struct_field_types('>Hs', 'count text_bs')
          {'count': int, 'text_bs':bytes}
  '''
  if isinstance(field_names, str):
    field_names = field_names.split()
  else:
    field_names = list(field_names)
  fieldmap = {}
  for c in struct_format:
    if not c.isalpha():
      continue
    try:
      fieldtype = {
          'x': None,
          'C': int,
          'b': int,
          'B': int,
          'h': int,
          'H': int,
          'i': int,
          'I': int,
          'l': int,
          'L': int,
          'q': int,
          'Q': int,
          'n': int,
          'N': int,
          'e': float,
          'f': float,
          'd': float,
          's': bytes,
          'p': str,
          'P': int,
      }[c]
    except KeyError:
      raise ValueError(
          f'no type known for struct spec {c=} in {struct_format=}'
      )
    if fieldtype is None:
      # padding
      continue
    try:
      field_name = field_names.pop(0)
    except IndexError:
      raise ValueError(
          f'no field names left at struct spec {c=} in {struct_format=}'
      )
    fieldmap[field_name] = fieldtype
  if field_names:
    raise ValueError(f'unused field names {field_names=} vs {struct_format=}')
  return fieldmap

@pfx
def BinaryStruct(
    class_name: str,
    struct_format: str,
    field_names: Union[str, List[str]] = 'value',
):
  ''' A class factory for `AbstractBinary` `namedtuple` subclasses
      built around potentially complex `struct` formats.

      Parameters:
      * `class_name`: name for the generated class
      * `struct_format`: the `struct` format string
      * `field_names`: optional field name list,
        a space separated string or an interable of strings;
        the default is `'value'`, intended for single field structs

      Example:

          # an "access point" record from the .ap file
          Enigma2APInfo = BinaryMultiStruct('Enigma2APInfo', '>QQ', 'pts offset')

          # a "cut" record from the .cuts file
          Enigma2Cut = BinaryMultiStruct('Enigma2Cut', '>QL', 'pts type')

          >>> UInt16BE = BinaryStruct('UInt16BE', '>H')
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
  if isinstance(field_names, str):
    field_names = field_names.split()
  elif not isinstance(field_names, tuple):
    field_names = tuple(field_names)
  if len(set(field_names)) != len(field_names):
    raise ValueError(f'repeated name in {field_names=}')
  struct = Struct(struct_format)
  fieldmap = struct_field_types(struct_format, field_names)
  for field_name in field_names:
    with Pfx(field_name):
      if (field_name in ('length', 'struct', 'format')
          or hasattr(AbstractBinary, field_name)):
        raise ValueError(
            f'field name conflicts with AbstractBinary.{field_name}'
        )

  # pylint: disable=function-redefined
  class struct_class(
      namedtuple(class_name or "StructSubValues", field_names),
      AbstractBinary,
      Promotable,
  ):
    ''' A struct field for a complex struct format.
    '''

    _struct = struct
    _field_names = tuple(field_names)

    @classmethod
    @promote
    def parse(cls, bfr: CornuCopyBuffer):
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
      # structs with a single field

      def __str__(self):
        return str(self[0])

      ##def __repr__(self):
      ##  return repr(self[0])

      def __int__(self):
        return int(self[0])

      def __float__(self):
        return float(self[0])

      if field_names[0] != 'value':

        @property
        def value(self):
          ''' Alias `.value` as the first (and only) struct value.
          '''
          return self[0]

      @classmethod
      def parse_value(cls, bfr: CornuCopyBuffer) -> fieldmap[field_names[0]]:
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

      @classmethod
      def promote(cls, obj):
        ''' Promote a single value to an instance of `cls`.
        '''
        if isinstance(obj, cls):
          return obj
        return cls(**{field_names[0]: obj})
    else:

      @classmethod
      def promote(cls, obj):
        ''' Promote an iterable of field values to an instance of `cls`.
        '''
        if isinstance(obj, cls):
          return obj
        return cls(
            **{
                field_name: item
                for field_name, item in zip(field_names, obj)
            }
        )

  assert isinstance(struct_class, type)
  struct_class.__name__ = class_name
  struct_class.__doc__ = (
      ''' An `AbstractBinary` `namedtuple` which parses and transcribes
          the struct format `%r` and presents the attributes %r.
      ''' % (struct_format, field_names)
  )
  struct_class.struct = struct
  struct_class.format = struct_format
  struct_class.length = struct.size
  struct_class.field_names = field_names
  return struct_class

BinaryMultiStruct = OBSOLETE(BinaryStruct)
BinarySingleStruct = OBSOLETE(BinaryStruct)

# various common values
UInt8 = BinaryStruct('UInt8', 'B')
UInt8.TEST_CASES = (
    (0, b'\0'),
    (65, b'A'),
)
Int16BE = BinaryStruct('Int16BE', '>h')
Int16BE.TEST_CASES = (
    (0, b'\0\0'),
    (1, b'\0\1'),
    (32767, b'\x7f\xff'),
    (-1, b'\xff\xff'),
    (-32768, b'\x80\x00'),
)
Int16LE = BinaryStruct('Int16LE', '<h')
Int16LE.TEST_CASES = (
    (0, b'\0\0'),
    (1, b'\1\0'),
    (32767, b'\xff\x7f'),
    (-1, b'\xff\xff'),
    (-32768, b'\x00\x80'),
)
Int32BE = BinaryStruct('Int32BE', '>l')
Int32BE.TEST_CASES = (
    (0, b'\0\0\0\0'),
    (1, b'\0\0\0\1'),
    (2147483647, b'\x7f\xff\xff\xff'),
    (-1, b'\xff\xff\xff\xff'),
    (-2147483648, b'\x80\x00\x00\x00'),
)
Int32LE = BinaryStruct('Int32LE', '<l')
Int32LE.TEST_CASES = (
    (0, b'\0\0\0\0'),
    (1, b'\1\0\0\0'),
    (2147483647, b'\xff\xff\xff\x7f'),
    (-1, b'\xff\xff\xff\xff'),
    (-2147483648, b'\x00\x00\x00\x80'),
)
UInt16BE = BinaryStruct('UInt16BE', '>H')
UInt16BE.TEST_CASES = (
    (0, b'\0\0'),
    (1, b'\0\1'),
    (32767, b'\x7f\xff'),
    (32768, b'\x80\x00'),
    (65535, b'\xff\xff'),
)
UInt16LE = BinaryStruct('UInt16LE', '<H')
UInt16LE.TEST_CASES = (
    (0, b'\0\0'),
    (1, b'\1\0'),
    (32767, b'\xff\x7f'),
    (32768, b'\x00\x80'),
    (65535, b'\xff\xff'),
)
UInt32BE = BinaryStruct('UInt32BE', '>L')
UInt32BE.TEST_CASES = (
    (0, b'\0\0\0\0'),
    (1, b'\0\0\0\1'),
    (2147483647, b'\x7f\xff\xff\xff'),
    (2147483648, b'\x80\x00\x00\x00'),
    (4294967294, b'\xff\xff\xff\xfe'),
    (4294967295, b'\xff\xff\xff\xff'),
)
UInt32LE = BinaryStruct('UInt32LE', '<L')
UInt32LE.TEST_CASES = (
    (0, b'\0\0\0\0'),
    (1, b'\1\0\0\0'),
    (2147483647, b'\xff\xff\xff\x7f'),
    (2147483648, b'\x00\x00\x00\x80'),
    (4294967294, b'\xfe\xff\xff\xff'),
    (4294967295, b'\xff\xff\xff\xff'),
)
UInt64BE = BinaryStruct('UInt64BE', '>Q')
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
UInt64LE = BinaryStruct('UInt64LE', '<Q')
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
Float64BE = BinaryStruct('Float64BE', '>d')
Float64BE.TEST_CASES = (
    (0.0, b'\0\0\0\0\0\0\0\0'),
    (1.0, b'?\xf0\x00\x00\x00\x00\x00\x00'),
)
Float64LE = BinaryStruct('Float64LE', '<d')
Float64LE.TEST_CASES = (
    (0.0, b'\0\0\0\0\0\0\0\0'),
    (1.0, b'\x00\x00\x00\x00\x00\x00\xf0?'),
)

class BSUInt(BinarySingleValue, value_type=int):
  ''' A binary serialised unsigned `int`.

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
  def parse_value(bfr: CornuCopyBuffer) -> int:
    ''' Parse an extensible byte serialised unsigned `int` from a buffer.

        Continuation octets have their high bit set.
        The value is big-endian.

        This is the go for reading from a stream. If you already have
        a bare bytes instance then the `.decode_bytes` static method
        is probably most efficient;
        there is of course the usual `AbstractBinary.parse_bytes`
        but that constructs a buffer to obtain the individual bytes.
    '''
    n = 0
    b = 0x80
    while b & 0x80:
      b = bfr.byte0()
      n = (n << 7) | (b & 0x7f)
    return n

  @staticmethod
  def decode_bytes(data, offset=0) -> Tuple[int, int]:
    ''' Decode an extensible byte serialised unsigned `int` from `data` at `offset`.
        Return value and new offset.

        Continuation octets have their high bit set.
        The octets are big-endian.

        If you just have a `bytes` instance, this is the go. If you're
        reading from a stream you're better off with `parse` or `parse_value`.

        Examples:

            >>> BSUInt.decode_bytes(b'\\0')
            (0, 1)

        Note: there is of course the usual `AbstractBinary.parse_bytes`
        but that constructs a buffer to obtain the individual bytes;
        this static method will be more performant
        if all you are doing is reading this serialisation
        and do not already have a buffer.
    '''
    n = 0
    b = 0x80
    while b & 0x80:
      b = data[offset]
      offset += 1
      n = (n << 7) | (b & 0x7f)
    return n, offset

  # pylint: disable=arguments-renamed
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

class BSData(BinarySingleValue, value_type=Buffer):
  ''' A run length encoded data chunk, with the length encoded as a `BSUInt`.
  '''

  TEST_CASES = (
      (b'', b'\x00'),
      (b'A', b'\x01A'),
  )

  @property
  def data(self) -> bytes:
    ''' An alias for the `.value` attribute.
    '''
    return self.value

  @property
  def data_offset(self) -> int:
    ''' The length of the length indicator,
        useful for computing the location of the raw data.
    '''
    return len(BSUInt(len(self.value)))

  @classmethod
  def parse_value(cls, bfr: CornuCopyBuffer) -> bytes:
    ''' Parse the data from `bfr`.
    '''
    data_length = BSUInt.parse_value(bfr)
    data = bfr.take(data_length)
    return data

  # pylint: disable=arguments-renamed
  @staticmethod
  def transcribe_value(data):
    ''' Transcribe the payload length and then the payload.
    '''
    yield BSUInt.transcribe_value(len(data))
    yield data

  @staticmethod
  def data_offset_for(bs) -> int:
    ''' Compute the `data_offset` which would obtain for the bytes `bs`.
    '''
    return BSData(bs).data_offset

  @classmethod
  def promote(cls, obj):
    if isinstance(obj, cls):
      return obj
    if isinstance(obj, Buffer):
      return cls(bytes(obj))
    raise TypeError(f'{cls.__name__}.promote: cannot promote {r(obj)}')

class BSString(BinarySingleValue, value_type=str):
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
  def parse_value(
      bfr: CornuCopyBuffer, encoding='utf-8', errors='strict'
  ) -> str:
    ''' Parse a run length encoded string from `bfr`.
    '''
    strlen = BSUInt.parse_value(bfr)
    bs = bfr.take(strlen)
    if isinstance(bs, memoryview):
      bs = bs.tobytes()
    return bs.decode(encoding=encoding, errors=errors)

  # pylint: disable=arguments-differ,arguments-renamed
  @staticmethod
  def transcribe_value(value: str, encoding='utf-8'):
    ''' Transcribe a string.
    '''
    payload = value.encode(encoding)
    return b''.join((BSUInt.transcribe_value(len(payload)), payload))

class BSSFloat(BinarySingleValue, value_type=float):
  ''' A float transcribed as a `BSString` of `str(float)`.
  '''

  TEST_CASES = (
      (0.0, b'\x030.0'),
      (0.1, b'\x030.1'),
  )

  @classmethod
  def parse_value(cls, bfr: CornuCopyBuffer) -> float:
    ''' Parse a `BSSFloat` from a buffer and return the `float`.
    '''
    s = BSString.parse_value(bfr)
    return float(s)

  # pylint: disable=arguments-renamed
  @staticmethod
  def transcribe_value(f):
    ''' Transcribe a `float`.
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

  def _s(
      self,
      *,
      crop_length: int = 64,
      choose_name: Optional[Callable[[str], bool]] = None,
  ):
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

  __str__ = _s

  ##__repr__ = _s

  def for_json(self) -> Mapping[str, Any]:
    ''' Return a `dict` mapping field names to their values.
    '''
    return {
        field_name: getattr(self, field_name)
        for field_name in self.FIELD_ORDER
    }

  @classmethod
  def parse(cls, bfr: CornuCopyBuffer):
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
    field_values = {}
    for field_name in cls.FIELD_ORDER:
      with Pfx(field_name):
        field = cls.FIELDS[field_name]
        value = field.parse(bfr)
        field_values[field_name] = value
    self = cls(**field_values)
    return self

  def parse_field(
      self, field_name: str, bfr: CornuCopyBuffer, **field_parse_kw
  ):
    ''' Parse `bfr` for the data for `field_name`
        and set the associated attribute.
    '''
    field = self.FIELDS[field_name]
    value = field.parse(bfr, **field_parse_kw)
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

  def transcribe_field(self, field_name: str):
    ''' Return the transcription of `field_name`.
    '''
    field = self.FIELDS[field_name]
    field_value = getattr(self, field_name)
    with Pfx("transcribe %s", field_value):
      return field.transcribe(field_value)

def BinaryMultiValue(class_name, field_map, field_order=None):
  ''' Construct a `SimpleBinary` subclass named `class_name`
      whose fields are specified by the mapping `field_map`.

      The `field_map` is a mapping of field name
      to parse/trasncribe specifications suitable for `pt_spec()`;
      these are all promoted by `pt_spec` into `AbstractBinary` subclasses.

      The `field_order` is an optional ordering of the field names;
      the default comes from the iteration order of `field_map`.

      *Note* for Python <3.6:
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

      If the class has both `parse_value` and `transcribe_value` methods
      then the value itself will be directly stored.
      Otherwise the class it presumed to be more complex subclass
      of `AbstractBinary` and the instance is stored.

      Here is an example exhibiting various ways of defining each field:
      * `n1`: defined with the *`_value` methods of `UInt8`,
        which return or transcribe the `int` from an unsigned 8 bit value;
        this stores a `BinarySingleValue` whose `.value` is an `int`
      * `n2`: defined from the `UInt8` class,
        which parses an unsigned 8 bit value;
        this stores an `UInt8` instance
        (also a `BinarySingleValue` whole `.value` is an `int`)
      * `n3`: like `n2`
      * `data1`: defined with the *`_value` methods of `BSData`,
        which return or transcribe the data `bytes`
        from a run length encoded data chunk;
        this stores a `BinarySingleValue` whose `.value` is a `bytes`
      * `data2`: defined from the `BSData` class
        which parses a run length encoded data chunk;
        this is a `BinarySingleValue` so we store its `bytes` value directly.

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
            BMV(n1=17, n2=34, n3=119, nd=nd(short=33154, bs=b'zyxw'), data1=b'AB', data2=b'DEFG')
            >>> bmv.nd  #doctest: +ELLIPSIS
            nd(short=33154, bs=b'zyxw')
            >>> bmv.nd.bs
            b'zyxw'
            >>> bytes(bmv.nd)
            b'\x81\x82zyxw'
            >>> bmv.data1
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
      if len(field_order) > 1 and sys.version_info < (3, 6):
        raise ValueError(
            f'class {class_name}: Python version {sys.version} < 3.6:'
            ' dicts are not ordered and so we cannot infer the field_order'
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
        f'''A `SimpleBinary` which parses and transcribes
            the fields {field_order!r}.
        '''
    )
    return bmv_class

@decorator
def binclass(cls, kw_only=True):
  r'''Experimental decorator for `dataclass`-like binary classes.

      Example use:

          >>> @binclass
          ... class SomeStruct:
          ...     """A struct containing a count and some flags."""
          ...     count : UInt32BE
          ...     flags : UInt8
          >>> ss = SomeStruct(count=3, flags=0x04)
          >>> ss
          SomeStruct:SomeStruct__dataclass(count=UInt32BE(value=3),flags=UInt8(value=4))
          >>> print(ss)
          SomeStruct(count=3,flags=4)
          >>> bytes(ss)
          b'\x00\x00\x00\x03\x04'
          >>> SomeStruct.promote(b'\x00\x00\x00\x03\x04')
          SomeStruct:SomeStruct__dataclass(count=UInt32BE(value=3),flags=UInt8(value=4))

      Extending an existing `@binclass` class, for example to add
      the body of a structure to some header part:

          >>> @binclass
          ... class HeaderStruct:
          ...     """A header containing a count and some flags."""
          ...     count : UInt32BE
          ...     flags : UInt8
          >>> @binclass
          ... class Packet(HeaderStruct):
          ...     body_text : BSString
          ...     body_data : BSData
          ...     body_longs : BinaryMultiStruct(
          ...         'longs', '>LL', 'long1 long2'
          ...     )
          >>> packet = Packet(
          ...     count=5, flags=0x03,
          ...     body_text="hello",
          ...     body_data=b'xyzabc',
          ...     body_longs=(10,20),
          ... )
          >>> packet
          Packet:Packet__dataclass(count=UInt32BE(value=5),flags=UInt8(value=3),body_text=BSString('hello'),body_data=BSData(b'xyzabc'),body_longs=longs(long1=10, long2=20))
          >>> print(packet)
          Packet(count=5,flags=3,body_text=hello,body_data=b'xyzabc',body_longs=longs(long1=10,long2=20))
          >>> packet.body_data
          b'xyzabc'
  '''

  # collate the annotated class attributes
  attr_annotations = {}
  for supercls in reversed(cls.__mro__):
    for attr, anno_type in getattr(supercls, '__annotations__', {}).items():
      attr_annotations[attr] = anno_type
  if not attr_annotations:
    raise TypeError(f'{cls} has no annotated attributes')

  # create the dataclass
  class dcls:
    pass

  for attr in attr_annotations.keys():
    try:
      attr_value = getattr(cls, attr)
    except AttributeError:
      continue
    setattr(dcls, attr, attr_value)
  dcls.__annotations__ = attr_annotations
  dcls.__doc__ = f'The inner dataclass supporting {cls.__module__}.{cls.__name__}.'
  dcls.__name__ = f'{cls.__name__}__dataclass'
  dcls = dataclass(dcls, kw_only=kw_only)

  # cache a mapping of its fields by name
  # this dict's keys will be in the fields() order
  fieldmap = {field.name: field for field in fields(dcls)}

  # sanity check the filed types - they should be AbstractBinary subclasses
  for field_name, field in fieldmap.items():
    field_type = field.type
    X("field_type = %r", field_type)
    if isinstance(field.type, type):
      if not issubclass(field_type, AbstractBinary):
        raise TypeError(
            f'field {field_name!r}, type {field_type} should be a subclass of AbstractBinary'
        )
    else:
      # a Union of types?
      typing_class = get_origin(field_type)
      if typing_class is Union:
        for element_type in get_args(field_type):
          if element_type is not None and not issubclass(element_type,
                                                         AbstractBinary):
            raise TypeError(
                f'field {field_name!r}, Union element type {element_type} should be a subclass of AbstractBinary'
            )
      elif field.type is Ellipsis or isinstance(field.type, int):
        # a ... or an int indicates a object consuming that many bytes
        class FieldClass(BinaryByteses, consume=field.type):
          pass

        FieldClass.__name__ = field_name
        FieldClass.__doc__ = f'BinaryByteses,consume={field.type})'
        field.type = FieldClass
      else:
        raise TypeError(
            f'field {field_name!r}, type {field_type} is not supported'
        )

  class BinClass(AbstractBinary):

    _dataclass = dcls
    _datafields = fieldmap
    _field_names = tuple(fieldmap)

    # a list of the fields used by AbstractBinary.self_check
    FIELD_TYPES = {
        fieldname: (True, fieldtype)
        for fieldname, fieldtype in fieldmap.items()
    }

    def __init__(self, **dcls_kwargs):
      self.__dict__['_data'] = None  # get dummy entry in early, aids debugging
      cls = self.__class__
      # promote nonbinary values to single binary values
      dcls_kwargs = {
          attr: self.promote_field_value(attr, obj)
          for attr, obj in dcls_kwargs.items()
      }
      dataobj = cls._dataclass(**dcls_kwargs)
      self.__dict__['_data'] = dataobj

    def __str__(self):
      cls = self.__class__
      fieldnames = self._field_names
      if len(fieldnames) == 1:
        return str(getattr(self, fieldnames[0]))
      return "%s(%s)" % (
          self.__class__.__name__,
          ",".join(
              f'{fieldname}={getattr(self,fieldname)}'
              for fieldname in fieldnames
          ),
      )

    def __repr__(self):
      cls = self.__class__
      data = self._data
      fieldnames = self._field_names
      return "%s:%s(%s)" % (
          self.__class__.__name__,
          data.__class__.__name__,
          ",".join(
              f'{fieldname}={getattr(data,fieldname)!r}'
              for fieldname in fieldnames
          ),
      )

    @classmethod
    def parse_field(cls, fieldname: str, bfr: CornuCopyBuffer):
      ''' Parse an instance of the field named `fieldname` from `bfr`.
          Return the field instance.
      '''
      return cls._datafields[fieldname].type.parse(bfr)

    @classmethod
    def promote_field_value(cls, fieldname: str, obj):
      ''' Promote a received `obj` to the appropriate `AbstractBinary` instance.
      '''
      return cls._datafields[fieldname].type.promote(obj)

    def __getattr__(self, attr):
      ''' Return a data field value, the `.value` attribute if it is a single value field.
      '''
      try:
        obj = getattr(self._data, attr)
      except AttributeError as e:
        raise AttributeError(f'{self.__class__.__name__}._data.{attr}') from e
      assert isinstance(
          obj, AbstractBinary
      ), f'{self._data}.{attr}={r(obj)} is not an AbstractBinary'
      if is_single_value(obj):
        return obj.value
      return obj

    def __setattr__(self, attr, value):
      ''' Set a data field from `value`.
      '''
      cls = self.__class__
      try:
        field = cls._datafields[attr]
      except KeyError:
        raise AttributeError(f'{cls.__name__}.{attr}')
      dataobj = self._data
      datavalue = self.promote_field_value(attr, value)
      setattr(dataobj, attr, datavalue)

    @classmethod
    def parse(cls, bfr: CornuCopyBuffer):
      ''' Parse an instance from `bfr`.
      '''
      init_kw = {}
      for fieldname, field in cls._datafields.items():
        with Pfx("%s.parse[%r]", cls.__name__, fieldname):
          init_kw[fieldname] = field.type.parse(bfr)
      return cls(**init_kw)

    def transcribe(self):
      ''' Transcribe this instance.
      '''
      cls = self.__class__
      for fieldname in cls._datafields:
        yield getattr(self._data, fieldname).transcribe()

  return BinClass

def BinaryFixedBytes(class_name: str, length: int):
  ''' Factory for an `AbstractBinary` subclass matching `length` bytes of data.
      The bytes are saved as the attribute `.data`.
  '''
  return BinaryStruct(class_name, f'>{length}s', 'data')

class BinaryUTF8NUL(BinarySingleValue, value_type=str):
  ''' A NUL terminated UTF-8 string.
  '''

  FIELD_TYPES = dict(value=str)

  TEST_CASES = (
      b'123\0',
      ('123', {}, b'123\0'),
  )

  @staticmethod
  def parse_value(bfr: CornuCopyBuffer) -> str:
    ''' Read a NUL terminated UTF-8 string from `bfr`, return field.
    '''
    # probe for the terminating NUL
    bs_length = 1
    missing_nul = False
    while True:
      nul_pos = bs_length - 1
      try:
        bfr.extend(bs_length)
      except EOFError as e:
        debug(
            "BinaryUTF8NUL.parse_value: EOF found looking for NUL terminator: %s",
            e
        )
        missing_nul = True
        break
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
    if not missing_nul:
      nul = bfr.take(1)
      if nul != b'\0':
        raise RuntimeError(
            "after %d bytes, expected NUL, found %r" % (nul_pos, nul)
        )
    return utf8

  # pylint: disable=arguments-renamed
  @staticmethod
  def transcribe_value(s):
    ''' Transcribe the `value` in UTF-8 with a terminating NUL.
    '''
    yield s.encode('utf-8')
    yield b'\0'

class BinaryUTF16NUL(BinarySingleValue, value_type=str):
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
  def __init__(self, value: str, *, encoding: str):
    if encoding not in self.VALID_ENCODINGS:
      raise ValueError(
          'unexpected encoding %r, expected one of %r' %
          (encoding, self.VALID_ENCODINGS)
      )
    self.encoding = encoding
    self.value = value

  # pylint: disable=arguments-differ
  @classmethod
  def parse(cls, bfr: CornuCopyBuffer, *, encoding: str):
    ''' Parse the encoding and value and construct an instance.
    '''
    value = cls.parse_value(bfr, encoding=encoding)
    return cls(value, encoding=encoding)

  # pylint: disable=arguments-differ
  @staticmethod
  def parse_value(bfr: CornuCopyBuffer, *, encoding: str) -> str:
    ''' Read a NUL terminated UTF-16 string from `bfr`, return a `UTF16NULField`.
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
  def transcribe_value(value: str, encoding='utf-16'):
    ''' Transcribe `value` in UTF-16 with a terminating NUL.
    '''
    yield value.encode(encoding)
    yield b'\0\0'
