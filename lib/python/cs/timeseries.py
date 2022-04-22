#!/usr/bin/env python3
#
# pylint: disable=too-many-lines

''' Efficient portable machine native columnar storage of time series data
    for double float and signed 64-bit integers.

    On a personal basis, I use this as efficient storage of time
    series data from my solar inverter, which reports in a slightly
    clunky time limited CSV format; I import those CSVs into
    time series data directories which contain the overall accrued
    data.

    Three levels of storage are defined here:
    - `TimeSeriesFile`: a single file containing a binary list of
      float64 or signed int64 values
    - `TimeSeriesPartitioned`: a directory containing multiple
      `TimeSeriesFile` files, each covering a separate time span
      according to a supplied policy, for example a calendar month
    - `TimeSeriesDataDir`: a directory containing multiple
      `TimeSeriesPartitioned` subdirectories, each for a different
      time series, for example one subdirectory for grid voltage
      and another for grid power

    Together these provide a hierary for finite sized files storing
    unbounded time series data for multiple parameters.
    The core purpose is to provide time series data storage; there
    are assorted convenience methods to export arbitrary subsets
    of the data for use by other libraries in common forms, such
    as dataframes or series, numpy arrays and simple lists.
    There are also some simple plot methods for making graphs using `plotly`.
'''

from abc import ABC, abstractmethod
from array import array, typecodes  # pylint: disable=no-name-in-module
from contextlib import contextmanager
from fnmatch import fnmatch
from functools import partial
from getopt import GetoptError
import os
from os.path import (
    exists as existspath,
    isdir as isdirpath,
    isfile as isfilepath,
)
import shlex
from struct import pack, Struct  # pylint: disable=no-name-in-module
import sys
import time
from typing import Callable, List, Optional, Tuple, Union

import arrow
from arrow import Arrow
from icontract import ensure, require, DBC
import numpy as np
from numpy import datetime64
from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.configutils import HasConfigIni
from cs.deco import cachedmethod, decorator
from cs.fs import HasFSPath, fnmatchdir, is_clean_subpath, needdir, shortpath
from cs.fstags import FSTags
from cs.lex import is_identifier, s
from cs.logutils import warning, error
from cs.pfx import pfx, pfx_call, Pfx
from cs.py.modules import import_extra
from cs.resources import MultiOpenMixin

from cs.x import X

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 3 - Alpha",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
    'entry_points': {
        'console_scripts': [
            'csts = cs.timeseries:main',
        ],
    },
    'extras_requires': {
        'numpy': ['numpy'],
        'pandas': ['pandas'],
        'plotting': ['kaleido', 'plotly'],
    },
}

Numeric = Union[int, float]

def main(argv=None):
  ''' Run the command line tool for `TimeSeries` data.
  '''
  return TimeSeriesCommand(argv).run()

pfx_listdir = partial(pfx_call, os.listdir)
pfx_mkdir = partial(pfx_call, os.mkdir)
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

class TimeSeriesBaseCommand(BaseCommand, ABC):
  ''' Abstract base class for command line interfaces to `TimeSeries` data files.
  '''

  def cmd_fetch(self, argv):
    ''' Usage: {cmd} ...
          Fetch raw data files from the primary source to a local spool.
          To be implemented in subclasses.
    '''
    raise GetoptError(
        f"the {type(self).__name__} class"
        f" does not provide a \"{self.cmd}\" subcommand"
    )

  @abstractmethod
  def cmd_import(self, argv):
    ''' Usage: {cmd} ...
          Import data into the time series.
          To be implemented in subclasses.
    '''
    raise NotImplementedError

  def cmd_info(self, argv):
    ''' Usage: {cmd} tspath
          Report infomation about the time series stored at tspath.
          tspath may refer to a single .csts TimeSeriesFile,
          a TimeSeriesPartitioned directory of such files,
          or a TimeSeriesDataDir containing partitions for multiple keys.
    '''
    ts = self.popargv(argv, "tspath", timeseries_from_path)
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    print(ts)
    if isinstance(ts, TimeSeries):
      print("  start =", ts.start, arrow.get(ts.start))
      print("  step =", ts.step)
      print("  typecode =", ts.typecode)
    elif isinstance(ts, TimeSeriesPartitioned):
      for tsfilename in sorted(ts.tsfilenames()):
        tsf = TimeSeriesFile(ts.pathto(tsfilename))
        print(" ", tsf)
    elif isinstance(ts, TimeSeriesDataDir):
      for key in sorted(ts.keys()):
        print(" ", key, ts[key])
    else:
      raise RuntimeError("unhandled time series type: %s" % (s(ts),))

  def cmd_plot(self, argv):
    ''' Usage: {cmd} [--show] tspath impath.png days [{{glob|fields}}...]
          Plot the most recent days of data from the time series at tspath
          to impath.png. Open the image if --show is provided.
          tspath may refer to a single .csts TimeSeriesFile,
          a TimeSeriesPartitioned directory of such files,
          or a TimeSeriesDataDir containing partitions for multiple keys.
          If glob is supplied, constrain the keys of a TimeSeriesDataDir
          by the glob.
    '''
    show_image = False
    if argv and argv[0] == '--show':
      show_image = True
      argv.pop(0)
    ts = self.popargv(argv, "tspath", timeseries_from_path)
    imgpath = self.popargv(
        argv, "impath.png", str, lambda path: not existspath(path),
        "already exists"
    )
    days = self.popargv(argv, int, "days to display", lambda days: days > 0)
    now = time.time()
    start = now - days * 24 * 3600
    if isinstance(ts, TimeSeries):
      if argv:
        raise GetoptError(
            "fields:%r should not be suppplied for a %s" % (argv, s(ts))
        )
      figure = ts.plot(start, now)  # pylint: disable=missing-kwoa
    elif isinstance(ts, TimeSeriesDataDir):
      if argv:
        keys = ts.keys(argv)
        if not keys:
          raise GetoptError(
              "no matching keys, I know: %s" % (', '.join(sorted(ts.keys())),)
          )
      else:
        keys = ts.keys()
        if not keys:
          raise GetoptError("no keys in %s" % (ts,))
      figure = ts.plot(
          start, now, keys
      )  # pylint: too-many-function-args.disable=missing-kwoa
    else:
      raise RuntimeError("unhandled type %s" % (s(ts),))
    with Pfx("write %r", imgpath):
      if existspath(imgpath):
        error("already exists")
      else:
        figure.write_image(imgpath, format="png", width=2048, height=1024)
    if show_image:
      os.system(shlex.join(['open', imgpath]))

class TimeSeriesCommand(TimeSeriesBaseCommand):
  ''' Command line interface to `TimeSeries` data files.
  '''

  SUBCOMMAND_ARGV_DEFAULT = 'test'

  # pylint: disable=no-self-use
  def cmd_test(self, argv):
    ''' Usage: {cmd} [testnames...]
          Run some tests of functionality.
    '''
    if not argv:
      argv = ['pandas']

    def test_pandas():
      t0 = 1649552238
      fspath = f'foo--from-{t0}.dat'
      ts = TimeSeriesFile(fspath, 'd', start=t0, step=1)
      ts.pad_to(time.time() + 300)
      print("len(ts) =", len(ts))
      pds = ts.as_pd_series()
      print(type(pds), pds.memory_usage())
      print(pds)

    def test_partitioned_spans():
      policy = TimespanPolicyDaily()
      start = time.time()
      end = time.time() + 7 * 24 * 3600
      print("start =", Arrow.fromtimestamp(start))
      print("end =", Arrow.fromtimestamp(end))
      for partition, partition_start, partition_stop in policy.partitioned_spans(
          start, end):
        print(
            partition,
            Arrow.fromtimestamp(partition_start),
            Arrow.fromtimestamp(partition_stop),
        )

    def test_datadir():
      with TimeSeriesDataDir('tsdatadir', policy='daily', step=300) as datadir:
        ts = datadir['key1']
        ts[time.time()] = 9.0

    def test_timespan_policy():
      policy = TimespanPolicyMonthly()
      policy.timespan_for(time.time())

    def test_timeseries():
      t0 = 1649464235
      fspath = 'foo.dat'
      ts = TimeSeriesFile(fspath, 'd', start=t0, step=1)
      ary = ts.array
      print(ary)
      ts.pad_to(time.time() + 300)
      print(ary)
      ts.save()

    testfunc_map = {
        'datadir': test_datadir,
        'pandas': test_pandas,
        'partitioned_spans': test_partitioned_spans,
        'timeseries': test_timeseries,
        'timespan_policy': test_timespan_policy,
    }
    ok = True
    for testname in argv:
      with Pfx(testname):
        if testname not in testfunc_map:
          warning("unknown test name")
          ok = False
    if not ok:
      raise GetoptError(
          "unknown test names, I know: %s" %
          (", ".join(sorted(testfunc_map.keys())),)
      )
    for testname in argv:
      with Pfx(testname):
        testfunc_map[testname]()

def timeseries_from_path(tspath: str, start=None, step=None, typecode=None):
  ''' Turn a time series filesystem path into a time series:
      * a file: a `TimeSeries`
      * a directory holding `.csts` files: a `TimeSeriesPartitioned`
      * a directory: a `TimeSeriesDataDir`
  '''
  if isfilepath(tspath):
    if not tspath.endswith(TimeSeriesFile.DOTEXT):
      raise GetoptError(
          "%s does not end in %s" % (shortpath(tspath), TimeSeriesFile.DOTEXT)
      )
    return TimeSeriesFile(tspath, None, start=start, step=step)
  if isdirpath(tspath):
    if fnmatchdir(tspath, '*' + TimeSeriesFile.DOTEXT):
      return TimeSeriesPartitioned(
          tspath, typecode, start=start, step=step, policy='annual'
      )
    return TimeSeriesDataDir(tspath, policy=TimespanPolicyAnnual)
  raise ValueError("cannot deduce time series type from tspath %r" % (tspath,))

@decorator
def plotrange(func, needs_start=False, needs_stop=False):
  ''' A decorator for plotting methods with optional `start` and `stop`
      leading positional parameters and an optional `figure` keyword parameter.

      The decorator parameters `needs_start` and `needs_stop`
      may be set to require non-`None` values for `start` and `stop`.

      If `start` is `None` its value is set to `self.start`.
      If `stop` is `None` its value is set to `self.stop`.
      If `figure` is `None` its value is set to a new
      `plotly.graph_objects.Figure` instance.

      The decorated method is then called as:

          func(self, start, stop, *a, figure=figure, **kw)

      where `*a` and `**kw` are the additional positional and keyword
      parameters respectively, if any.
  '''

  # pylint: disable=keyword-arg-before-vararg
  @require(lambda start: not needs_start or start is not None)
  @require(lambda stop: not needs_stop or stop is not None)
  def plotrange_wrapper(self, start=None, stop=None, *a, figure=None, **kw):
    plotly = import_extra('plotly', DISTINFO)
    go = plotly.graph_objects
    if start is None:
      start = self.start
    if stop is None:
      stop = self.stop
    if figure is None:
      figure = go.Figure()
    return func(self, start, stop, *a, figure=figure, **kw)

  return plotrange_wrapper

def get_default_timezone_name():
  ''' Return the default timezone name.
  '''
  return arrow.now('local').format('ZZZ')

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

class TimeStepsMixin:
  ''' Methods for an object with `start` and `step` attributes.
  '''

  def offset(self, when: Numeric) -> int:
    ''' Return the step offset for the UNIX time `when` from `self.start`.

        Eample in a `TimeSeries`:

           >>> ts = TimeSeriesFile('tsfile.csts', 'd', start=19.1, step=1.2)
           >>> ts.offset(19.1)
           0
           >>> ts.offset(20)
           0
           >>> ts.offset(22)
           2
    '''
    offset = when - self.start
    offset_steps = offset // self.step
    when0 = self.start + offset_steps * self.step
    if when0 < self.start:
      offset_steps += 1
    offset_steps_i = int(offset_steps)
    assert offset_steps == offset_steps_i
    return offset_steps_i

  def when(self, offset):
    ''' Return `self.start+offset*self.step`.
    '''
    return self.start + offset * self.step

  def offset_bounds(self, start, stop) -> (int, int):
    ''' Return the bounds of `(start,stop)` as offsets
        (multiples of `self.step`).
    '''
    offset_steps = self.offset(start)
    end_offset_steps = self.offset(stop)
    if end_offset_steps == offset_steps and stop > start:
      end_offset_steps += 1
    return offset_steps, end_offset_steps

  def offset_range(self, start, stop):
    ''' Return an iterable of the offsets from `start` to `stop`
        in units of `self.step`
        i.e. `offset(start) == 0`.

        Eample in a `TimeSeries`:

           >>> ts = TimeSeriesFile('tsfile.csts', 'd', start=19.1, step=1.2)
           >>> list(ts.offset_range(20,30))
           [0, 1, 2, 3, 4, 5, 6, 7, 8]
    '''
    if start < self.start:
      raise IndexError(
          "start:%s must be >= self.start:%s" % (start, self.start)
      )
    if stop < start:
      raise IndexError("start:%s must be <= stop:%s" % (start, stop))
    offset_steps, end_offset_steps = self.offset_bounds(start, stop)
    return range(offset_steps, end_offset_steps)

  def round_down(self, when):
    ''' Return `when` rounded down to the start of its time slot.
    '''
    return self.when(self.offset(when))

  def round_up(self, when):
    ''' Return `when` rounded up to the start of the next time slot.
    '''
    rounded = self.round_down(when)
    if rounded < when:
      rounded = self.when(self.offset(when) + 1)
    return rounded

  def range(self, start, stop):
    ''' Return an iterable of the times from `start` to `stop`.

        Eample in a `TimeSeries`:

           >>> ts = TimeSeriesFile('tsfile.csts', 'd', start=19.1, step=1.2)
           >>> list(ts.range(20,30))
           [19.1, 20.3, 21.5, 22.700000000000003, 23.900000000000002, 25.1, 26.3, 27.5, 28.700000000000003]


        Note that if the `TimeSeries` uses `float` values for `start` and `step`
        then the values returned here will not necessarily round trip
        to array indicies because of rounding.

        As such, these times are useful for supplying the index to
        a time series as might be wanted for a graph, but are not
        reliably useful to _obtain_ the values from the time series.
        So this is reliable:

            # works well: pair up values with their times
            graph_data = zip(ts.range(20,30), ts[20:30])

        but this is unreliable because of rounding:

            # unreliable: pair up values with their times
            times = list(ts.range(20, 30))
            graph_data = zip(times, [ts[t] for t in times])

        The reliable form is available as the `data(start,stop)` method.

        Instead, the reliable way to obtain the values between the
        UNIX times `start` and `stop` is to directly fetch them
        from the `array` underlying the `TimeSeries`.
        This can be done using the `offset_bounds`
        or `array_indices` methods to obtain the `array` indices,
        for example:

            astart, astop = ts.offset_bounds(start, stop)
            return ts.array[astart:astop]

        or more conveniently by slicing the `TimeSeries`:

            values = ts[start:stop]
    '''
    # this would be a range but they only work in integers
    return (
        self.start + self.step * offset_step
        for offset_step in self.offset_range(start, stop)
    )

class TimeSeries(MultiOpenMixin, TimeStepsMixin, ABC):
  ''' Common base class of any time series.
  '''

  def __init__(self, start, step, typecode):
    self.start = start
    self.step = step
    self.typecode = typecode

  @abstractmethod
  def __getitem__(self, index):
    ''' Return a datum or list of data.
    '''
    raise NotImplementedError

  def data(self, start, stop):
    ''' Return an iterable of `(when,datum)` tuples for each time `when`
        from `start` to `stop`.
    '''
    return zip(self.range(start, stop), self[start:stop])

  @property
  def np_type(self):
    ''' The `numpy` type corresponding to `self.typecode`.
    '''
    if self.typecode == 'd':
      return np.float64
    if self.typecode == 'q':
      return np.int64
    raise TypeError(
        "%s.np_type: unsupported typecode %r" %
        (type(self).__name__, self.typecode)
    )

  @pfx
  def as_np_array(self, start=None, stop=None) -> np.array:
    ''' Return a `numpy.array` 1xN array containing the data from `start` to `stop`,
        default from `self.start` and `self.stop` respectively.
    '''
    if start is None:
      start = self.start
    if stop is None:
      stop = self.stop
    return np.array([self[start:stop]], self.np_type)

  @pfx
  def as_pd_series(self, start=None, stop=None):
    ''' Return a `pandas.Series` containing the data from `start` to `stop`,
        default from `self.start` and `self.stop` respectively.
    '''
    pandas = import_extra('pandas', DISTINFO)
    if start is None:
      start = self.start
    if stop is None:
      stop = self.stop
    times, data = self.data2(start, stop)
    indices = (datetime64(t, 's') for t in times)
    return pandas.Series(data, indices)

  def data2(self, start, stop):
    ''' Like `data(start,stop)` but returning 2 lists: one of time and one of data.
    '''
    data = self.data(start, stop)
    return [d[0] for d in data], [d[1] for d in data]

  @plotrange
  def plot(self, start, stop, *, figure, name=None, **scatter_kw):
    ''' Plot a trace on `figure:plotly.graph_objects.Figure`,
        creating it if necessary.
        Return `figure`.
    '''
    plotly = import_extra('plotly', DISTINFO)
    go = plotly.graph_objects
    if name is None:
      name = "%s[%s:%s]" % (self, arrow.get(start), arrow.get(stop))
    xdata, yaxis = self.data2(start, stop)
    xaxis = np.array(xdata).astype('datetime64[s]')
    assert len(xaxis) == len(yaxis), (
        "len(xaxis):%d != len(yaxis):%d, start=%s, stop=%s" %
        (len(xaxis), len(yaxis), start, stop)
    )
    figure.add_trace(go.Scatter(name=name, x=xaxis, y=yaxis, **scatter_kw))
    return figure

# pylint: disable=too-many-instance-attributes
class TimeSeriesFile(TimeSeries):
  ''' A file continaing a single time series for a single data field.

      This provides easy access to a time series data file.
      The instance can be indexed by UNIX time stamp for time based access
      or its `.array` property can be accessed for the raw data.

      Read only users can just instantiate an instance.
      Read/write users should use the instance as a context manager,
      which will automatically rewrite the file with the array data
      on exit.

      Note that the save-on-close is done with `TimeSeries.flush()`
      which ony saves if `self.modified`.
      Use of the `__setitem__` or `pad_to` methods set this flag automatically.
      Direct access via the `.array` will not set it,
      so users working that way for performance should update the flag themselves.

      The data file itself has a header indicating the file data big endianness
      and datum type (an `array.array` type code).
      This is automatically honoured on load and save.
      Note that the header _does not_ indicate the `start`,`step` time range of the data.
  '''

  DOTEXT = '.csts'
  MAGIC = b'csts'
  HEADER_LENGTH = 8

  # pylint: disable=too-many-branches
  @typechecked
  def __init__(
      self,
      fspath: str,
      typecode: Optional[str] = None,
      *,
      start: Union[int, float] = None,
      step: Union[int, float] = None,
      fill=None,
      fstags=None,
  ):
    ''' Prepare a new time series stored in the file at `fspath`
        containing machine data for the time series values.

        Parameters:
        * `fspath`: the filename of the data file
        * `typecode` optional expected `array.typecode` value of the data;
          if specified and the data file exists, they must match;
          if not specified then the data file must exist
          and the `typecode` will be obtained from its header
        * `start`: the UNIX epoch time for the first datum
        * `step`: the increment between data times
        * `fill`: optional default fill values for `pad_to`;
          if unspecified, fill with `0` for `'q'`
          and `float('nan') for `'d'`

        If `start` or `step` are omitted the file's fstags will be
        consulted for their values.
        This class does not set these tags (that would presume write
        access to the parent directory or its `.fstags` file)
        when a `TimeSeriesFile` is made by a `TimeSeriesPartitioned` instance
        it sets these flags.
    '''
    if fstags is None:
      fstags = FSTags()
    self.fstags = fstags
    if start is None:
      start = self.tags.start
      if start is None:
        raise ValueError("no start and no 'start' FSTags tag")
    if step is None:
      step = self.tags.step
      if step is None:
        raise ValueError("no step and no 'step' FSTags tag")
    if typecode is not None and typecode not in SUPPORTED_TYPECODES:
      raise ValueError(
          "expected typecode to be one of %r, got %r" %
          (tuple(SUPPORTED_TYPECODES.keys()), typecode)
      )
    if step <= 0:
      raise ValueError("step should be >0, got %s" % (step,))
    self.fspath = fspath
    # compare the file against the supplied arguments
    hdr_stat = self.stat(fspath)
    if hdr_stat is None:
      if typecode is None:
        raise ValueError(
            "no typecode supplied and no data file %r" % (fspath,)
        )
      file_bigendian = NATIVE_BIGENDIANNESS[typecode]
    else:
      file_typecode, file_bigendian = hdr_stat
      if typecode is None:
        typecode = file_typecode
      elif typecode != file_typecode:
        raise ValueError(
            "typecode=%r but data file %s has typecode %r" %
            (typecode, fspath, file_typecode)
        )
    if fill is None:
      if typecode == 'd':
        fill = float('nan')
      elif typecode == 'q':
        fill = 0
      else:
        raise RuntimeError(
            "no default fill value for typecode=%r" % (typecode,)
        )
    super().__init__(start, step, typecode)
    self.file_bigendian = file_bigendian
    self.fill = fill
    self._itemsize = array(typecode).itemsize
    assert self._itemsize == 8
    struct_fmt = self.make_struct_format(typecode, self.file_bigendian)
    self._struct = Struct(struct_fmt)
    assert self._struct.size == self._itemsize
    self.modified = False
    self._array = None

  def __str__(self):
    return "%s(%s,%r,%d:%d,%r)" % (
        type(self).__name__, shortpath(self.fspath), self.typecode, self.start,
        self.step, self.fill
    )

  @contextmanager
  def startup_shutdown(self):
    yield self
    self.flush()

  @property
  def end(self):
    ''' The end time of this array,
        computed as `self.start+len(self.array)*self.step`.
    '''
    return self.start + len(self.array) * self.step

  @property
  @cachedmethod
  def tags(self):
    ''' The `TagSet` associated with this `TimeSeriesFile` instance.
    '''
    return self.fstags[self.fspath]

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
    if bytes((_1, _2)) != b'__':
      warning(
          "ignoring unexpected header trailer, expected %r, got %r" %
          (b'__', _1 + _2)
      )
    return typecode, bigendian

  @classmethod
  @pfx
  def stat(cls, fspath):
    ''' Read the data file header, return `(typecode,bigendian)`
        as from the `parse_header(heasder_bs)` method.
        Returns `None` if the file does not exist.
        Raises `ValueError` for an invalid header.
    '''
    # read the data file header
    try:
      with pfx_open(fspath, 'rb') as tsf:
        header_bs = tsf.read(cls.HEADER_LENGTH)
      if len(header_bs) != cls.HEADER_LENGTH:
        raise ValueError(
            "file header is the wrong length, expected %d, got %d" %
            (cls.HEADER_LENGTH, len(header_bs))
        )
    except FileNotFoundError:
      # file does not exist
      return None
    return cls.parse_header(header_bs)

  @property
  @cachedmethod
  def array(self):
    ''' The time series as an `array.array` object.
        This loads the array data from `self.fspath` on first use.
    '''
    try:
      ary = self.load_from(self.fspath, self.typecode)
    except FileNotFoundError:
      # no file, empty array
      ary = array(self.typecode)
    return ary

  def flush(self, keep_array=False):
    ''' Save the data file if `self.modified`.
    '''
    if self.modified:
      self.save()
      self.modified = False
      if not keep_array:
        self._array = None

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

  @ensure(lambda result: result >= 0)
  def array_index(self, when) -> int:
    ''' Return the array index corresponding the time UNIX time `when`.
    '''
    if when < self.start:
      raise ValueError("when:%s predates self.start:%s" % (when, self.start))
    return self.offset(when)

  def array_index_bounds(self, start, stop) -> (int, int):
    ''' Return a `(array_start,array_stop)` pair for the array indices
        between the UNIX times `start` and `stop`.

        Eample:

           >>> ts = TimeSeriesFile('tsfile.csts', 'd', 19.1, 1.2)
           >>> ts.array_index_bounds(20,30)
           (0, 9)
    '''
    if start < self.start:
      raise IndexError(
          "start:%s must be >= self.start:%s" % (start, self.start)
      )
    return self.offset_bounds(start, stop)

  def array_indices(self, start, stop):
    ''' Return an iterable of the array indices for the UNIX times
        from `start` to `stop` from this `TimeSeries`.

        Eample:

           >>> ts = TimeSeriesFile('tsfile.csts', 'd', 19.1, 1.2)
           >>> list(ts.array_indices(20,30))
           [0, 1, 2, 3, 4, 5, 6, 7, 8]
    '''
    return self.offset_range(start, stop)

  @typechecked
  def index_when(self, index: int):
    ''' Return the UNIX time corresponding to the array index `index`.
    '''
    if index < 0:
      raise IndexError("index:%d must be >=0" % (index,))
    return self.when(index)

  def __len__(self):
    ''' The length of the time series data,
        from `len(self.array)`.
    '''
    return len(self.array)

  @typechecked
  def __getitem__(self, when: Union[Numeric, slice]):
    ''' Return the datum for the UNIX time `when`.

        If `when` is a slice, return a list of the data
        for the times in the range `start:stop`
        as given by `self.range(start,stop)`.
    '''
    ary = self.array
    if isinstance(when, slice):
      start, stop, step = when.start, when.stop, when.step
      if step is not None:
        raise ValueError(
            "%s index slices may not specify a step" % (type(self).__name__,)
        )
      astart, astop = self.offset_bounds(start, stop)
      return ary[astart:astop]
    # avoid confusion with negative indices
    if when < 0:
      raise ValueError("invalid when:%s, must be >= 0" % (when,))
    return ary[self.array_index(when)]

  def __setitem__(self, when, value):
    ''' Set the datum for the UNIX time `when`.
    '''
    if when < 0:
      raise ValueError("invalid when:%s, must be >= 0" % (when,))
    self.pad_to(when)
    assert isinstance(value,
                      (int, float)), "value is a %s:%r" % (type(value), value)
    self.array[self.array_index(when)] = value
    self.modified = True

  def pad_to(self, when, fill=None):
    ''' Pad the time series to store values up to the UNIX time `when`.

        The `fill` value is optional and defaults to the `fill` value
        supplied when the `TimeSeries` was initialised.
    '''
    if when < 0:
      raise ValueError("invalid when:%s, must be >= 0" % (when,))
    if fill is None:
      fill = self.fill
    ary_index = self.array_index(when)
    ary = self.array
    if ary_index >= len(ary):
      ary.extend(fill for _ in range(ary_index - len(ary) + 1))
      self.modified = True
      assert len(ary) == ary_index + 1

class TimespanPolicy(DBC):
  ''' A class implementing a policy about where to store data,
      used by `TimeSeriesPartitioned` instances
      to partition data among multiple `TimeSeries` data files.

      Probably the most important methods are `partition_for(when)`
      which returns a label for a timestamp (eg `"2022-01"` for a monthly policy)
      and `timespan_for` which returns the per partition start and end times
      enclosing a timestamp.
  '''

  name = None  # subclasses get this when they are registered
  FACTORIES = {}
  DEFAULT_NAME = 'monthly'
  DEFAULT_PARTITION_FORMAT = ''  # set by subclasses to an Arrow format string

  @typechecked
  def __init__(self, *, timezone: Optional[str] = None):
    ''' Initialise the policy.

        Parameters:
        * `timezone`: optional timezone name used to compute `datetime`s;
          the default is inferred from the default time zone
          using the `get_default_timezone_name` function
    '''
    self.name = type(self).name
    if timezone is None:
      timezone = get_default_timezone_name()
    self.timezone = timezone

  def __str__(self):
    return "%s:%r:%r:%r" % (
        type(self).__name__, self.name, self.DEFAULT_PARTITION_FORMAT,
        self.timezone
    )

  # pylint: disable=keyword-arg-before-vararg
  @classmethod
  def from_name(cls, policy_name=None, *a, **kw):
    ''' Factory method to return a new `TimespanPolicy` instance
        from the policy name,
        which indexes `TimespanPolicy.FACTORIES`.
        The default `policy_name`
        is from `TimespanPolicy.DEFAULT_NAME` (`'monthly'`).
    '''
    if policy_name is None:
      policy_name = cls.DEFAULT_NAME
    return cls.FACTORIES[policy_name](*a, **kw)

  @classmethod
  def from_any(cls, policy):
    ''' Factory to promote `policy` to a `TimespanPolicy` instance.

        The supplied `policy` may be:
        * `None`: return an instance of the default named policy
        * `str`: return an instance of the named policy
        * `TimespanPolicy` subclass: return an instance of the subclass
        * `TimespanPolicy` instance: return the instance
    '''
    if policy is None:
      policy = TimespanPolicy.from_name()
    elif isinstance(policy, str):
      policy = TimespanPolicy.from_name(policy)
    elif isinstance(policy, type) and issubclass(policy, TimespanPolicy):
      policy = policy()
    else:
      assert isinstance(policy, TimespanPolicy
                        ), "policy=%s:%r" % (type(policy), policy)
    return policy

  @classmethod
  @typechecked
  def register_factory(cls, factory: Callable, name: str):
    ''' Register a new policy `factory` under then supplied `name`.
    '''
    if name in cls.FACTORIES:
      raise KeyError(
          "%s.FACTORIES: name %r already taken" % (cls.__name__, name)
      )
    cls.FACTORIES[name] = factory
    factory.name = name

  def Arrow(self, when):
    ''' Return an `arrow.Arrow` instance for the UNIX time `when`
        in the policy timezone.
    '''
    return arrow.Arrow.fromtimestamp(when, tzinfo=self.timezone)

  @abstractmethod
  @ensure(lambda when, result: result[0] <= when < result[1])
  def timespan_for(self, when: Numeric) -> Tuple[Numeric, Numeric]:
    ''' A `TimespanPolicy` bracketing the UNIX time `when`.
    '''
    raise NotImplementedError

  def partition_for(self, when):
    ''' Return the default partition for the UNIX time `when`,
        which is derived from the `arrow.Arrow`
        format string `self.DEFAULT_PARTITION_FORMAT`.
    '''
    return self.Arrow(when).format(self.DEFAULT_PARTITION_FORMAT)

  @require(lambda start, stop: start < stop)
  def partitioned_spans(self, start, stop):
    ''' Generator yielding a sequence of `(partition,partition_start,partition_stop)`
        covering the range `start:stop`.
    '''
    when = start
    while when < stop:
      partition = self.partition_for(when)
      _, partition_stop = self.timespan_for(when)
      yield partition, when, min(partition_stop, stop)
      when = partition_stop

  def partition_timespan(self, partition: str) -> Tuple[Numeric, Numeric]:
    ''' Return the start and end times for the supplied `partition`.
    '''
    return self.timespan_for(
        arrow.get(
            partition, self.DEFAULT_PARTITION_FORMAT, tzinfo=self.timezone
        ).timestamp
    )

class TimespanPolicyDaily(TimespanPolicy):
  ''' A `TimespanPolicy` bracketing times at day boundaries.
  '''

  DEFAULT_PARTITION_FORMAT = 'YYYY-MM-DD'

  def timespan_for(self, when):
    ''' Return the start and end UNIX times
        (inclusive and exclusive respectively)
        bracketing the UNIX time `when`.
    '''
    a = self.Arrow(when)
    start = Arrow(a.year, a.month, a.day, tzinfo=self.timezone)
    end = start.shift(days=1)
    return start.timestamp, end.timestamp

TimespanPolicy.register_factory(TimespanPolicyDaily, 'daily')

class TimespanPolicyMonthly(TimespanPolicy):
  ''' A `TimespanPolicy` bracketing times at month boundaries.
  '''

  DEFAULT_PARTITION_FORMAT = 'YYYY-MM'

  def timespan_for(self, when):
    ''' Return the start and end UNIX times
        (inclusive and exclusive respectively)
        bracketing the UNIX time `when`.
    '''
    a = self.Arrow(when)
    start = Arrow(a.year, a.month, 1, tzinfo=self.timezone)
    end = start.shift(months=1)
    return start.timestamp, end.timestamp

TimespanPolicy.register_factory(TimespanPolicyMonthly, 'monthly')

class TimespanPolicyAnnual(TimespanPolicy):
  ''' A `TimespanPolicy` bracketing times at month boundaries.
  '''

  DEFAULT_PARTITION_FORMAT = 'YYYY'

  def timespan_for(self, when):
    ''' Return the start and end UNIX times
        (inclusive and exclusive respectively)
        bracketing the UNIX time `when`.
    '''
    a = self.Arrow(when)
    start = Arrow(a.year, 1, 1, tzinfo=self.timezone)
    end = start.shift(years=1)
    return start.timestamp, end.timestamp

TimespanPolicy.register_factory(TimespanPolicyAnnual, 'annual')

class TimeSeriesMapping(dict, MultiOpenMixin, TimeStepsMixin, ABC):
  ''' A group of named `TimeSeries` instances, indexed by a key.

      This is the basis for `TimeSeriesDataDir`.
  '''

  @typechecked
  def __init__(
      self,
      *,
      step: Numeric,
      policy=None,  # :TimespanPolicy
      timezone: Optional[str] = None,
  ):
    super().__init__()
    if timezone is None:
      timezone = get_default_timezone_name()
    if policy is None:
      policy_name = TimespanPolicy.DEFAULT_NAME
      policy = TimespanPolicy.from_name(policy_name, timezone=timezone)
    self.step = step
    self.policy = policy
    self._rules = {}

  def __str__(self):
    return "%s(%s,%s)" % (
        type(self).__name__,
        getattr(self, 'step', 'STEP_UNDEFINED'),
        getattr(self, 'policy', 'POLICY_UNDEFINED'),
    )

  @contextmanager
  def startup_shutdown(self):
    ''' Context manager for `MultiOpenMixin`.
        Close the sub time series.
    '''
    try:
      yield
    finally:
      for ts in self.values():
        ts.close()

  @staticmethod
  def validate_key(key):
    ''' Check that `key` is a valid key, raise `valueError` if not.
        This implementation requires that `key` is an identifier.
    '''
    if not is_identifier(key):
      raise ValueError("invalid key %r, not an identifier" % (key,))
    return True

  def __missing__(self, key):
    ''' Create a new entry for `key` if missing.
        This implementation looks up the rules.
    '''
    self.validate_key(key)
    for fnglob, derivation in self._rules.items():
      if fnmatch(key, fnglob):
        # Construct a new time series from key.
        ts = derivation(key)
        self[key] = ts
        return
    raise KeyError("no entry for key %r and no implied time series" % (key,))

  @typechecked
  def __setitem__(self, key: str, ts):
    ''' Insert a time series into this `TimeSeriesMapping`.
        `key` may not already be present.
    '''
    self.validate_key(key)
    if key in self:
      raise ValueError("key already exists: %r" % (key,))
    super().__setitem__(key, ts)

  # pylint: disable=no-self-use,unused-argument
  def key_typecode(self, key):
    ''' The `array` type code for `key`.
        This default method returns `'d'` (float64).
    '''
    return 'd'

  @pfx
  @typechecked
  def as_pd_dataframe(
      self,
      start=None,
      stop=None,
      keys: Optional[List[str]] = None,
  ):
    ''' Return a `numpy.DataFrame` containing the specified data.

        Parameters:
        * `start`: start time of the data
        * `stop`: end time of the data
        * `keys`: optional iterable of keys, default from `self.keys()`
    '''
    pandas = import_extra('pandas', DISTINFO)
    if start is None:
      start = self.start
    if stop is None:
      stop = self.stop
    if keys is None:
      keys = self.keys()
    elif not isinstance(keys, (tuple, list)):
      keys = tuple(keys)
    indices = [datetime64(t, 's') for t in self.range(start, stop)]
    data_dict = {}
    for key in keys:
      with Pfx(key):
        if key not in self:
          raise KeyError("no such key")
        data_dict = self[key].as_np_array(start, stop)
    return pandas.DataFrame(
        data=data_dict,
        index=indices,
        columns=keys,
        copy=False,
    )

  @plotrange
  def plot(
      self, start, stop, keys=None, *, figure, key_colors=None, **scatter_kw
  ):
    ''' Plot traces on `figure:plotly.graph_objects.Figure`,
        creating it if necessary, for each key in `keys`.
        Return `figure`.

        Parameters:
        * `start`: optional start, default `self.start`
        * `stop`: optional stop, default `self.stop`
        * `keys`: optional list of keys, default all keys
        * `figure`: optional figure, created if not specified
        * `key_colors`: option mapping of key to `marker_color`
        Other keyword parameters are passed to `Scatter`.
    '''
    if keys is None:
      keys = sorted(self.keys())
    for key in keys:
      with Pfx(key):
        tsks = self[key]
        name = tsks.tags.get('csv.header', key)
        key_scatter_kw = dict(scatter_kw)
        if key_colors:
          try:
            colour = key_colors[key]
          except KeyError:
            pass
          else:
            key_scatter_kw.update(marker_color=colour)
        figure = tsks.plot(
            start, stop, figure=figure, name=name, **key_scatter_kw
        )
    return figure

class TimeSeriesDataDir(TimeSeriesMapping, HasFSPath, HasConfigIni,
                        TimeStepsMixin):
  ''' A directory containing a collection of `TimeSeries` data files.
  '''

  @typechecked
  def __init__(
      self,
      fspath,
      *,
      step: Optional[Numeric] = None,
      policy=None,  # :TimespanPolicy
      timezone: Optional[str] = None,
      fstags: Optional[FSTags] = None,
  ):
    HasFSPath.__init__(self, fspath)
    if fstags is None:
      fstags = FSTags()
    HasConfigIni.__init__(self, 'TimeSeriesDataDir')
    self.fstags = fstags
    config = self.config
    if step is None:
      if config.step is None:
        raise ValueError("missing step parameter and no step in config")
      step = config.step
    elif self.step is None:
      self.step = step
    elif step != self.step:
      raise ValueError("step:%r != config.step:%r" % (step, self.step))
    self.start = 0
    timezone = timezone or self.timezone
    if policy is None:
      policy_name = config.auto.policy.name or TimespanPolicy.DEFAULT_NAME
      policy = TimespanPolicy.from_name(policy_name)
    else:
      policy = TimespanPolicy.from_any(policy)
      policy_name = policy.name
    # fill in holes in the config
    if not config.auto.policy.name:
      self.policy_name = policy_name
    if not config.auto.policy.timezone:
      self.timezone = timezone
    TimeSeriesMapping.__init__(
        self, step=step, policy=policy, timezone=timezone
    )
    self._infill_keys_from_subdirs()

  def __str__(self):
    return "%s(%s,%s,%s)" % (
        type(self).__name__,
        shortpath(self.fspath),
        getattr(self, 'step', 'STEP_UNDEFINED'),
        getattr(self, 'policy', 'POLICY_UNDEFINED'),
    )

  def _infill_keys_from_subdirs(self):
    ''' Fill in any missing keys from subdirectories.
    '''
    for key in pfx_listdir(self.fspath):
      with Pfx(key):
        if key in self:
          continue
        try:
          self.validate_key(key)
        except ValueError:
          continue
        keypath = self.pathto(key)
        if not isdirpath(keypath):
          continue
        self[key] = self._tsfactory(key)

  def _tsfactory(self, key):
    ''' Create a `TimeSeriesPartitioned` for `key`.
    '''
    self.validate_key(key)
    keypath = self.pathto(key)
    needdir(keypath)
    ts = TimeSeriesPartitioned(
        keypath,
        self.key_typecode(key),
        step=self.step,
        policy=self.policy,
        fstags=self.fstags,
    )
    ts.tags['key'] = key
    ts.tags['step'] = ts.step
    ts.tags['typecode'] = ts.typecode
    ts.open()
    return ts

  @contextmanager
  def startup_shutdown(self):
    ''' Context manager for `MultiOpenMixin`.
        Close the sub time series and save the config if modified.
    '''
    try:
      with self.fstags:
        with super().startup_shutdown():
          yield
    finally:
      self.config_flush()

  @property
  def policy_name(self):
    ''' The `policy.timezone` config value, usually a key from
        `TimespanPolicy.FACTORIES`.
    '''
    name = self.config.auto.policy.name
    if not name:
      name = TimespanPolicy.DEFAULT_NAME
      self.policy_name = name
    return name

  @policy_name.setter
  def policy_name(self, new_policy_name: str):
    ''' Set the `policy.timezone` config value, usually a key from
        `TimespanPolicy.FACTORIES`.
    '''
    self.config['policy.name'] = new_policy_name

  @property
  def step(self):
    ''' The `step` config value, the size of a time slot.
    '''
    return self.config.step

  @step.setter
  def step(self, new_step: Numeric):
    ''' Set the `step` config value, the size of a time slot.
    '''
    if new_step <= 0:
      raise ValueError("step must be >0, got %r" % (new_step,))
    self.config['step'] = new_step

  @property
  def timezone(self):
    ''' The `policy.timezone` config value, a timezone name.
    '''
    name = self.config.auto.policy.timezone
    if not name:
      name = get_default_timezone_name()
      self.timezone = name
    return name

  @timezone.setter
  def timezone(self, new_timezone: str):
    ''' Set the `policy.timezone` config value, a timezone name.
    '''
    self.config['policy.timezone'] = new_timezone

  def keys(self, fnglobs: Optional[Union[str, List[str]]] = None):
    ''' Return a list of the known keys, derived from the subdirectories,
        optionally constrained by `fnglobs`.
        If provided, `fnglobs` may be a glob string or list of glob strings
        suitable for `fnmatch`.
    '''
    all_keys = sorted(super().keys())
    if fnglobs is None:
      return all_keys
    if isinstance(fnglobs, str):
      fnglobs = [fnglobs]
    ks = []
    for fnglob in fnglobs:
      gks = [k for k in all_keys if fnmatch(k, fnglob)]
      if gks:
        ks.extend(gks)
      else:
        warning("no matches for %r", fnglob)
    return ks

class TimeSeriesPartitioned(TimeSeries, HasFSPath):
  ''' A collection of `TimeSeries` files in a subdirectory.
      We have one of these for each `TimeSeriesDataDir` key.

      This class manages a collection of files
      named by the partition from a `TimespanPolicy`,
      which dictates which partition holds the datum for a UNIX time.
  '''

  @typechecked
  @require(lambda step: step > 0)
  def __init__(
      self,
      dirpath: str,
      typecode: str,
      *,
      policy,  # :TimespanPolicy,
      start: Optional[Numeric] = None,
      step: Optional[Numeric] = None,
      fstags: Optional[FSTags] = None,
  ):
    ''' Initialise the `TimeSeriesPartitioned` instance.

        Parameters:
        * `dirpath`: the directory filesystem path,
          known as `.fspath` within the instance
        * `typecode`: the `array` type code for the data
        * `policy`: the partitioning `TimespanPolicy`
        * `start`: the reference epoch, default `0`
        * `step`: keyword parameter specifying the width of a time slot

        The instance requires a reference epoch
        because the `policy` start times will almost always
        not fall on exact multiples of `step`.
        The reference allows for reliable placement of times
        which fall within `step` of a partition boundary.
        For example, if `start==0` and `step==6` and a partition
        boundary came at `19` (eg due to some calendar based policy)
        then a time of `20` would fall in the partion left of the
        boundary because it belongs to the time slot commencing at `18`.

        If `start` or `step` or `typecode` are omitted the file's
        fstags will be consulted for their values.
        The `start` parameter will further fall back to `0`.
        This class does not set these tags (that would presume write
        access to the parent directory or its `.fstags` file)
        when a `TimeSeriesPartitioned` is made by a `TimeSeriesDataDir`
        instance it sets these flags.
    '''
    HasFSPath.__init__(self, dirpath)
    if fstags is None:
      fstags = FSTags()
    self.fstags = fstags
    if start is None:
      start = self.tags.start
      if start is None:
        start = 0
    if step is None:
      step = self.tags.step
      if step is None:
        raise ValueError("no step and no FSTags 'step' tag")
    if typecode is None:
      typecode = self.tags.typecode
      if typecode is None:
        raise ValueError("no typecode and no FSTags 'typecode' tag")
    if typecode not in SUPPORTED_TYPECODES:
      raise ValueError(
          "typecode=%s not in SUPPORTED_TYPECODES:%r" %
          (s(typecode), sorted(SUPPORTED_TYPECODES.keys()))
      )
    TimeSeries.__init__(self, start, step, typecode)
    policy = TimespanPolicy.from_any(policy)
    self.policy = policy
    self.start = start
    self.step = step
    self._ts_by_partition = {}

  def __str__(self):
    return "%s(%s,%r,%s,%s)" % (
        type(self).__name__,
        shortpath(self.fspath),
        getattr(self, 'typecode', 'NO_TYPECODE_YET'),
        getattr(self, 'step', 'NO_STEP_YET'),
        getattr(self, 'policy', 'NO_POLICY_YET'),
    )

  @contextmanager
  def startup_shutdown(self):
    ''' Close the subsidiary `TimeSeries` instances.
    '''
    try:
      with self.fstags:
        yield
    finally:
      for ts in self._ts_by_partition.values():
        ts.close()

  @property
  @cachedmethod
  def tags(self):
    ''' The `TagSet` associated with this `TimeSeriesPartitioned` instance.
    '''
    return self.fstags[self.fspath]

  @typechecked
  def partition_for(self, when: Numeric) -> str:
    ''' Return the partition for the UNIX time `when`.
    '''
    return self.policy.partition_for(self.round_down(when))

  def timespan_for(self, when):
    ''' Return the start and end UNIX times for the partition storing `when`.
    '''
    return self.policy.timespan_for(self.round_down(when))

  @typechecked
  def subseries(self, spec: Union[str, Numeric]):
    ''' Return the `TimeSeries` for `spec`,
        which may be a partition name or a UNIX time.
    '''
    if isinstance(spec, str):
      partition = spec
    else:
      # numeric UNIX time
      partition = self.partition_for(spec)
    try:
      ts = self._ts_by_partition[partition]
    except KeyError:
      partition_start, partition_stop = self.policy.partition_timespan(
          partition
      )
      filepath = self.pathto(partition + TimeSeriesFile.DOTEXT)
      ts = self._ts_by_partition[partition] = TimeSeriesFile(
          filepath, self.typecode, start=partition_start, step=self.step
      )
      ts.tags['partition'] = partition
      ts.tags['start'] = partition_start
      ts.tags['stop'] = partition_stop
      ts.tags['step'] = self.step
      ts.open()
    return ts

  def __getitem__(self, when: Union[Numeric, slice]):
    if isinstance(when, slice):
      if when.step is not None and when.step != self.step:
        raise IndexError(
            "slice.step:%r should be None or ==self.step:%r" %
            (when.step, self.step)
        )
      return [self[t] for t in self.range(when.start, when.stop)]
    return self.subseries(when)[when]

  def __setitem__(self, when: Numeric, value):
    self.subseries(when)[when] = value

  def tsfilenames(self):
    ''' Return a list of the time series data filenames.
    '''
    return self.fnmatch('*' + TimeSeriesFile.DOTEXT)

  def partition(self, start, stop):
    ''' Return an iterable of `(when,subseries)` for each time `when`
        from `start` to `stop`.
    '''
    ts = None
    partition_start = None
    partition_stop = None
    for when in self.range(start, stop):
      if partition_start is not None and not partition_start <= when < partition_stop:
        # different range, invalidate the current bounds
        partition_start = None
      if partition_start is None:
        ts = self.subseries(when)
        partition_start, partition_stop = self.timespan_for(when)
      yield when, ts

  def setitems(self, whens, values):
    ''' Store `values` against the UNIX times `whens`.

        This is most efficient if `whens` are ordered.
    '''
    ts = None
    partition_start = None
    partition_stop = None
    for when, value in zip(whens, values):
      if partition_start is not None and not partition_start <= when < partition_stop:
        # different range, invalidate the current bounds
        partition_start = None
      if partition_start is None:
        ts = self.subseries(when)
        partition_start, partition_stop = self.timespan_for(when)
      ts[when] = value

  def data(self, start, stop):
    ''' Return a list of `(when,datum)` tuples for the slot times from `start` to `stop`.
    '''
    xydata = []
    for partition, partition_start, partition_stop in self.policy.partitioned_spans(
        start, stop):
      ts = self.subseries(partition)
      xydata.extend(ts.data(partition_start, partition_stop))
    return xydata

  @plotrange
  def plot(self, start, stop, *, figure, name=None, **scatter_kw):
    ''' Plot a trace on `figure:plotly.graph_objects.Figure`,
        creating it if necessary.
        Return `figure`.
    '''
    if name is None:
      name = self.tags.get(
          'csv.header'
      ) or "%s[%s:%s]" % (self, arrow.get(start), arrow.get(stop))
    return super().plot(start, stop, figure=figure, name=name, **scatter_kw)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
