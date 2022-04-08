#!/usr/bin/env python3

''' Efficient portable machine native columnar storage of time series data
    for double float and signed 64-bit integers.
'''

from array import array, typecodes
from functools import partial
import os
from struct import pack, Struct
from typing import Tuple, Union

from icontract import ensure, require
from typeguard import typechecked

from cs.deco import cachedmethod
from cs.logutils import warning
from cs.pfx import pfx, pfx_call

from cs.x import X

os_open = partial(pfx_call, os.open)

# initial support is singled 64 bit integers and double floats
SUPPORTED_TYPECODES = {
    'q': int,
    'd': float,
}
assert all(typecode in typecodes for typecode in SUPPORTED_TYPECODES)

@typechecked
@require(lambda typecode: typecode in SUPPORTED_TYPECODES)
def deduce_type_big_endianness(typecode: str) -> bool:
  ''' Deduce the native endianness for `typecode`,
      an array/struct typecode character.
  '''
  test_value = SUPPORTED_TYPECODES[typecode](1)
  bs_a = array(typecode, (test_value,)).tobytes()
  bs_s_be = pack('>' + typecode, test_value)
  bs_s_le = pack('<' + typecode, test_value)
  if bs_a == bs_s_be:
    return True
  if bs_a == bs_s_le:
    return False
  raise RuntimeError(
      "cannot infer byte order: array(%r,(1,))=%r, pack(>%s,1)=%r, pack(<%s,1)=%r"
      % (typecode, bs_a, typecode, bs_s_be, typecode, bs_s_le)
  )

NATIVE_BIGENDIANNESS = {
    typecode: deduce_type_big_endianness(typecode)
    for typecode in SUPPORTED_TYPECODES
}
X("NATIVE_BIGENDIANNESS = %r", NATIVE_BIGENDIANNESS)

@require(lambda typecode: typecode in SUPPORTED_TYPECODES)
def struct_format(typecode, big_endian):
  ''' Return a `struct` format string for the supplied `typecode` and big endianness.
  '''
  return ('>' if big_endian else '<') + typecode

class TimeSeries:
  ''' A single time series for a single data field.
  '''

  DOTEXT = '.csts'
  MAGIC = b'csts'
  HEADER_LENGTH = 8

  @typechecked
  @require(lambda type_: type_ is int or type_ is float)
  @require(lambda step: step > 0)
  def __init__(
      self, fspath: str, typecode: str, start: Union[int, float],
      step: Union[int, float]
  ):
    ''' Prepare a new time series stored in the file at `fspath`
          containing machine data for the time series values.

          Parameters:
          * `fspath`: the filename of the data file
          * `typecode` the expected `array.typecode` value of the data
          * `start`: the UNIX epoch time for the first datum
          * `step`: the increment between data times
      '''
    self.fspath = fspath
    self.typecode = typecode
    self.start = start
    self.step = step
    # read the data file header
    try:
      with os_open(fspath, 'rb') as tsf:
        header_bs = tsf.read(self.HEADER_LENGTH)
      if len(header_bs) != len(self.HEADER_LENGTH):
        raise ValueError(
            "file header is the wrong length, expected %d, got %d" %
            (self.HEADER_LENGTH, len(header_bs))
        )
    except FileNotFoundError:
      # file does not exist, use our native ordering
      self.big_endian = NATIVE_BIGENDIANNESS[typecode]
      self._byte_swapped = False
    else:
      file_typecode, file_big_endian = self.parse_header(header_bs)
      if typecode != file_typecode:
        raise ValueError(
            "expected typecode %r but the existing file contains typecode %r" %
            (typecode, file_typecode)
        )
      self.big_endian = file_big_endian
    self._itemsize = array(typecode).itemsize
    assert self._itemsize == 8
    self._byte_swapped = self.big_endian != NATIVE_BIGENDIANNESS[typecode]
    self._struct_format = ('>' if self.big_endian else '<') + typecode
    self._struct = Struct(self._struct_format)
    assert self._struct.calcsize() == self._itemsize

  @property
  def header(self):
    ''' The header magic bytes.
      '''
    header_bs = self.MAGIC + self._struct_format.encode('ascii') + b'__'
    assert len(header_bs) == self.HEADER_LENGTH
    return header_bs

  @classmethod
  @pfx
  @typechecked
  @ensure(lambda result: result[0] in SUPPORTED_TYPECODES)
  def parse_header(cls, header_bs: bytes) -> Tuple[str, bool]:
    ''' Parse the file header record.
        Return `(typecode,bigendian)`.
    '''
    if len(header_bs) != cls.HEADER_LENGTH:
      raise ValueError(
          "expected %d bytes, got %d bytes" %
          (cls.HEADER_LENGTH, len(header_bs))
      )
    if not header_bs.startswith(cls.MAGIC):
      raise ValueError(
          "bad leading magic, expected %r, got %r" %
          (cls.MAGIC, header_bs[:len(cls.MAGIC)])
      )
    struct_endian_b, typecode_b, _1, _2 = header_bs[len(cls.MAGIC):]
    struct_endian_marker = chr(struct_endian_b)
    if struct_endian_marker == '>':
      big_endian = True
    elif struct_endian_marker == '<':
      big_endian = False
    else:
      raise ValueError(
          "invalid endian marker, expected '>' or '<', got %r" %
          (struct_endian_marker,)
      )
    typecode = chr(typecode_b)
    if typecode not in SUPPORTED_TYPECODES:
      raise ValueError(
          "unsupported typecode, expected one of %r, got %r" % (
              SUPPORTED_TYPECODES,
              typecode,
          )
      )
    if _1 != b'_' or _2 != b'_':
      warning(
          "ignoring unexpected header trailer, expected %r, got %r" %
          (b'__', _1 + _2)
      )
    return typecode, big_endian

  @property
  def typecode(self):
    ''' The `array.array` type code.
    '''
    type_ = self._type
    if type_ is int:
      return 'q'
    if type_ is float:
      return 'd'
    raise RuntimeError('unsupported type %s' % (type,))

  @property
  @cachedmethod
  def array(self):
    ''' The time series as an `array.array` object.
        This loads the array data from `self.fspath` on first use.
    '''
    assert self._array is None
    ary = array(self.typecode)
    try:
      with os_open(self.fspath, 'rb') as tsf:
        header_bs = tsf.read(self.HEADER_LENGTH)
        assert len(header_bs) == self.HEADERLENGTH
        flen = os.fstat(tsf.fileno()).st_size
        datalen = flen - len(header_bs)
        if flen % self._itemsize != 0:
          warning(
              "data length:%d is not a multiple of item size:%d", datalen,
              self._itemsize
          )
        datum_count = datalen // self._item_size
        ary.from_file(tsf, datum_count)
      if self._byte_swapped:
        ary.byteswap()
    except FileNotFoundError:
      pass
    return ary
