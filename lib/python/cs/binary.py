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

    All the classes derived from the above inherit all the methods
    of `BinaryMixin`.
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
'''

from abc import ABC, abstractmethod, abstractclassmethod
from collections import namedtuple
from struct import Struct  # pylint: disable=no-name-in-module
import sys
from types import SimpleNamespace
from typing import List, Union

from cs.buffer import CornuCopyBuffer
from cs.deco import OBSOLETE, promote, strable
from cs.gimmicks import warning, debug
from cs.lex import cropped, cropped_repr, typed_str
from cs.pfx import Pfx, pfx, pfx_method, pfx_call
from cs.seq import Seq

__version__ = '20240201'

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
      # pylint: disable=unnecessary-lambda-assignment
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

      Naming conventions:
      - `parse`* methods parse a single instance from a buffer
      - `scan`* methods are generators yielding successive instances from a buffer
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
    with Pfx("%s.scan", cls.__name__):
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
          raise ValueError(
              "count must be >=0 if specified, got: %r" % (count,)
          )
        min_count = max_count = count
      scanned = 0
      while (max_count is None or scanned < max_count) and not bfr.at_eof():
        pre_offset = bfr.offset
        obj = cls.parse(bfr)
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
  @OBSOLETE(suggestion="BinaryMixin.scan")
  def scan_with_offsets(cls, bfr, count=None, min_count=None, max_count=None):
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
  @OBSOLETE(suggestion="BinaryMixin.scan")
  def scan_fspath(cls, fspath: str, *, with_offsets=False, **kw):
    ''' Open the file with filesystenm path `fspath` for read
        and yield from `self.scan(..,**kw)` or
        `self.scan_with_offsets(..,**kw)` according to the
        `with_offsets` parameter.

        *Deprecated; please just call `scan` with a filesystem pathname.

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

  @classmethod
  def load(cls, f):
    ''' Load an instance from the file `f`
        which may be a filename or an open file as for `BinaryMixin.scan`.
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

class AbstractBinary(ABC, BinaryMixin):
  ''' Abstract class for all `Binary`* implementations,
      specifying the `parse` and `transcribe` methods
      and providing the methods from `BinaryMixin`.
  '''

  # pylint: disable=deprecated-decorator
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

      To constrain the arguments passed to `__init__`,
      define an `__init__` which accepts specific keyword arguments
      and pass through to `super().__init__()`. Example:

          def __init__(self, *, field1=None, field2):
              """ Accept only `field1` (optional)
                  and `field2` (mandatory).
              """
              super().__init__(field1=field1, field2=field2)
  '''

  def __str__(self, attr_names=None, attr_choose=None):
    if attr_names is None:
      attr_names = sorted(self.__dict__.keys())
    if attr_choose is None:
      # pylint: disable=unnecessary-lambda-assignment
      attr_choose = lambda attr: not attr.startswith('_')
    return "%s(%s)" % (
        type(self).__name__, ','.join(
            (
                "%s=%s" % (attr, cropped_repr(getattr(self, attr, None)))
                for attr in attr_names
                if attr_choose(attr)
            )
        )
    )

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

@pfx
def BinaryMultiStruct(
    class_name: str, struct_format: str, field_names: Union[str, List[str]]
):
  ''' A class factory for `AbstractBinary` `namedtuple` subclasses
      built around complex `struct` formats.

      Parameters:
      * `class_name`: name for the generated class
      * `struct_format`: the `struct` format string
      * `field_names`: field name list,
        a space separated string or an interable of strings

      Example:

          # an "access point" record from the .ap file
          Enigma2APInfo = BinaryMultiStruct('Enigma2APInfo', '>QQ', 'pts offset')

          # a "cut" record from the .cuts file
          Enigma2Cut = BinaryMultiStruct('Enigma2Cut', '>QL', 'pts type')
  '''
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
  struct_class.field_names = field_names
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
  def parse_value(bfr):
    ''' Parse an extensible byte serialised unsigned `int` from a buffer.

        Continuation octets have their high bit set.
        The value is big-endian.

        This is the go for reading from a stream. If you already have
        a bare bytes instance then the `.decode_bytes` static method
        is probably most efficient;
        there is of course the usual `BinaryMixin.parse_bytes`
        but that constructs a buffer to obtain the individual bytes.
    '''
    n = 0
    b = 0x80
    while b & 0x80:
      b = bfr.byte0()
      n = (n << 7) | (b & 0x7f)
    return n

  @staticmethod
  def decode_bytes(data, offset=0):
    ''' Decode an extensible byte serialised unsigned `int` from `data` at `offset`.
        Return value and new offset.

        Continuation octets have their high bit set.
        The octets are big-endian.

        If you just have a `bytes` instance, this is the go. If you're
        reading from a stream you're better off with `parse` or `parse_value`.

        Examples:

            >>> BSUInt.decode_bytes(b'\\0')
            (0, 1)

        Note: there is of course the usual `BinaryMixin.parse_bytes`
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

  # pylint: disable=arguments-renamed
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

  # pylint: disable=arguments-differ,arguments-renamed
  @staticmethod
  def transcribe_value(value: str, encoding='utf-8'):
    ''' Transcribe a string.
    '''
    payload = value.encode(encoding)
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

  # pylint: disable=arguments-renamed
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

  def _s(self, *, crop_length=64, choose_name=None):
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
            BMV(n1=17, n2=34, n3=119, nd=nd_1_short__bs(short=33154, bs=b'zyxw'), data1=b'AB', data2=b'DEFG')
            >>> bmv.nd  #doctest: +ELLIPSIS
            nd_1_short__bs(short=33154, bs=b'zyxw')
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
