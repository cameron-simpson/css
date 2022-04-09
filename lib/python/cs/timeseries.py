#!/usr/bin/env python3

''' Efficient portable machine native columnar storage of time series data
    for double float and signed 64-bit integers.
'''

from abc import ABC, abstractmethod
from array import array, typecodes  # pylint: disable=no-name-in-module
from contextlib import contextmanager
from functools import partial
import os
from struct import pack, Struct  # pylint: disable=no-name-in-module
import sys
from typing import Optional, Tuple, Union

import arrow
from arrow import Arrow
from icontract import ensure, require
from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.deco import cachedmethod
from cs.logutils import warning
from cs.pfx import pfx, pfx_call
from cs.resources import MultiOpenMixin

from cs.x import X

def main(argv=None):
  ''' Run the command line tool for `TimeSeries` data.
  '''
  return TimeSeriesCommand(argv).run()

pfx_open = partial(pfx_call, open)

# initial support is singled 64 bit integers and double floats
SUPPORTED_TYPECODES = {
    'q': int,
    'd': float,
}
assert all(typecode in typecodes for typecode in SUPPORTED_TYPECODES)

@typechecked
@require(lambda typecode: typecode in SUPPORTED_TYPECODES)
def deduce_type_bigendianness(typecode: str) -> bool:
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
    typecode: deduce_type_bigendianness(typecode)
    for typecode in SUPPORTED_TYPECODES
}
X("NATIVE_BIGENDIANNESS = %r", NATIVE_BIGENDIANNESS)

class TimeSeriesCommand(BaseCommand):
  ''' Command line interface to `TimeSeries` data files.
  '''

@require(lambda typecode: typecode in SUPPORTED_TYPECODES)
def struct_format(typecode, bigendian):
  ''' Return a `struct` format string for the supplied `typecode` and big endianness.
  '''
  return ('>' if bigendian else '<') + typecode

@contextmanager
def array_byteswapped(ary):
  ''' Context manager to byteswap the `array.array` `ary` temporarily.
  '''
  ary.byteswap()
  try:
    yield
  finally:
    ary.byteswap()

class TimeSeries(MultiOpenMixin):
  ''' A single time series for a single data field.

      This provides easy access to a time series data file.
      The instance can be indexed by UNIX time stamp for time based access
      or its `.array` property can be accessed for the raw data.

      Read only users can just instantiate an instance.
      Read/write users should use the instance as a context manager,
      which will automatically rewrite the file with the array data
      on exit.

      The data file itself has a header indicating the file data big endianness
      and datum type (an `array.array` type code).
      This is automatically honoured on load and save.
      Note that the header _does not_ indicate the `start`,`step` time range of the data.
  '''

  DOTEXT = '.csts'
  MAGIC = b'csts'
  HEADER_LENGTH = 8

  @typechecked
  def __init__(
      self,
      fspath: str,
      typecode: str,
      start: Union[int, float],
      step: Union[int, float],
  ):
    ''' Prepare a new time series stored in the file at `fspath`
        containing machine data for the time series values.

        Parameters:
        * `fspath`: the filename of the data file
        * `typecode` the expected `array.typecode` value of the data
        * `start`: the UNIX epoch time for the first datum
        * `step`: the increment between data times
    '''
    if typecode not in SUPPORTED_TYPECODES:
      raise ValueError(
          "expected typecode to be one of %r, got %r" %
          (tuple(SUPPORTED_TYPECODES.keys()), typecode)
      )
    if step <= 0:
      raise ValueError("step should be >0, got %s" % (step,))
    self.fspath = fspath
    self.typecode = typecode
    self.start = start
    self.step = step
    # read the data file header
    try:
      with pfx_open(fspath, 'rb') as tsf:
        header_bs = tsf.read(self.HEADER_LENGTH)
      if len(header_bs) != self.HEADER_LENGTH:
        raise ValueError(
            "file header is the wrong length, expected %d, got %d" %
            (self.HEADER_LENGTH, len(header_bs))
        )
    except FileNotFoundError:
      # file does not exist, use our native ordering
      self.file_bigendian = NATIVE_BIGENDIANNESS[typecode]
    else:
      file_typecode, file_bigendian = self.parse_header(header_bs)
      if typecode != file_typecode:
        raise ValueError(
            "expected typecode %r but the existing file contains typecode %r" %
            (typecode, file_typecode)
        )
      self.file_bigendian = file_bigendian
    self._itemsize = array(typecode).itemsize
    assert self._itemsize == 8
    struct_fmt = self.make_struct_format(typecode, self.file_bigendian)
    self._struct = Struct(struct_fmt)
    assert self._struct.size == self._itemsize

  @contextmanager
  def startup_shutdown(self):
    yield self
    if self._array is not None:
      self.save()

  @staticmethod
  def make_struct_format(typecode, bigendian):
    ''' Make a `struct` format string for the data in a file.
    '''
    return ('>' if bigendian else '<') + typecode

  @property
  def header(self):
    ''' The header magic bytes.
    '''
    return self.make_header(self.typecode, self.file_bigendian)

  @classmethod
  def make_header(cls, typecode, bigendian):
    ''' Construct a header `bytes` object for `typecode` and `bigendian`.
    '''
    header_bs = (
        cls.MAGIC +
        cls.make_struct_format(typecode, bigendian).encode('ascii') + b'__'
    )
    assert len(header_bs) == cls.HEADER_LENGTH
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
      bigendian = True
    elif struct_endian_marker == '<':
      bigendian = False
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
    return typecode, bigendian

  @property
  @cachedmethod
  def array(self):
    ''' The time series as an `array.array` object.
        This loads the array data from `self.fspath` on first use.
    '''
    assert not hasattr(self, '_array')
    try:
      ary = self.load_from(self.fspath, self.typecode)
    except FileNotFoundError:
      # no file, empty array
      ary = array(self.typecode)
    return ary

  def save(self, fspath=None):
    ''' Save the time series to `fspath`, default `self.fspath`.
    '''
    assert self._array is not None, "array not yet loaded, nothing to save"
    if fspath is None:
      fspath = self.fspath
    self.save_to(self.array, fspath, self.file_bigendian)

  @classmethod
  @ensure(
      lambda typecode, result: typecode is None or result.typecode == typecode
  )
  def load_from(cls, fspath, typecode=None):
    ''' Load the data from `fspath`, return an `array.array(typecode)`
        containing the file data.
    '''
    ary = array(typecode)
    with pfx_open(fspath, 'rb') as tsf:
      header_bs = tsf.read(cls.HEADER_LENGTH)
      assert len(header_bs) == cls.HEADER_LENGTH
      h_typecode, h_bigendian = cls.parse_header(header_bs)
      if typecode is not None and h_typecode != typecode:
        raise ValueError(
            "expected typecode %r, file contains typecode %r" %
            (typecode, h_typecode)
        )
      flen = os.fstat(tsf.fileno()).st_size
      datalen = flen - len(header_bs)
      if flen % ary.itemsize != 0:
        warning(
            "data length:%d is not a multiple of item size:%d", datalen,
            ary.itemsize
        )
      datum_count = datalen // ary.itemsize
      ary.fromfile(tsf, datum_count)
      if h_bigendian != NATIVE_BIGENDIANNESS[h_typecode]:
        ary.byteswap()
    return ary

  @classmethod
  @typechecked
  def save_to(cls, ary, fspath: str, bigendian=Optional[bool]):
    ''' Save the array `ary` to `fspath`.
        If `bigendian` is specified, write the data in that endianness.
        The default is to use the native endianness.

        *Warning*:
        if the file endianness is not the native endianness,
        the array will be byte swapped temporarily
        during the file write operation.
        Concurrent users should avoid using the array during this function.
    '''
    native_bigendian = NATIVE_BIGENDIANNESS[ary.typecode]
    if bigendian is None:
      bigendian = native_bigendian
    header_bs = cls.make_header(ary.typecode, bigendian)
    with pfx_open(fspath, 'wb') as tsf:
      tsf.write(header_bs)
      if bigendian != native_bigendian:
        with array_byteswapped(ary):
          ary.tofile(tsf)
      else:
        ary.tofile(tsf)

  def array_index(self, when):
    ''' Return te array index corresponding the time UNIX time `when`.
    '''
    when_offset = when - self.start
    if when_offset < 0:
      raise ValueError("when:%s predates self.start:%s" % (when, self.start))
    return int(when_offset // self.step)

  def __getitem__(self, when):
    ''' Return the datum for the UNIX time `when`.
    '''
    # avoid confusion with negatiove indices
    if when < 0:
      raise ValueError("invalid when:%s, must be >= 0" % (when,))
    return self.array[self.array_index(when)]

  def __setitem__(self, when, value):
    ''' Set the datum for the UNIX time `when`.
    '''
    if when < 0:
      raise ValueError("invalid when:%s, must be >= 0" % (when,))
    self.array[self.array_index(when)] = value

  def pad_to(self, when, fill=None):
    ''' Pad the time series to store values up to the UNIX time `when`.

        The `fill` value is optional and defaults to `0` for intergers
        and `float('nan')` for floats.
    '''
    if when < 0:
      raise ValueError("invalid when:%s, must be >= 0" % (when,))
    if fill is None:
      if self.typecode == 'd':
        fill = float('nan')
      elif self.typecode == 'q':
        fill = 0
      else:
        raise ValueError(
            "no default fill value for self.typecode %r" % (self.typecode,)
        )
    ary_index = self.array_index(when)
    ary = self.array
    if ary_index >= len(ary):
      ary.extend(fill for _ in range(ary_index - len(ary) + 1))
      assert len(ary) == ary_index + 1


class TimespanPolicy(ABC):

  @typechecked
  def __init__(self, timezone: Optional[str] = None):
    ''' Initialise the policy.

        Parameters:
        * `timezone`: optional timezone name used to compute `datetime`s;
          the default is inferred from the default time zone
          using the `get_default_timezone_name` method
    '''
    if timezone is None:
      timezone = self.get_default_timezone_name()
    self.timezone = timezone

  @staticmethod
  def get_default_timezone_name():
    ''' Return the default timezone name.
    '''
    return arrow.now('local').format('ZZZ')

  @abstractmethod
  def timespan_for(self, when):
    ''' A `TimespanPolicy` bracketing the UNIX time `when`.
    '''
    raise NotImplemented

class DailyPolicy(TimespanPolicy):
  ''' A `TimespanPolicy` bracketing times at day boundaries.
  '''

  def timespan_for(self, when):
    ''' Return the start and end UNIX times
        bracketing the UNIX time `when`.
    '''
    a = arrow.Arrow.fromtimestamp(when, tzinfo=self.timezone)
    start = Arrow(a.year, a.month, a.day, tzinfo=self.timezone)
    end = start.shift(days=1)
    return start.timestamp(), end.timestamp()

class MonthlyPolicy(TimespanPolicy):
  ''' A `TimespanPolicy` bracketing times at month boundaries.
  '''

  def timespan_for(self, when):
    ''' Return the start and end UNIX times
    '''
    a = arrow.Arrow.fromtimestamp(when, tzinfo=self.timezone)
    start = Arrow(a.year, a.month, 1, tzinfo=self.timezone)
    end = start.shift(months=1)
    return start.timestamp(), end.timestamp()
if __name__ == '__main__':
  sys.exit(main(sys.argv))
