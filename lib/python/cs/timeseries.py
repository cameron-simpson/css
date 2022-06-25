#!/usr/bin/env python3
#
# pylint: disable=too-many-lines

''' Efficient portable machine native columnar storage of time series data
    for double float and signed 64-bit integers.

    The core purpose is to provide time series data storage; there
    are assorted convenience methods to export arbitrary subsets
    of the data for use by other libraries in common forms, such
    as dataframes or series, numpy arrays and simple lists.
    There are also some simple plot methods for plotting graphs.

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

    Together these provide a hierarchy for finite sized files storing
    unbounded time series data for multiple parameters.

    On a personal basis, I use this as efficient storage of time
    series data from my solar inverter, which reports in a slightly
    clunky time limited CSV format; I import those CSVs into
    time series data directories which contain the overall accrued
    data; see my `cs.splink` module which is built on this module.
'''

from abc import ABC, abstractmethod
from array import array, typecodes  # pylint: disable=no-name-in-module
from collections import defaultdict, namedtuple
from contextlib import contextmanager
from datetime import datetime, tzinfo
from fnmatch import fnmatch
from functools import partial
from getopt import GetoptError
from math import nan  # pylint: disable=no-name-in-module
from mmap import (
    mmap,
    ALLOCATIONGRANULARITY,
    MAP_PRIVATE,
    PROT_READ,
    PROT_WRITE,
)  # pylint: disable=no-name-in-module,c-extension-no-member
import os
from os.path import (
    basename,
    dirname,
    exists as existspath,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    splitext,
)
from pprint import pformat
from struct import pack  # pylint: disable=no-name-in-module
from subprocess import run
import sys
from tempfile import TemporaryDirectory
import time
from typing import (
    Callable,
    Iterable,
    List,
    Optional,
    Tuple,
    Union,
)

import arrow
from arrow import Arrow
import dateutil
from icontract import ensure, require, DBC
from matplotlib.figure import Figure
import numpy as np
from numpy import datetime64, timedelta64
from typeguard import typechecked

from cs.binary import BinarySingleStruct, SimpleBinary
from cs.buffer import CornuCopyBuffer
from cs.cmdutils import BaseCommand
from cs.configutils import HasConfigIni
from cs.context import stackattrs
from cs.csvutils import csv_import
from cs.deco import cachedmethod, decorator
from cs.fs import HasFSPath, fnmatchdir, needdir, shortpath
from cs.fstags import FSTags
from cs.lex import is_identifier, s, r
from cs.logutils import warning
from cs.mappings import column_name_to_identifier
from cs.pfx import Pfx, pfx, pfx_call, pfx_method
from cs.progress import progressbar
from cs.py.modules import import_extra
from cs.resources import MultiOpenMixin
from cs.result import CancellationError
from cs.upd import Upd, UpdProxy, print  # pylint: disable=redefined-builtin

__version__ = '20220606-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 3 - Alpha",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'arrow',
        'cs.binary',
        'cs.buffer',
        'cs.cmdutils',
        'cs.configutils>=HasConfigIni',
        'cs.context',
        'cs.csvutils',
        'cs.deco',
        'cs.fs',
        'cs.fstags',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.progress',
        'cs.py.modules',
        'cs.resources',
        'cs.result',
        'cs.upd',
        'icontract',
        'matplotlib',
        'numpy',
        'typeguard',
    ],
    'entry_points': {
        'console_scripts': [
            'csts = cs.timeseries:main',
        ],
    },
    'extras_requires': {
        'pandas': ['pandas'],
    },
}

numeric_types = int, float
Numeric = Union[numeric_types]

def main(argv=None):
  ''' Run the command line tool for `TimeSeries` data.
  '''
  return TimeSeriesCommand(argv).run()

pfx_listdir = partial(pfx_call, os.listdir)
pfx_mkdir = partial(pfx_call, os.mkdir)
pfx_open = partial(pfx_call, open)

class TypeCode(str):
  ''' A valid `array` typecode with convenience methods.
  '''

  TYPES = ('q', int), ('d', float)
  BY_CODE = {code: type_ for code, type_ in TYPES}  # pylint: disable=unnecessary-comprehension
  BY_TYPE = {type_: code for code, type_ in TYPES}
  assert all(map(lambda typecode: typecode in typecodes, BY_CODE.keys()))

  def __new__(cls, t):
    if isinstance(t, str):
      if t not in cls.BY_CODE:
        raise ValueError(
            "invalid typecode %r, I know %r" % (t, sorted(cls.BY_CODE.keys()))
        )
    elif isinstance(t, type):
      try:
        t = cls.BY_TYPE[t]
      except KeyError:
        # pylint: disable=raise-missing-from
        raise ValueError(
            "invalid type %r, I know %r" % (t, sorted(cls.BY_TYPE.keys()))
        )
    else:
      raise TypeError("unsupported type for %s, should be str or type" % r(t))
    return super().__new__(cls, t)

  @classmethod
  def promote(cls, t):
    ''' Promote `t` to a `TypeCode`.
    '''
    if not isinstance(t, cls):
      if isinstance(t, (str, type)):
        t = cls(t)
      else:
        raise TypeError(
            "cannot promote %s to %s, expect str or type" % (r(t), cls)
        )
    return t

  @property
  def type(self):
    ''' The Python type for this `TypeCode`.
    '''
    return self.BY_CODE[self]

  def struct_format(self, bigendian):
    ''' Return a `struct` format string for the supplied big endianness.
    '''
    return ('>' if bigendian else '<') + self

  @property
  def default_fill(self):
    ''' The default fill for the type code.
    '''
    if self == 'd':
      return nan
    if self == 'q':
      return 0
    raise RuntimeError('no default fill value for %r' % (self,))

@typechecked
def deduce_type_bigendianness(typecode: str) -> bool:
  ''' Deduce the native endianness for `typecode`,
      an array/struct typecode character.
  '''
  test_value = TypeCode(typecode).type(1)
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
    for typecode in TypeCode.BY_CODE.keys()
}

DT64_0 = datetime64(0, 's')
TD_1S = timedelta64(1, 's')

def as_datetime64s(times, unit='s'):
  ''' Return a Numpy array of `datetime64` values
      computed from an iterable of `int`/`float` UNIX timestamp values.

      The optional `unit` parameter (default `'s'`) may be one of:
      - `'s'`: seconds
      - `'ms'`: milliseconds
      - `'us'`: microseconds
      - `'ns'`: nanoseconds
      and represents the precision to preserve in the source time
      when converting to a `datetime64`.
      Less precision gives greater time range.
  '''
  try:
    scale = {
        's': int,
        'ms': lambda f: int(f * 1000),
        'us': lambda f: int(f * 1000000),
        'ns': lambda f: int(f * 1000000000),
    }[unit]
  except KeyError:
    # pylint: disable=raise-missing-from
    raise ValueError("as_datetime64s: unhandled unit %r" % (unit,))
  return np.array(list(map(scale, times))).astype(f'datetime64[{unit}]')

def datetime64_as_timestamp(dt64: datetime64):
  ''' Return the UNIX timestamp for the `datetime64` value `dt64`.
  '''
  return dt64 / TD_1S

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

  @abstractmethod
  def cmd_info(self, argv):
    ''' Usage: {cmd}
          Report information.
    '''
    raise NotImplementedError

  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
  def cmd_plot(self, argv):
    ''' Usage: {cmd} [-f] [-o imgpath.png] [--show] [--tz tzspec] days [{{glob|fields}}...]
          Plot the most recent days of data from the time series at tspath.
          Options:
          -f              Force. -o will overwrite an existing image file.
          -o imgpath.png  File system path to which to save the plot.
          --show          Show the image in the GUI.
          --tz tzspec     Skew the UTC times presented on the graph
                          to emulate the timezone spcified by tzspec.
          --stacked       Stack the plot lines/areas.
          glob|fields     If glob is supplied, constrain the keys of
                          a TimeSeriesDataDir by the glob.
    '''
    options = self.options
    runstate = options.runstate
    options.show_image = False
    options.imgpath = None
    options.multi = False
    options.stacked = False
    options.tz = None
    self.popopts(
        argv,
        options,
        f='force',
        multi=None,
        o_='imgpath',
        show='show_image',
        stacked=None,
        tz_=('tz', tzfor),
    )
    force = options.force
    imgpath = options.imgpath
    tz = options.tz
    if imgpath and not force and existspath(imgpath):
      raise GetoptError("imgpath exists: %r" % (imgpath,))
    days = self.poparg(argv, int, "days to display", lambda days: days > 0)
    xit = 0
    now = time.time()
    start = now - days * 24 * 3600
    ts = options.ts
    plot_dx = 14
    plot_dy = 8
    figure = Figure(figsize=(plot_dx, plot_dy), dpi=100)
    figure.add_subplot()
    ax = figure.axes[0]
    plot_kw = {}
    if isinstance(ts, TimeSeries):
      if argv:
        raise GetoptError(
            "fields:%r should not be suppplied for a %s" % (argv, s(ts))
        )
      ax = ts.plot(
          start,
          now,
          ax=ax,
          runstate=runstate,
          tz=tz,
          figsize=(plot_dx, plot_dy),
          **plot_kw
      )  # pylint: disable=missing-kwoa
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
      plot_kw.update(
          stacked=options.stacked,
          subplots=options.multi,
          sharex=options.multi,
      )
      ax = ts.plot(
          start,
          now,
          keys,
          runstate=runstate,
          tz=tz,
          ax=ax,
          **plot_kw,
      )  # pylint: too-many-function-args.disable=missing-kwoa
      figure = (ax[0] if options.multi else ax).figure
    else:
      raise RuntimeError("unhandled type %s" % (s(ts),))
    if runstate.cancelled:
      return 1
    if imgpath:
      save_figure(figure, imgpath, force=force)
    else:
      print_figure(figure)
    if options.show_image:
      figure.show()
    return xit

class TimeSeriesCommand(TimeSeriesBaseCommand):
  ''' Command line interface to `TimeSeries` data files.
  '''

  USAGE_FORMAT = r'''Usage: {cmd} [-s ts-step] tspath subcommand...
    -s ts-step  Specify the UNIX time step for the time series,
                used if the time series is new and checked otherwise.
    tspath      The filesystem path to the time series;
                this may refer to a single .csts TimeSeriesFile, a
                TimeSeriesPartitioned directory of such files, or
                a TimeSeriesDataDir containing partitions for
                multiple keys.'''
  GETOPT_SPEC = 's:'
  SUBCOMMAND_ARGV_DEFAULT = 'info'

  # conversion functions for a date column
  DATE_CONV_MAP = {
      'int': int,
      'float': float,
      'date': lambda d: pfx_call(arrow.get, d, tzinfo='local').timestamp(),
      'iso8601': lambda d: pfx_call(arrow.get, d, tzinfo='local').timestamp(),
  }

  def apply_defaults(self):
    self.options.ts_step = None  # the time series step
    self.options.ts = None

  def apply_opt(self, opt, val):
    if opt == '-s':
      try:
        ts_step = pfx_call(float, val)
      except ValueError as e:
        raise GetoptError("not a floating point value: %s" % (e,)) from e
      if ts_step <= 0:
        raise GetoptError("ts-step must be >0, got %s" % (ts_step,))
      self.options.ts_step = ts_step
    else:
      raise RuntimeError("unhandled option")

  def apply_preargv(self, argv):
    ''' Parse a leading time series filesystem path from `argv`,
        set `self.options.ts` to the time series,
        return modified `argv`.
    '''
    argv = list(argv)
    options = self.options
    if argv and argv[0] in ('test',):
      pass
    else:
      options.ts = self.poparg(
          argv,
          'tspath',
          partial(timeseries_from_path, epoch=options.ts_step),
      )
      if options.ts_step is not None and options.ts.step != options.ts_step:
        warning(
            "tspath step=%s but -s ts-step specified %s", options.ts.step,
            options.ts_step
        )
    return argv

  @contextmanager
  def run_context(self):
    with super().run_context():
      with Upd() as upd:
        with stackattrs(self.options, upd=upd):
          if self.options.ts is None:
            yield
          else:
            with self.options.ts:
              yield

  def cmd_dump(self, argv):
    ''' Usage: {cmd}
          Dump the contents of tspath.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    options = self.options
    ts = options.ts
    if isinstance(ts, TimeSeries):
      npary = ts.as_pd_series()
      print(npary)
    elif isinstance(ts, TimeSeriesMapping):
      df = ts.as_pd_dataframe()
      print(df)
    else:
      raise GetoptError("unhandled time series: %s" % ts)

  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
  def cmd_import(self, argv):
    ''' Usage: {cmd} csvpath datecol[:conv] [import_columns...]
          Import data into the time series.
          csvpath   The CSV file to import.
          datecol[:conv]
                    Specify the timestamp column and optional
                    conversion function.
                    "datecol" can be either the column header name
                    or a numeric column index counting from 0.
                    If "conv" is omitted, the column should contain
                    a UNIX seconds timestamp.  Otherwise "conv"
                    should be either an identifier naming one of
                    the known conversion functions or an "arrow.get"
                    compatible time format string.
          import_columns
                    An optional list of column names or their derived
                    attribute names. The default is to import every
                    numeric column except for the datecol.
    '''
    options = self.options
    runstate = options.runstate
    upd = options.upd
    ts = options.ts
    badopts = False
    csvpath = self.poparg(
        argv,
        'csvpath',
        str,
        lambda csvp: csvp.lower().endswith('.csv') and isfilepath(csvp),
        'not an existing .csv file',
    )
    datecolspec = self.poparg(argv, 'datecol[:conv]', str)
    with Pfx("datecol[:conv] %r", datecolspec):
      try:
        datecol, dateconv = datecolspec.split(':', 1)
      except ValueError:
        datecol, dateconv = datecolspec, float
      else:
        with Pfx("conv %r", dateconv):
          try:
            dateconv = self.DATE_CONV_MAP[dateconv]
          except KeyError:
            if is_identifier(dateconv):
              warning(
                  "unknown conversion function; I know: %s",
                  ", ".join(sorted(self.DATE_CONV_MAP.keys()))
              )
              badopts = True
            else:
              dateconv_format = dateconv
              dateconv = lambda datestr: arrow.get(
                  datestr, dateconv_format, tzinfo='local'
              ).timestamp()
      # see if the column is numeric
      try:
        datecol = int(datecol)
      except ValueError:
        pass
    if badopts:
      raise GetoptError("invalid arguments")
    with Pfx("import %s", shortpath(csvpath)):
      unixtimes = []
      data = defaultdict(list)
      rowcls, rows = csv_import(csvpath, snake_case=True)
      attrlist = rowcls.name_attributes_
      if argv:
        attrindices = list(range(len(rowcls.name_attributes_)))
      else:
        attrindices = [rowcls.index_of_[attr] for attr in argv]
      if isinstance(datecol, int):
        if datecol >= len(rowcls.names_):
          raise GetoptError(
              "date column index %d exceeds the width of the CSV data" %
              (datecol,)
          )
        dateindex = datecol
      else:
        try:
          dateindex = rowcls.index_of_[datecol]
        except KeyError:
          warning(
              "date column %r is not present in the row class, which knows:\n  %s"
              % (datecol, "\n  ".join(sorted(rowcls.index_of_.keys())))
          )
          # pylint: disable=raise-missing-from
          raise GetoptError("date column %r is not recognised" % (datecol,))
      # load the data, store the numeric values
      for i in attrindices:
        if i != dateindex:
          ts.makeitem(attrlist[i])
      for row in progressbar(
          rows,
          "parse " + shortpath(csvpath),
          update_frequency=1024,
          report_print=True,
          runstate=runstate,
      ):
        when = pfx_call(dateconv, row[datecol])
        unixtimes.append(when)
        for i, value in enumerate(row):
          if i == dateindex:
            continue
          attr = attrlist[i]
          try:
            value = int(value)
          except ValueError:
            try:
              value = float(value)
            except ValueError:
              value = None
          data[attrlist[i]].append(value)
      # store the data into the time series
      for attr, values in progressbar(
          sorted(data.items()),
          "set subseries",
          report_print=True,
          runstate=runstate,
      ):
        upd.out("%s: %d values..." % (attr, len(values)))
        with Pfx("%s: store %d values", attr, len(values)):
          ts[attr].setitems(unixtimes, values, skipNone=True)
      if runstate.cancelled:
        return 1
      return 0

  def cmd_info(self, argv):
    ''' Usage: {cmd}
          Report infomation about the time series stored at tspath.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    ts = self.options.ts
    print(ts)
    print(pformat(ts.info_dict(), compact=True))

  # pylint: disable=no-self-use
  def cmd_test(self, argv):
    ''' Usage: {cmd} [testnames...]
          Run some tests of functionality.
    '''

    def test_pandas(tmpdirpath):
      t0 = 1649552238
      fspath = joinpath(tmpdirpath, f'foo--from-{t0}.dat')
      now = time.time()
      ts = TimeSeriesFile(fspath, 'd', epoch=(now, 60))
      ts.pad_to(now + 300)
      print("len(ts.array) =", len(ts.array))
      pds = ts.as_pd_series()
      print(type(pds), pds.memory_usage())
      print(pds)

    # pylint: disable=unused-argument
    def test_partitioned_spans(tmpdirpath):
      # a daily partition with 1 minute time slots
      policy = TimespanPolicyDaily(epoch=60)
      now = time.time()
      start = now
      stop = now + 7 * 24 * 3600
      print("start =", Arrow.fromtimestamp(start))
      print("stop =", Arrow.fromtimestamp(stop))
      prev_stop = None
      for span in policy.partitioned_spans(start, stop):
        print(
            span,
            Arrow.fromtimestamp(span.start),
            Arrow.fromtimestamp(span.stop),
        )
        if prev_stop is not None:
          assert prev_stop == span.start
        prev_stop = span.stop

    def test_datadir(tmpdirpath):
      with TimeSeriesDataDir(
          joinpath(tmpdirpath, 'tsdatadir'),
          policy='daily',
          epoch=30,
      ) as datadir:
        ts = datadir.make_ts('key1')
        ts[time.time()] = 9.0

    # pylint: disable=unused-argument
    def test_timespan_policy(tmpdirpath):
      policy = TimespanPolicyMonthly(epoch=60)
      print(policy.span_for_time(time.time()))

    def test_timeseries(tmpdirpath):
      now = time.time()
      fspath = joinpath(tmpdirpath, 'foo.dat')
      ts = TimeSeriesFile(fspath, 'd', epoch=(now, 1))
      ary = ts.array
      print(ary)
      ts.pad_to(now + 300)
      print(ary)
      ts.save()

    testfunc_map = {
        'datadir': test_datadir,
        'pandas': test_pandas,
        'partitioned_spans': test_partitioned_spans,
        'timeseries': test_timeseries,
        'timespan_policy': test_timespan_policy,
    }

    if not argv:
      argv = sorted(testfunc_map.keys())
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
        with TemporaryDirectory(
            dir='.',
            prefix=f'{self.cmd}--test--{testname}--',
        ) as tmpdirpath:
          testfunc_map[testname](tmpdirpath)

# TODO: accept a `datetime` for `tz`, use its offset for `utcoffset`
@decorator
def plotrange(method, needs_start=False, needs_stop=False):
  ''' A decorator for plotting methods which presents optional
      `start` and `stop` leading positional parameters and optional `tz` or `utcoffset`
      keyword parameters.
      The decorated function will be called with leading `start`
      and `stop` positional parameters and a specific `utcoffset`
      parameter.

      The as-decorated function is called with the following parameters:
      * `start`: an optional UNIX timestamp for the start of the range;
        if omitted the default is `self.start`;
        this is a required parameter if the decorator has `needs_start=True`
      * `stop`: an optional UNIX timestamp for the end of the range;
        if omitted the default is `self.stop`;
        this is a required parameter if the decorator has `needs_stop=True`
      * `tz`: optional timezone `datetime.tzinfo` object or
        specification as for `tzfor()`;
        this is used to infer a UTC offset in seconds
      * `utcoffset`: an offset from UTC time in seconds
      Other parameters are passed through to the deocrated function.

      The decorated method is then called as:

          method(self, start, stop, *a, utcoffset, **kw)

      where `*a` and `**kw` are the additional positional and keyword
      parameters respectively, if any.

      The `utcoffset` is an offset to apply to UTC-based time data
      for _presentation_ on the graph, largely because the plotting
      functions use `DataFrame.plot` which broadly ignores attempts
      to set locators or formatters because it supplies its own.

      If neither `utcoffset` or `tz` is supplied by the caller, the
      `utcoffset` is `0.0`.
      A specified `utcoffset` is passed through.
      A `tz` is promoted to a `tzinfo` instance via the `tzfor()`
      function and applied to the `stop` timestamp to obtain a
      `datetime` from which the `utcoffset` will be derived.
      It is an error to specify both `utcoffset` and `tz`.
  '''

  # pylint: disable=keyword-arg-before-vararg
  @typechecked
  @require(lambda start: not needs_start or start is not None)
  @require(lambda stop: not needs_stop or stop is not None)
  def plotrange_method_wrapper(
      self,
      start: Optional[Numeric] = None,
      stop: Optional[Numeric] = None,
      *a,
      tz: Optional[tzinfo] = None,
      utcoffset: Optional[Numeric] = None,
      **kw,
  ):
    import_extra('pandas', DISTINFO)
    import_extra('matplotlib', DISTINFO)
    if start is None:
      start = self.start
    if stop is None:
      stop = self.stop
    if utcoffset is None:
      if tz is None:
        utcoffset = 0.0
      else:
        tz = tzfor(tz)
        assert isinstance(tz, tzinfo)
        # DF hack: compute the timezone offset for "stop",
        # use it to skew the UNIX timestamps so that UTC tick marks and
        # placements look "local"
        dt = datetime.fromtimestamp(stop, tz=tz)
        utcoffset = tz.utcoffset(dt).total_seconds()
    elif tz is not None:
      raise ValueError(
          "may not supply both utcoffset:%s and tz:%s" % (r(utcoffset), r(tz))
      )

    method.__doc__ += '''

        The `utcoffset` or `tz` parameters may be used to provide
        an offset from UT in seconds for the timestamps _as presented
        on the index/x-axis_. It is an error to specify both.
        Specifying neither uses an offset of `0.0`.

        The `utcoffset` parameter is a plain offset from UTC in seconds.

        The timezone parameter is a little idiosyncratic.
        `DataFrame.plot` _has no timezone support_. It uses its own
        locators and formatters, which render UTC.
        For most scientific data that is a sound practice, so that
        graphs have a common time reference for people in different
        time zones.

        For some data a timezone _is_ relevant, for example my
        originating use case which plots my solar inverter data -
        the curves are correlated with the position of the sun,
        which is closely correlated with the local timezone; for
        this use case `dateutil.tz.tzlocal()` is a good choice.

        When you supply a `tzinfo` object it will be used to compute
        the offset from UTC for the rightmost timestamp on the graph
        (`stop`) and that offset will be applied to all the timestamps
        on the graph.'''
    return method(self, start, stop, *a, utcoffset=utcoffset, **kw)

  return plotrange_method_wrapper

# TODO: optional `utcoffset`/`tz` parameters for presentation
# pylint: disable=too-many-locals
def plot_events(
    ax, events, value_func, *, start=None, stop=None, **scatter_kw
):
  ''' Plot `events`, an iterable of objects with `.unixtime` attributes
      such as an `SQLTagSet`, on an existing set of axes `ax`.

      Parameters:
      * `ax`: axes on which to plot
      * `events`: an iterable of objects with `.unixtime` attributes
      * `value_func`: a callable to compute the y-axis value from an event
      * `start`: optional start UNIX time, used to crop the events plotted
      * `stop`: optional stop UNIX time, used to crop the events plotted
      Other keyword parameters are passed to `Axes.scatter`.
  '''
  xaxis = []
  yaxis = []
  for event in (ev for ev in events
                if (start is None or ev.unixtime >= start) and (
                    stop is None or ev.unixtime < stop)):
    try:
      x = datetime64(int(event.unixtime), 's')
    except ValueError as e:
      warning(
          "cannot convert event.unixtime=%s to datetime64: %s",
          r(event.unixtime), e
      )
      continue
    xaxis.append(x)
    yaxis.append(value_func(event))
  ax.scatter(xaxis, yaxis, **scatter_kw)

def get_default_timezone_name():
  ''' Return the default timezone name.
  '''
  return arrow.now('local').format('ZZZ')

@typechecked
def tzfor(tzspec: Optional[str] = None) -> tzinfo:
  ''' Promote the timezone specification `tzspec` to a `tzinfo` instance.
      If `tzspec` is an instance of `tzinfo` it is returned unchanged.
      If `tzspec` is omitted or the string `'local'` this returns
      `dateutil.tz.gettz()`, the local system timezone.
      Otherwise it returns `dateutil.tz.gettz(tzspec)`.
  '''
  if tzspec is None or tzspec == 'local':
    return dateutil.tz.gettz()
  if isinstance(tzspec, tzinfo):
    return tzspec
  tz = dateutil.tz.gettz(tzspec)
  if tz is None:
    raise ValueError("dateutil.tz.gettz(%r) gave None" % (tzspec,))
  return tz

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

        Example in a `TimeSeries`:

           >>> ts = TimeSeriesFile('tsfile.csts', 'd', epoch=(19.1, 1.2))
           >>> ts.offset(19.1)
           0
           >>> ts.offset(20)
           0
           >>> ts.offset(22)
           2
    '''
    return int((when - self.start) // self.step)

  def when(self, offset):
    ''' Return `self.start+offset*self.step`.
    '''
    return self.start + offset * self.step

  def offset_bounds(self, start, stop) -> (int, int):
    ''' Return the bounds of `(start,stop)` as offsets
        (`self.start` plus multiples of `self.step`).
    '''
    start_offset_steps = self.offset(start)
    end_offset_steps = self.offset(stop)
    if end_offset_steps == start_offset_steps and stop > start:
      end_offset_steps += 1
    return start_offset_steps, end_offset_steps

  def offset_range(self, start, stop):
    ''' Return an iterable of the offsets from `start` to `stop`
        in units of `self.step`
        i.e. `offset(start) == 0`.

        Example in a `TimeSeries`:

           >>> ts = TimeSeriesFile('tsfile.csts', 'd', epoch=(19.1, 1.2))
           >>> list(ts.offset_range(20,30))
           [0, 1, 2, 3, 4, 5, 6, 7, 8]
    '''
    if start < self.start:
      raise IndexError(
          "start:%s must be >= self.start:%s" % (start, self.start)
      )
    if stop < start:
      raise IndexError("start:%s must be <= stop:%s" % (start, stop))
    start_offset_steps, end_offset_steps = self.offset_bounds(start, stop)
    return range(start_offset_steps, end_offset_steps)

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

           >>> ts = TimeSeriesFile('tsfile.csts', 'd', epoch=(19.1, 1.2))
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
    # this would be a Python range but they only work in integers
    return (
        self.start + self.step * offset_step
        for offset_step in self.offset_range(start, stop)
    )

class Epoch(namedtuple('Epoch', 'start step'), TimeStepsMixin):
  ''' The basis of time references with a starting UNIX time `start`
      and the `step` defining the width of a time slot.
  '''

  def __new__(cls, *a, **kw):
    epoch = super().__new__(cls, *a, **kw)
    assert isinstance(epoch.start, (int, float))
    assert isinstance(epoch.step, (int, float))
    assert type(epoch.start) is type(epoch.step), (
        "type(epoch.start):%s is not type(epoch.step):%s" %
        (s(epoch.start), s(epoch.step))
    )
    assert epoch.step > 0
    return epoch

  def info_dict(self, d=None):
    ''' Return an informational `dict` containing salient information
        about this `Epoch`, handy for use with `pformat()` or `pprint()`.
    '''
    if d is None:
      d = {}
    d.update(
        typecode=self.typecode,
        start=self.start,
        start_dt=str(arrow.get(self.start)),
        step=self.step
    )
    return d

  @property
  def typecode(self):
    ''' The `array` typecode for the times from this `Epoch`.
    '''
    return TypeCode(type(self.start))

  @classmethod
  def promote(cls, epochy):
    ''' Promote `epochy` to an `Epoch` (except for `None`).

        `None` remains `None`.

        An `Epoch` remains unchanged.

        An `int` or `float` argument will be used as the `step` in
        an `Epoch` starting at `0`.

        A 2-tuple of `(start,step)` will be used to construct a new `Epoch` directly.
    '''
    if epochy is not None and not isinstance(epochy, Epoch):
      if isinstance(epochy, (int, float)):
        # just the step value, start the epoch at 0
        epochy = 0, epochy
      if isinstance(epochy, tuple):
        start, step = epochy
        if isinstance(start, float) and isinstance(step, int):
          step0 = step
          step = float(step)
          if step != step0:
            raise ValueError(
                "promoted step:%r to float to match start, but new value %r is not equal"
                % (step0, step)
            )
        elif isinstance(start, int) and isinstance(step, float):
          # ints have unbound precision in Python and 63 bits in storage
          # so if we can work in ints, use ints
          step_i = int(step)
          if step_i == step:
            # demote step to an int to match start
            step = step_i
          else:
            # promote start to float to match step
            start0 = start
            start = float(start)
            if start != start0:
              raise ValueError(
                  "promoted start:%r to float to match start, but new value %r is not equal"
                  % (start0, start)
              )
        epochy = cls(start, step)
      else:
        raise TypeError(
            "%s.promote: do not know how to promote %s" %
            (cls.__name__, r(epochy))
        )
    return epochy

Epochy = Union[Epoch, Tuple[Numeric, Numeric], Numeric]
OptionalEpochy = Optional[Epochy]

class HasEpochMixin(TimeStepsMixin):
  ''' A `TimeStepsMixin` with `.start` and `.step` derive from `self.epoch`.
  '''

  def info_dict(self, d=None):
    ''' Return an informational `dict` containing salient information
        about this `HasEpochMixin`, handy for use with `pformat()` or `pprint()`.
    '''
    if d is None:
      d = {}
    d.update(epoch=self.epoch.info_dict())
    return d

  @property
  def start(self):
    ''' The start UNIX time from `self.epoch.start`.
    '''
    return self.epoch.start

  @property
  def step(self):
    ''' The time slot width from `self.epoch.step`.
    '''
    return self.epoch.step

  @property
  def time_typecode(self):
    ''' The `array` typecode for times from `self.epoch`.
    '''
    return self.epoch.typecode

class TimeSeries(MultiOpenMixin, HasEpochMixin, ABC):
  ''' Common base class of any time series.
  '''

  @typechecked
  def __init__(self, epoch: Epoch, typecode: Union[str | TypeCode]):
    typecode = TypeCode.promote(typecode)
    if fill is None:
      fill = typecode.default_fill
    self.epoch = epoch
    self.typecode = typecode
    self.fill = fill

  def info_dict(self, d=None):
    ''' Return an informational `dict` containing salient information
        about this `TimeSeries`, handy for use with `pformat()` or `pprint()`.
    '''
    if d is None:
      d = {}
    d.update(typecode=self.typecode)
    HasEpochMixin.info_dict(self, d)
    return d

  @abstractmethod
  @contextmanager
  def startup_shutdown(self):
    ''' This is required, even if empty.
    '''
    raise NotImplementedError

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

  def data2(self, start, stop):
    ''' Like `data(start,stop)` but returning 2 lists: one of time and one of data.
    '''
    data = list(self.data(start, stop))
    return [d[0] for d in data], [d[1] for d in data]

  def slice(self, start, stop, pad=False, prepad=False):
    ''' Return a slice of the underlying array
        for the times `start:stop`.

        If `stop` implies values beyond the end of the array
        and `pad` is true, pad the resulting list with `self.fill`
        to the expected length.

        If `start` corresponds to an offset before the start of the array
        raise an `IndexError` unless `prepad` is true,
        in which case the list of values will be prepended
        with enough of `self.fill` to reach the array start.
    '''
    astart, astop = self.offset_bounds(start, stop)
    return self.offset_slice(astart, astop, pad=pad, prepad=prepad)

  def offset_slice(self, astart: int, astop: int, pad=False, prepad=False):
    ''' Return a slice of the underlying array
        for the array indices `astart:astop`.

        If `astop` implies values beyond the end of the array
        and `pad` is true, pad the resulting list with `self.fill`
        to the expected length.

        If `astart` is an offset before the start of the array
        raise an `IndexError` unless `prepad` is true,
        in which case the list of values will be prepended
        with enough of `self.fill` to reach the array start.
    '''
    if astart < 0:
      if prepad:
        prepad_len = -astart
      else:
        raise IndexError(
            "%s slice index %s starts at a negative offset" %
            (type(self).__name__, astart)
        )
    else:
      prepad_len = 0
    ary = self.array
    values = ary[astart:astop]
    if prepad_len > 0:
      values[:0] = [self.fill] * prepad_len
    if astop > len(ary) and pad:
      values.extend([self.fill] * (astop - len(ary)))
    return values

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
      stop = self.stop  # pylint: disable=no-member
    return np.array(self[start:stop], self.np_type)

  @pfx
  def as_pd_series(self, start=None, stop=None, utcoffset=None):
    ''' Return a `pandas.Series` containing the data from `start` to `stop`,
        default from `self.start` and `self.stop` respectively.
    '''
    pd = import_extra('pandas', DISTINFO)
    if start is None:
      start = self.start  # pylint: disable=no-member
    if stop is None:
      stop = self.stop  # pylint: disable=no-member
    if utcoffset is None:
      utcoffset = 0.0
    times, data = self.data2(start, stop)
    return pd.Series(
        data, as_datetime64s([t + utcoffset for t in times]), self.np_type
    )

  def update_tag(self, tag_name, new_tag_value):
    ''' Update tag with new value.
    '''
    tag_value = self.tags.get(tag_name)
    if tag_value != new_tag_value:
      warning("%s: %s <= %r, was %r", self, tag_name, new_tag_value, tag_value)
      self.tags[tag_name] = new_tag_value

  @property
  def csv_header(self):
    ''' The value of the `csv.header` tag for this `TimeSeries`, or `None`.
    '''
    return self.tags.get('csv.header')

  @csv_header.setter
  def csv_header(self, new_header):
    ''' Set the `csv.header` tag to `new_header`.
    '''
    self.update_tag('csv.header', new_header)

  @plotrange
  def plot(
      self,
      start,
      stop,
      *,
      label=None,
      runstate=None,  # pylint: disable=unused-argument
      utcoffset,
      **plot_kw,
  ):
    ''' Convenience shim for `DataFrame.plot` to plot data from
        `start` to `stop`.  Return the plot `Axes`.

        Parameters:
        * `start`,`stop`: the time range
        * `label`: optional label for the graph
        * `runstate`: optional `RunState`, ignored in this implementation
        * `utcoffset`: optional timestamp skew from UTC in seconds
        Other keyword parameters are passed to `DataFrame.plot`.
    '''
    pd = import_extra('pandas', DISTINFO)
    if label is None:
      label = "%s[%s:%s]" % (self, arrow.get(start), arrow.get(stop))
    times, yaxis = self.data2(start, stop)
    xaxis = as_datetime64s([t + utcoffset for t in times], 'ms')
    assert len(xaxis) == len(yaxis), (
        "len(xaxis):%d != len(yaxis):%d, start=%s, stop=%s" %
        (len(xaxis), len(yaxis), start, stop)
    )
    df = pd.DataFrame(dict(x=xaxis, y=yaxis))
    return df.plot('x', 'y', title=label, **plot_kw)

class TimeSeriesFileHeader(SimpleBinary, HasEpochMixin):
  ''' The binary data structure of the `TimeSeriesFile` file header.

      This is 24 bytes long and consists of:
      * the 4 byte magic number, `b'csts'`
      * the file bigendian marker, a `struct` byte order indicator
        with a value of `b'>'` for big endian data
        or `b'<'` for little endian data
      * the datum typecode, `b'd'` for double float
        or `b'q'` for signed 64 bit integer
      * the time typecode, `b'd'` for double float
        or `b'q'` for signed 64 bit integer
      * a pad byte, value `b'_'`
      * the start UNIX time, a double float or signed 64 bit integer
        according to the time typecode and bigendian flag
      * the step size, a double float or signed 64 bit integer
        according to the time typecode and bigendian flag

      In addition to the header values tnd methods this also presents:
      * `datum_type`: a `BinarySingleStruct` for the binary form of a data value
      * `time_type`:  a `BinarySingleStruct` for the binary form of a time value
  '''

  MAGIC = b'csts'
  # MAGIC + endian + data type + time type + pad ('_')
  # start time
  # step time
  HEADER_LENGTH = 24

  @typechecked
  @require(lambda typecode: typecode in 'dq')
  def __init__(
      self,
      *,
      bigendian: bool,
      typecode: str,
      epoch: Epoch,
  ):
    super().__init__(
        bigendian=bigendian,
        typecode=typecode,
        epoch=epoch,
    )
    self.datum_type = BinarySingleStruct(
        'Datum', self.struct_endian_marker + self.typecode
    )
    self.time_type = BinarySingleStruct(
        'TimeValue', self.struct_endian_marker + self.time_typecode
    )

  @property
  def struct_endian_marker(self):
    ''' The endianness indicatoe for a `struct` format string.
    '''
    return '>' if self.bigendian else '<'

  @classmethod
  def parse(cls, bfr):
    ''' Parse the header record, return a `TimeSeriesFileHeader`.
    '''
    offset0 = bfr.offset
    magic = bfr.take(4)
    if magic != cls.MAGIC:
      raise ValueError(
          "invalid magic number, expected %r, got %r" % (cls.MAGIC, magic)
      )
    struct_endian_b, typecode_b, time_typecode_b, pad = bfr.take(4)
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
    typecode = TypeCode(chr(typecode_b))
    time_typecode = TypeCode(chr(time_typecode_b))
    if pad != ord('_'):
      warning(
          "ignoring unexpected header pad, expected %r, got %r" % (b'_', pad)
      )
    time_type = BinarySingleStruct(
        'TimeValue', struct_endian_marker + time_typecode
    )
    start = time_type.parse_value(bfr)
    step = time_type.parse_value(bfr)
    assert bfr.offset - offset0 == cls.HEADER_LENGTH
    epoch = Epoch(start, step)
    return cls(
        bigendian=bigendian,
        typecode=typecode,
        epoch=epoch,
    )

  def transcribe(self):
    ''' Transcribe the header record.
    '''
    yield self.MAGIC
    yield b'>' if self.bigendian else b'<'
    yield self.typecode
    yield self.time_typecode
    yield b'_'
    yield self.time_type.transcribe_value(self.start)
    yield self.time_type.transcribe_value(self.step)

# pylint: disable=too-many-instance-attributes,too-many-public-methods
class TimeSeriesFile(TimeSeries, HasFSPath):
  ''' A file containing a single time series for a single data field.

      This provides easy access to a time series data file.
      The instance can be indexed by UNIX time stamp for time based access
      or its `.array` property can be accessed for the raw data.

      The data file itself has a header indicating the file data big endianness,
      the datum type and the time type (both `array.array` type codes).
      Following these are the start and step sizes in the time type format.
      This is automatically honoured on load and save.

      A new file will use the native endianness, but files of other
      endianness are correctly handled, making a `TimeSeriesFile`
      portable between architectures.

      Read only users can just instantiate an instance and access
      its `.array` property, or use the `peek` and `peek_offset` methods.

      Read/write users should use the instance as a context manager,
      which will automatically update the file with the array data
      on exit:

          with TimeSeriesFile(fspath) as ts:
              ... work with ts here ...

      Note that the save-on-close is done with `TimeSeries.flush()`
      which only saves if `self.modified`.
      Use of the `__setitem__` or `pad_to` methods set this flag automatically.
      Direct access via the `.array` will not set it,
      so users working that way for performance should update the flag themselves.

      A `TimeSeriesFile` has two underlying modes of operation:
      in-memory `array.array` mode and direct-to-file `mmap` mode.

      The in-memory mode reads the whole file into an `array.array` instance,
      and all updates then modify the in-memory `array`.
      The file is saved when the context manager exits or when `.save()` is called.
      This maximises efficiency when many accesses are done.

      The `mmap` mode maps the file into memory, and accesses work
      directly against the file contents.
      This is more efficient for just a few accesses,
      but every "write" access (setting a datum) will make the mmapped page dirty,
      causing the OS to queue it for disc.
      This mode is recommended for small accesses
      such as updating a single datum, eg from polling a data source.

      Presently the mode used is triggered by the access method.
      Using the `peek` and `poke` methods uses `mmap` by default.
      Other accesses default to the use the in-memory mode.
      Access to the `.array` property forces use of the `array` mode.
      Poll/update operations should usually choose to use `peek`/`poke`.
  '''

  DOTEXT = '.csts'

  # pylint: disable=too-many-branches,too-many-statements
  @pfx_method
  @typechecked
  def __init__(
      self,
      fspath: str,
      typecode: Optional[str] = None,
      *,
      epoch: OptionalEpochy = None,
      fill=None,
      fstags=None,
  ):
    ''' Prepare a new time series stored in the file at `fspath`
        containing machine native data for the time series values.

        Parameters:
        * `fspath`: the filename of the data file
        * `typecode` optional expected `array.typecode` value of the data;
          if specified and the data file exists, they must match;
          if not specified then the data file must exist
          and the `typecode` will be obtained from its header
        * `epoch`: optional `Epoch` specifying the start time and
          step size for the time series data in the file;
          if not specified then the data file must exist
          and the `epoch` will be obtained from its header
        * `fill`: optional default fill values for `pad_to`;
          if unspecified, fill with `0` for `'q'`
          and `float('nan') for `'d'`
    '''
    epoch = Epoch.promote(epoch)
    HasFSPath.__init__(self, fspath)
    if fstags is None:
      fstags = FSTags()
    self.fstags = fstags
    try:
      header, = TimeSeriesFileHeader.scan_fspath(self.fspath, max_count=1)
    except FileNotFoundError:
      # a missing file is ok, other exceptions are not
      ok = True
      if typecode is None:
        ok = False
        warning("no typecode supplied and no data file %r", fspath)
      if epoch is None:
        ok = False
        warning("no epoch supplied and no data file %r", fspath)
      if not ok:
        raise
      header = TimeSeriesFileHeader(
          bigendian=NATIVE_BIGENDIANNESS[typecode],
          typecode=typecode,
          epoch=epoch,
      )
    else:
      # check the header against supplied parameters
      if typecode is not None and typecode != header.typecode:
        raise ValueError(
            "typecode=%r but data file %s has typecode %r" %
            (typecode, fspath, header.typecode)
        )
      if epoch is not None and epoch.step != header.epoch.step:
        raise ValueError(
            "epoch.step=%s but data file %s has epoch.step %s" %
            (epoch.step, fspath, header.epoch.step)
        )
    self.header = header
    epoch = header.epoch
    typecode = header.typecode
    TimeSeries.__init__(self, epoch, typecode, fill)
    self.fill_bs = header.datum_type.transcribe_value(self.fill)
    self._itemsize = array(typecode).itemsize
    assert self._itemsize == self.header.datum_type.length
    self.modified = False
    # only one of ._array or ._mmap may be not None at a time
    # in memory copy of the file data
    self._array = None
    # mmaped file data
    self._mmap = None
    self._mmap_fd = None
    self._mmap_offset = None
    self._mmap_datum_struct = None

  def __str__(self):
    return "%s(%s,%r,%d:%d,%r)" % (
        type(self).__name__, shortpath(self.fspath), self.typecode, self.start,
        self.step, self.fill
    )

  def info_dict(self, d=None):
    ''' Return an informational `dict` containing salient information
        about this `TimeSeriesFile`, handy for use with `pformat()` or `pprint()`.
    '''
    if d is None:
      d = {}
    d.update(fspath=self.fspath, slots=len(self.array))
    TimeSeries.info_dict(self, d)
    return d

  @contextmanager
  def startup_shutdown(self):
    yield self
    self.flush()

  @property
  def stop(self):
    ''' The end time of this array;
        the UNIX time of the first time slot beyond the end of the array.
    '''
    return self.when(len(self.array))

  def file_offset(self, offset: int) -> int:
    ''' Return the file position for the data with position `offset`.
    '''
    return (
        TimeSeriesFileHeader.HEADER_LENGTH +
        self.header.datum_type.length * offset
    )

  def peek(self, when: Numeric) -> Numeric:
    ''' Read a single data value for the UNIX time `when`.

        This method uses the `mmap` interface if the array is not already loaded.
    '''
    return self.peek_offset(self.offset(when))

  def peek_offset(self, offset: int) -> Numeric:
    ''' Read a single data value from `offset`.

        This method uses the `mmap` interface if the array is not already loaded.
    '''
    if self._array is not None:
      assert self._mmap is None
      return self._array_peek_offset(offset)
    if self._mmap is None:
      self._mmap_open()
    return self._mmap_peek_offset(offset)

  def poke(self, when: Numeric, value: Numeric):
    ''' Write a single data value for the UNIX time `when`.

        This method uses the `mmap` interface if the array is not already loaded.
    '''
    self.poke_offset(self.offset(when), value)

  def poke_offset(self, offset: int, value: Numeric):
    ''' Write a single data value at `offset`.

        This method uses the `mmap` interface if the array is not already loaded.
    '''
    if offset < 0:
      raise ValueError("offset:%d must be >= 0" % (offset,))
    if self._array is not None:
      # array in memory, write to it
      assert self._mmap is None
      self._array_poke_offset(offset, value)
      return
    # save to the mmap
    if self._mmap is None:
      self._mmap_open()
    self._mmap_poke_offset(offset, value)

  @property
  @cachedmethod
  def tags(self):
    ''' The `TagSet` associated with this `TimeSeriesFile` instance.
    '''
    return self.fstags[self.fspath]

  @property
  @cachedmethod
  def array(self):
    ''' The time series as an `array.array` object.
        This loads the array data from `self.fspath` on first use.
    '''
    assert self._array is None
    # we load the data from an mmap
    # ensure we have a current mmap, use it, close it
    if self._mmap is None:
      try:
        self._mmap_open()
      except FileNotFoundError:
        # no file, empty array
        return array(self.typecode)
    else:
      # see if its length is still valid
      flen = os.fstat(self._mmap_fd).st_size
      if flen != len(self._mmap):
        self._mmap_close()
        try:
          self._mmap_open()
        except FileNotFoundError:
          # no file, empty array
          return array(self.typecode)
    mm = self._mmap
    ary = array(self.typecode)
    mv = memoryview(mm)
    ary.frombytes(mv[self._mmap_offset:])
    mv = None  # prompt release of reference
    self._mmap_close()  # release mmap also
    header = self.header
    if header.bigendian != NATIVE_BIGENDIANNESS[header.typecode]:
      ary.byteswap()
    self.modified = False
    return ary

  def _array_peek_offset(self, offset):
    ''' Fetch the datum from the `array` at `offset`.
    '''
    assert offset >= 0
    assert self._array is not None
    try:
      return self.array[offset]
    except IndexError:
      return self.fill

  def _array_poke_offset(self, offset, value):
    ''' Store `value` at `offset` in the `array`.
        Pads with `self.fill` as needed.
    '''
    assert offset >= 0
    assert self._array is not None
    ary = self._array
    if offset >= len(ary):
      ary.extend([self.fill] * (offset - len(ary) + 1))
    ary[offset] = value
    self.modified = True

  def _mmap_open(self):
    ''' Open a `mmap` of the data file.
        This requires the data to not already be loaded into memory as `self._array`.
    '''
    assert self._array is None
    assert self._mmap_fd is None
    assert self._mmap_offset is None
    with Pfx("_mmap_open: fspath %r", self.fspath):
      self._mmap_fd = pfx_call(os.open, self.fspath, os.O_RDWR)
      flen = os.fstat(self._mmap_fd).st_size
      self._mmap = pfx_call(
          mmap, self._mmap_fd, flen, MAP_PRIVATE, PROT_READ | PROT_WRITE
      )
      bfr = CornuCopyBuffer([self._mmap])
      header = self.header = TimeSeriesFileHeader.parse(bfr)
      self._mmap_offset = bfr.offset
      assert self._mmap_offset == header.HEADER_LENGTH
      self._mmap_datum_struct = header.datum_type.struct

  def _mmap_close(self):
    ''' Close the open `mmap` of the data file.
    '''
    assert self._mmap is not None
    assert self._mmap_fd is not None
    assert self._mmap_offset is not None
    assert self._mmap_datum_struct is not None
    self._mmap.close()
    self._mmap = None
    os.close(self._mmap_fd)
    self._mmap_fd = None
    self._mmap_offset = None
    self._mmap_datum_struct = None

  def _mmap_peek_offset(self, offset: int):
    ''' Fetch a datum from the open `mmap` of the data file.
    '''
    assert offset >= 0
    # run as a loop to always recompute the mm* variables
    while True:
      mm = self._mmap
      assert mm is not None
      mm_size = self._mmap_datum_struct.size
      mm_offset = self._mmap_offset + offset * mm_size
      mm_end_offset = mm_offset + mm_size
      if mm_end_offset > len(mm):
        # not within the current mmap
        flen = os.fstat(self._mmap_fd).st_size
        if mm_end_offset > flen:
          # file too short, return the fill value
          return self.fill
        # file has grown, reopen it
        self._mmap_close()
        self._mmap_open()
        flen = os.fstat(self._mmap_fd).st_size
        assert flen >= mm_end_offset
        # retry with the larger mapping
        continue
      # unpack the datum from the mmap
      datum, = self._mmap_datum_struct.unpack_from(mm, mm_offset)
      return datum

  def _mmap_poke_offset(self, offset: int, value):
    ''' Write the datum `value` to the open `mmap` of the data file.
    '''
    assert offset >= 0
    # run as a loop to always recompute the mm* variables
    while True:
      mm = self._mmap
      assert mm is not None
      mm_struct = self._mmap_datum_struct
      mm_size = mm_struct.size
      mm_offset = self._mmap_offset + offset * mm_size
      mm_end_offset = mm_offset + mm_size
      value_bs = mm_struct.pack(value)
      assert len(value_bs) == mm_size
      if mm_end_offset <= len(mm):
        # within the current mmap, save and return
        mm[mm_offset:mm_end_offset] = value_bs
        return
      # not within the current mmap
      flen = os.fstat(self._mmap_fd).st_size
      if mm_end_offset <= flen:
        # file has grown to sufficient size
        self._mmap_close()
        self._mmap_open()
        # retry with the larger mapping
        continue
      # file too short, pad the file and append the value
      self._mmap_close()
      with open(self.fspath, 'r+b' if existspath(self.fspath) else 'wb') as f:
        flen = f.seek(0, os.SEEK_END)
        if flen < mm_offset:
          pad_len = mm_offset - flen
          datum_len = self.header.datum_type.length
          assert pad_len % datum_len == 0
          pad_count = pad_len // datum_len
          assert pad_count > 0
          pad_data = self.header.datum_type.pack(self.fill) * pad_count
          f.write(pad_data)
          assert f.tell() == mm_offset
        f.write(value_bs)
        flen = f.tell()
        assert flen == mm_end_offset
        partial_alloc = flen % ALLOCATIONGRANULARITY
        if partial_alloc > 0:
          # pad to the end of ALLOCATIONGRANULARITY
          pad_len = ALLOCATIONGRANULARITY - partial_alloc
          pad_count = pad_len // datum_len
          if pad_count < 1:
            warning(
                "file length=%d, ALLOCATIONGRANULARITY=%d:"
                " pad_len:%d < datum_len:%d, would overpad - not padding",
                flen, ALLOCATIONGRANULARITY, pad_len, datum_len
            )
          else:
            pad_data = self.header.datum_type.pack(self.fill) * pad_count
            f.write(pad_data)
      return

  def flush(self, keep_array=False):
    ''' Save the data file if `self.modified`.
    '''
    if self.modified:
      self.save()
      self.modified = False
      if not keep_array:
        self._array = None

  def save(self, fspath=None, truncate=False):
    ''' Save the time series to `fspath`, default `self.fspath`.

        *Warning*:
        if the file endianness is not the native endianness,
        the array will be byte swapped temporarily
        during the file write operation.
        Concurrent users should avoid using the array during this function.
    '''
    assert self._array is not None, "array not yet loaded, nothing to save"
    if fspath is None:
      fspath = self.fspath
    self.save_to(fspath, truncate=truncate)

  @pfx_method
  @typechecked
  def save_to(self, fspath: str, truncate=False):
    ''' Save the time series to `fspath`.

        *Warning*:
        if the file endianness is not the native endianness,
        the array will be byte swapped temporarily
        during the file write operation.
        Concurrent users should avoid using the array during this function.

        Note:
        the default behaviour (`truncate=False`) overwrites the data in place,
        leaving data beyond the in-memory array untouched.
        This is more robust against interruptions or errors,
        or updates by other programmes (beyond the in-memory array).
        However, if the file is changing endianness or data type
        (which never happens without deliberate effort)
        this could leave a mix of data, resulting in nonsense
        beyond the in-memory array.
    '''
    assert self._array is not None, "array not yet loaded, nothing to save"
    ary = self.array
    if len(ary) == 0:
      warning("no data, not saving")
      return
    header = self.header
    native_bigendian = NATIVE_BIGENDIANNESS[ary.typecode]
    with pfx_open(
        fspath,
        'wb' if truncate or not existspath(fspath) else 'r+b',
    ) as tsf:
      for bs in header.transcribe_flat():
        tsf.write(bs)
      if header.bigendian != native_bigendian:
        with array_byteswapped(ary):
          ary.tofile(tsf)
      else:
        ary.tofile(tsf)
    tags = self.fstags[fspath]
    tags['start'] = self.epoch.start
    tags['step'] = self.epoch.step
    tags['datatype'] = self.typecode.type.__name__
    tags['timetype'] = type(self.epoch.start).__name__

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

        Example:

           >>> ts = TimeSeriesFile('tsfile.csts', 'd', epoch=(19.1, 1.2))
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

        Example:

           >>> ts = TimeSeriesFile('tsfile.csts', 'd', epoch=(19.1, 1.2))
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

  def array_length(self):
    ''' The length of the time series data,
        from `len(self.array)`.
    '''
    return len(self.array)

  def __getitem__(self, when: Union[Numeric, slice]):
    ''' Return the datum for the UNIX time `when`.

        If `when` is a slice, return a list of the data
        for the times in the range `start:stop`
        as given by `self.range(start,stop)`.
        This will raise an `IndexError` if `start` corresponds to
        an offset before the beginning of the array.
    '''
    if isinstance(when, slice):
      start, stop, step = when.start, when.stop, when.step
      if step is not None:
        raise ValueError(
            "%s index slices may not specify a step" % (type(self).__name__,)
        )
      return self.slice(start, stop)
    # avoid confusion with negative indices
    if when < 0:
      raise ValueError("invalid when:%s, must be >= 0" % (when,))
    return self.peek_offset(self.array_index(when))

  # TODO: if when is a slice, compute whens and call setitems?
  def __setitem__(self, when, value):
    ''' Set the datum for the UNIX time `when`.
    '''
    if when < 0:
      raise ValueError("invalid when:%s, must be >= 0" % (when,))
    self.poke_offset(self.array_index(when), value)

  def setitems(self, whens, values, *, skipNone=False):
    ''' Bulk set values.
    '''
    # ensure we're using array mode
    self.array  # pylint: disable=pointless-statement
    for offset, value in zip(map(self.offset, whens), values):
      if skipNone and value is None:
        continue
      self._array_poke_offset(offset, value)

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

class TimePartition(namedtuple('TimePartition',
                               'epoch name start_offset end_offset'),
                    TimeStepsMixin):
  ''' A `namedtuple` for a slice of time with the following attributes:
      * `epoch`: the reference `Epoch`
      * `name`: the name for this slice
      * `start_offset`: the epoch offset of the start time (`self.start`)
      * `end_offset`: the epoch offset of the end time (`self.stop`)

      These are used by `TimespanPolicy` instances to express the partitions
      into which they divide time.
  '''

  @property
  def start(self):
    ''' The start UNIX time derived from `self.epoch` and `self.start_offset`.
    '''
    return self.epoch.when(self.start_offset)

  @property
  def stop(self):
    ''' The end UNIX time derived from `self.epoch` and `self.end_offset`.
    '''
    return self.epoch.when(self.end_offset)

  @property
  def step(self):
    ''' The epoch step size.
    '''
    return self.epoch.step

  def __contains__(self, when: Numeric) -> bool:
    ''' Test whether the UNIX timestamp `when` lies in this partition.
    '''
    return self.start <= when < self.stop

  def __iter__(self):
    ''' A generator yielding times from this partition from
        `self.start` to `self.stop` by `self.step`.
    '''
    offset = self.start_offset
    epoch = self.epoch
    for offset in self.offsets():
      yield epoch.when(offset)

  def offsets(self):
    ''' Return an iterable of the epoch offsets from `self.start` to `self.stop`.
    '''
    return range(self.start_offset, self.end_offset)

class TimespanPolicy(DBC, HasEpochMixin):
  ''' A class implementing a policy allocating times to named time spans.

      The `TimeSeriesPartitioned` uses these policies
      to partition data among multiple `TimeSeries` data files.

      Probably the most important methods are:
      * `span_for_time`: return a `TimePartition` from a UNIX time
      * `span_for_name`: return a `TimePartition` a partition name
  '''

  # definition to happy linters
  name = None  # subclasses get this when they are registered

  # a not unreasonable default policy name
  DEFAULT_NAME = 'monthly'

  FACTORIES = {}

  @typechecked
  def __init__(self, epoch: Epochy):
    ''' Initialise the policy.
    '''
    epoch = Epoch.promote(epoch)
    self.name = type(self).name
    self.epoch = epoch

  def __str__(self):
    return "%s:%r:%s" % (type(self).__name__, self.name, self.epoch)

  # pylint: disable=keyword-arg-before-vararg
  @classmethod
  @typechecked
  def from_name(
      cls, policy_name: str, epoch: OptionalEpochy = None, **policy_kw
  ):
    ''' Factory method to return a new `TimespanPolicy` instance
        from the policy name,
        which indexes `TimespanPolicy.FACTORIES`.
    '''
    if cls is not TimespanPolicy:
      raise TypeError(
          "TimespanPolicy.from_name is not meaningful from a subclass (%s)" %
          (cls.__name__,)
      )
    epoch = Epoch.promote(epoch)
    policy = cls.FACTORIES[policy_name](epoch=epoch, **policy_kw)
    assert epoch is None or policy.epoch == epoch
    return policy

  @classmethod
  @pfx_method
  @typechecked
  def promote(cls, policy, epoch: OptionalEpochy = None, **policy_kw):
    ''' Factory to promote `policy` to a `TimespanPolicy` instance.

        The supplied `policy` may be:
        * `str`: return an instance of the named policy
        * `TimespanPolicy` subclass: return an instance of the subclass
        * `TimespanPolicy` instance: return the instance
    '''
    if cls is not TimespanPolicy:
      raise TypeError(
          "TimespanPolicy.promote is not meaningful from a subclass (%s)" %
          (cls.__name__,)
      )
    if not isinstance(policy, TimespanPolicy):
      epoch = Epoch.promote(epoch)
      if epoch is None:
        raise ValueError("epoch may not be None if promotion is required")
      if isinstance(policy, str):
        policy = TimespanPolicy.from_name(policy, epoch=epoch, **policy_kw)
      elif isinstance(policy, type) and issubclass(policy, TimespanPolicy):
        policy = policy(epoch=epoch, **policy_kw)
      else:
        raise TypeError(
            "%s.promote: do not know how to promote %s" %
            (cls.__name__, policy)
        )
    assert epoch is None or policy.epoch == epoch
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

  @abstractmethod
  @typechecked
  @ensure(lambda when, result: result[0] <= when < result[1])
  def raw_edges(self, when: Numeric):
    ''' Return the _raw_ start and end UNIX times
        (inclusive and exclusive respectively)
        bracketing the UNIX time `when`.
        This is the core method that a policy must implement.

        These are the direct times implied by the policy.
        For example, with a policy for a calendar month
        this would return the start second of that month
        and the start second of the following month.

        These times are used as the basis for the time slots allocated
        to a particular partition by the `span_for_time(when)` method.
    '''
    raise NotImplementedError

  def span_for_time(self, when):
    ''' Return a `TimePartition` enclosing `when`, a UNIX timestamp.

        The boundaries of the partition are derived from the "raw"
        start and end times returned by the `raw_edges(when)` method,
        but fall on time slot boundaries defined by `self.epoch`.

        Because the raw start/end times will usually fall within a
        time slot instead of exactly on an edge a decision must be
        made as to which partition a boundary slot falls.

        This implementation chooses that the time slot spanning the
        "raw" start second of the partition belongs to that partition.
        As a consequence, the last "raw" seconds of the partition
        will belong to the next partition
        as their time slot overlaps the "raw" start of the next partition.
    '''
    epoch = self.epoch
    raw_start, raw_end = self.raw_edges(when)
    start = epoch.round_down(raw_start)
    end = epoch.round_down(raw_end)
    assert start <= when < end
    name = self.name_for_time(raw_start)
    start_offset = epoch.offset(start)
    end_offset = epoch.offset(end)
    return TimePartition(
        epoch=epoch,
        name=name,
        start_offset=start_offset,
        end_offset=end_offset,
    )

  @abstractmethod
  def span_for_name(self, span_name):
    ''' Return a `TimePartition` derived from the `span_name`.
    '''
    raise NotImplementedError

  @abstractmethod
  def name_for_time(self, when):
    ''' Return a time span name for the UNIX time `when`.
    '''
    raise NotImplementedError

  @require(lambda start, stop: start < stop)
  def partitioned_spans(self, start, stop):
    ''' Generator yielding a sequence of `TimePartition`s covering
        the range `start:stop` such that `start` falls within the first
        partition.

        Note that these partitions fall in the policy partitions,
        but are bracketed by `[round_down(start):stop]`.
        As such they will have the correct policy partition names
        but the boundaries of the first and last spans
        start at `round_down(start)` and end at `stop` respectively.
        This makes the returned spans useful for time ranges from a subseries.
    '''
    epoch = self.epoch
    when = start
    while when < stop:
      span = self.span_for_time(when)
      start_offset = epoch.offset(max(span.start, when))
      end_offset = epoch.offset(min(span.stop, stop))
      yield TimePartition(
          epoch=epoch,
          name=span.name,
          start_offset=start_offset,
          end_offset=end_offset,
      )
      when = span.stop

  def spans_for_times(self, whens):
    ''' Generator yielding `(when,TimePartition)` for each UNIX
        time in the iterabe `whens`.
        This is most efficient if times for a particular span are adjacent,
        trivially so if the times are ordered.
    '''
    span = None
    for when in whens:
      if span is not None and when in span:
        span = None
      if span is None:
        span = self.span_for_time(when)
      yield when, span

class ArrowBasedTimespanPolicy(TimespanPolicy):
  ''' A `TimespanPolicy` based on an Arrow format string.

      See the `raw_edges` method for the specifics of how these are defined.
  '''

  # this must be an Arrow format string used as the basis of the
  # partition names and edge computations
  PARTITION_FORMAT = None

  # this must be a dict holding parameters for Arrow.shift()
  # this definition is mostly to happy linters
  ARROW_SHIFT_PARAMS = None

  @typechecked
  def __init__(self, epoch: Epochy, *, tz: Optional[str] = None):
    super().__init__(epoch)
    if tz is None:
      tz = get_default_timezone_name()
    self.tz = tzfor(tz)

  def __str__(self):
    return "%s:%s" % (super().__str__(), self.tz)

  def Arrow(self, when):
    ''' Return an `arrow.Arrow` instance for the UNIX time `when`
        in the policy timezone.
    '''
    return arrow.Arrow.fromtimestamp(when, tzinfo=self.tz)

  # pylint: disable=no-self-use
  def partition_format_cononical(self, txt):
    ''' Modify the formatted text derived from `self.PARTITION_FORMAT`.

        The driving example is the 'weekly' policy, which uses
        Arrow's 'W' ISO week format but trims the sub-week day
        suffix.  This is sufficient if Arrow can parse the trimmed
        result, which it can for 'W'. If not, a subclass might need
        to override this method.
    '''
    return txt

  @typechecked
  def _arrow_name(self, a: Arrow):
    ''' Compute the partition name from an `Arrow` instance.
    '''
    name = a.format(self.PARTITION_FORMAT)
    post_fn = self.partition_format_cononical
    if post_fn is not None:
      name = post_fn(name)
    return name

  def name_for_time(self, when):
    ''' Return a time span name for the UNIX time `when`.
    '''
    return self._arrow_name(self.Arrow(when))

  @pfx_method
  def span_for_name(self, span_name: str):
    ''' Return a `TimePartition` derived from the `span_name`.
    '''
    a = arrow.get(span_name, self.PARTITION_FORMAT, tzinfo=self.tz)
    return self.span_for_time(a.timestamp())

  @typechecked
  @ensure(lambda when, result: result[0] <= when < result[1])
  def raw_edges(self, when: Numeric):
    ''' Return the _raw_ start and end UNIX times
        (inclusive and exclusive respectively)
        bracketing the UNIX time `when`.

        This implementation performs the following steps:
        * get an `Arrow` instance in the policy timezone from the
          UNIX time `when`
        * format that instance using `self.PARTITION_FORMAT`,
          modified by `self.partition_format_cononical`
        * parse that string into a new `Arrow` instance which is
          the raw start time
        * compute the raw end time as `calendar_start.shift(**self.ARROW_SHIFT_PARAMS)`
        * return the UNIX timestamps for the raw start and end times
    '''
    a = self.Arrow(when)
    name = self._arrow_name(a)
    calendar_start = pfx_call(arrow.get, name, tzinfo=self.tz)
    calendar_end = calendar_start.shift(**self.ARROW_SHIFT_PARAMS)
    raw_start = calendar_start.timestamp()
    raw_end = calendar_end.timestamp()
    assert raw_start < raw_end
    return raw_start, raw_end

  @classmethod
  def make(cls, name, partition_format, shift):
    ''' Create and register a simple `ArrowBasedTimespanPolicy`.
        Return the new policy.

        Parameters:
        * `name`: the name for the policy; this can also be a sequence of names
        * `partition_format`: the Arrow format string for naming time partitions
        * `shift`: a mapping of parameter values for `Arrow.shift()`
          defining the time step from one partition to the next
    '''
    if isinstance(name, str):
      names = (name,)
    else:
      names = name
    if isinstance(partition_format, str):
      post_format = None
    else:
      # Arrow time format, function to process the result
      partition_format, post_format = partition_format

    class _Policy(cls):

      PARTITION_FORMAT = partition_format
      ARROW_SHIFT_PARAMS = shift

      if post_format is not None:

        def partition_format_cononical(self, txt):
          return post_format(txt)

        partition_format_cononical.__doc__ = cls.partition_format_cononical.__doc__

    _Policy.__name__ = f'{names[0].title()}{cls.__name__}'
    _Policy.__doc__ = (
        f'A {names[0]} time policy.\n'
        f'PARTITION_FORMAT = {partition_format!r}\n'
        f'ARROW_SHIFT_PARAMS = {shift!r}'
    )
    for policy_name in names:
      TimespanPolicy.register_factory(_Policy, policy_name)
    return _Policy

# prepare some standard convenient policies
TimespanPolicyDaily = ArrowBasedTimespanPolicy.make(
    'daily', 'YYYY-MM-DD', dict(days=1)
)
TimespanPolicyWeekly = ArrowBasedTimespanPolicy.make(
    'weekly',
    ('W', lambda wtxt: '-'.join(wtxt.split('-')[:2])),
    dict(weeks=1),
)
TimespanPolicyMonthly = ArrowBasedTimespanPolicy.make(
    'monthly', 'YYYY-MM', dict(months=1)
)
TimespanPolicyAnnual = TimespanPolicyYearly = ArrowBasedTimespanPolicy.make(
    ('annual', 'yearly'), 'YYYY', dict(years=1)
)

class TimeSeriesMapping(dict, MultiOpenMixin, HasEpochMixin, ABC):
  ''' A group of named `TimeSeries` instances, indexed by a key.

      This is the basis for `TimeSeriesDataDir`.
  '''

  DEFAULT_POLICY_NAME = 'monthly'

  @typechecked
  def __init__(
      self,
      *,
      epoch: Epoch,
      policy=None,  # :TimespanPolicy
      tz: Optional[str] = None,
  ):
    super().__init__()
    self.epoch = epoch
    if policy is None or isinstance(policy, str):
      policy_name = policy or self.DEFAULT_POLICY_NAME
      policy = TimespanPolicy.from_name(
          policy_name,
          epoch=self.epoch,
          tz=tz,
      )
    elif tz is not None:
      raise ValueError(
          "may not provide both tz:%s and a TimespanPolicy:%s", s(tz),
          s(policy)
      )
    self.policy = policy
    self._rules = {}

  def __str__(self):
    return "%s(%s,%s)" % (
        type(self).__name__,
        getattr(self, 'epoch', 'STEP_UNDEFINED'),
        getattr(self, 'policy', 'POLICY_UNDEFINED'),
    )

  @abstractmethod
  def shortname(self):
    ''' Return a short identifying name for this `TimeSeriesMapping`.
        For example, `TimeSeriesDataDir` returns `self.shortpath`
        for this function.
    '''
    raise NotImplementedError

  def info_dict(self, d=None):
    ''' Return an informational `dict` containing salient information
        about this `TimeSeriesMapping`, handy for use with `pformat()` or `pprint()`.
    '''
    if d is None:
      d = {}
    keys = sorted(self.keys())
    d.update(keys=keys, subseries={key: self[key].info_dict() for key in keys})
    return d

  @contextmanager
  def startup_shutdown(self):
    ''' Context manager for `MultiOpenMixin`.
        Close the sub time series.
    '''
    try:
      yield
    finally:
      for key, ts in list(self.items()):
        ts.close()
        del self[key]

  @abstractmethod
  def make_ts(self, key):
    ''' Return the `TimeSeries` for `key`,
        creating it if necessary.
    '''
    raise NotImplementedError

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
    raise KeyError(
        "%s[%r]: no entry for key and no implied time series" % (
            type(self).__name__,
            key,
        )
    )

  @typechecked
  def __setitem__(self, key: str, ts):
    ''' Insert a time series into this `TimeSeriesMapping`.
        `key` may not already be present.
    '''
    self.validate_key(key)
    if key in self:
      raise ValueError("key already exists: %r" % (key,))
    ts.open()
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
      keys: Optional[Iterable[str]] = None,
      *,
      key_map=None,
      runstate=None,
      utcoffset=None,
  ):
    ''' Return a `numpy.DataFrame` containing the specified data.

        Parameters:
        * `start`: start time of the data
        * `stop`: end time of the data
        * `keys`: optional iterable of keys, default from `self.keys()`
    '''
    pd = import_extra('pandas', DISTINFO)
    if start is None:
      start = self.start  # pylint: disable=no-member
    if stop is None:
      stop = self.stop  # pylint: disable=no-member
    if keys is None:
      keys = self.keys()
    if not isinstance(keys, (tuple, list)):
      keys = tuple(keys)
    if key_map is None:
      key_map = {}
    if utcoffset is None:
      utcoffset = 0.0
    indices = as_datetime64s([t + utcoffset for t in self.range(start, stop)])
    data_dict = {}
    with UpdProxy(prefix="gather fields: ") as proxy:
      for key in progressbar(keys, "gather fields"):
        if runstate and runstate.cancelled:
          raise CancellationError
        proxy.text = key
        with Pfx(key):
          if key not in self:
            raise KeyError("no such key")
          data_key = key_map.get(key, key)
          data_dict[data_key] = self[key].as_pd_series(
              start, stop, utcoffset=utcoffset
          )
    if runstate and runstate.cancelled:
      raise CancellationError
    return pfx_call(
        pd.DataFrame,
        data=data_dict,
        index=indices,
        copy=False,
    )

  @typechecked
  def csv_header(self, key: str) -> str:
    ''' Return the CSV header name for `key`.
    '''
    return self[key].csv_header or key

  def to_csv(
      self,
      start,
      stop,
      f,
      *,
      columns=None,
      key_map=None,
      df_mangle=None,
      **to_csv_kw,
  ):
    ''' Return `pandas.DataFrame.to_csv()` for the data between `start` and `stop`.
    '''
    if columns is None:
      columns = sorted(self.keys())
    elif not isinstance(columns, (list, tuple)):
      columns = list(columns)
    if key_map is None:
      key_map = {column: self.csv_header(column) for column in columns}
    df = self.as_pd_dataframe(start, stop, columns, key_map=key_map)
    if df_mangle:
      df_mangle(df)
    df.to_csv(f, **to_csv_kw)

  @plotrange
  def plot(
      self,
      start,
      stop,
      keys=None,
      *,
      label=None,
      runstate=None,
      utcoffset,
      **plot_kw
  ):
    ''' Convenience shim for `DataFrame.plot` to plot data from
        `start` to `stop` for each key in `keys`.
        Return the plot `Axes`.

        Parameters:
        * `start`: optional start, default `self.start`
        * `stop`: optional stop, default `self.stop`
        * `keys`: optional list of keys, default all keys
        * `label`: optional label for the graph
        Other keyword parameters are passed to `DataFrame.plot`.
    '''
    if keys is None:
      keys = sorted(self.keys())
    df = self.as_pd_dataframe(
        start,
        stop,
        keys,
        runstate=runstate,
        utcoffset=utcoffset,
    )
    for key in keys:
      with Pfx(key):
        ts = self[key]
        csv_header = self.csv_header(key)
        if csv_header != key:
          kname = f'{csv_header}\n{key}'
          df.rename(columns={key: kname}, inplace=True)
    if runstate and runstate.cancelled:
      raise CancellationError
    return df.plot(**plot_kw)

  @pfx_method
  def read_csv(self, csvpath, column_name_map=None, **pd_read_csv_kw):
    ''' Shim for `pandas.read_csv` to read a CSV file and save the contents
        in this `TimeSeriesMapping`.
        Return the `DataFrame` used for the import.

        Parameters:
        * `csvpath`: the filesystem path of the CSV file to read,
          passed to `pandas.read_csv`
        * `column_name_map`: an optional rename mapping for column names
          as detailed below
        * `pd_read_csv_kw`: other keyword arguments are passed to
          `pandas.read_csv`

        The `column_name_map` may have the following values:
        * `None`: the default, which renames columns using the
          `column_name_to_identifier` function from `cs.mappings` to
          create identifiers from column names
        * `id`: the builtin `id` function, which leaves column names unchanged
        * a `bool`: use `column_name_to_identifier` with
          its `snake_case` parameter set to `column_name_map`
        * a `callable`: compute the renamed column name from
          `column_name_map(column_name)`
        * otherwise assume `column_name_map` is a mapping and compute
          the renamed column name as
          `column_name_map.get(column_name,column_name)`
    '''
    pd = import_extra('pandas', DISTINFO)
    df = pfx_call(pd.read_csv, csvpath, **pd_read_csv_kw)
    # prepare column renames
    renamed = {}
    if column_name_map is not id:
      if column_name_map is None:
        column_name_map = column_name_to_identifier
      elif column_name_map is id:
        column_name_map = None
      elif isinstance(column_name_map, bool):
        column_name_map = partial(
            column_name_to_identifier, snake_case=column_name_map
        )
      elif callable(column_name_map):
        pass
      else:
        # a mapping
        column_name_map = lambda column_name: column_name_map.get(
            column_name, column_name
        )
      for column_name in df.columns:
        new_column_name = column_name_map(column_name)
        if new_column_name != column_name:
          renamed[column_name] = new_column_name
    if renamed:
      df.rename(columns=renamed, inplace=True, errors='raise')
    former_names = {
        new_name: former_name
        for former_name, new_name in renamed.items()
    }
    with Upd().insert(1) as proxy:
      proxy.prefix = f'update {self.shortname()}: '
      for column_name in df.columns:
        with Pfx(column_name):
          proxy.text = column_name
          series = df[column_name]
          ts = self.make_ts(column_name)
          proxy.prefix = f'update {ts.shortpath}: '
          ts.setitems(series.index, series.values)
          former_name = former_names.get(column_name)
          if former_name:
            ts.csv_header = former_name
    return df, renamed

# pylint: disable=too-many-ancestors
class TimeSeriesDataDir(TimeSeriesMapping, HasFSPath, HasConfigIni,
                        HasEpochMixin):
  ''' A directory containing a collection of `TimeSeriesPartitioned` subdirectories.
  '''

  # hard wired to avoid confusion in subclasses,
  # particularly we do not want to create the data dir with a
  # subclass and then not find the config reading with the generic
  # class
  CONFIG_SECTION_NAME = 'TimeSeriesDataDir'

  # pylint: disable=too-many-branches,too-many-statements
  @pfx_method
  @typechecked
  def __init__(
      self,
      fspath,
      *,
      epoch: OptionalEpochy = None,
      policy=None,  # :TimespanPolicy
      tz: Optional[str] = None,
      fstags: Optional[FSTags] = None,
  ):
    HasConfigIni.__init__(self, self.CONFIG_SECTION_NAME)
    HasFSPath.__init__(self, fspath)
    if fstags is None:
      fstags = FSTags()
    self.fstags = fstags
    config = self.config
    if not isdirpath(fspath):
      # new data dir, create it and save config
      pfx_call(needdir, fspath)
      epoch = Epoch.promote(epoch)
      config.start = epoch.start
      config.step = epoch.step
    else:
      # existing data dir, check params against config, fill in
      # gaps in config
      cfg_start = config.start
      cfg_step = config.step
      if epoch is None:
        epoch = Epoch(cfg_start, cfg_step)
      else:
        epoch = Epoch.promote(epoch)
        if cfg_start is not None and cfg_start != epoch.start:
          raise ValueError(
              "config.start:%s != epoch,start:%s" %
              (s(cfg_start), s(epoch.start))
          )
        if cfg_step is not None and cfg_step != epoch.step:
          raise ValueError(
              "config.step:%s != epoch,step:%s" % (s(cfg_step), s(epoch.step))
          )
    self.epoch = epoch
    if policy is None:
      policy_name = config.auto.policy.name or TimespanPolicy.DEFAULT_NAME
      policy = TimespanPolicy.from_name(policy_name, epoch=epoch)
    else:
      policy = TimespanPolicy.promote(policy, epoch=epoch)
      policy_name = policy.name
    # fill in holes in the config
    if not config.auto.policy.name:
      config['policy.name'] = policy_name
    if not config.auto.policy.tz:
      config['policy.tz'] = str(tz)
    TimeSeriesMapping.__init__(self, epoch=epoch, policy=policy, tz=tz)
    self._infill_keys_from_subdirs()
    self.config_flush()

  def __str__(self):
    return "%s(%s,%s,%s)" % (
        type(self).__name__,
        self.shortpath,
        getattr(self, 'step', 'STEP_UNDEFINED'),
        getattr(self, 'policy', 'POLICY_UNDEFINED'),
    )

  __repr__ = __str__

  def shortname(self):
    ''' Return `self.shortpath`.
    '''
    return self.shortpath

  def info_dict(self, d=None):
    ''' Return an informational `dict` containing salient information
        about this `TimeSeriesDataDir`, handy for use with `pformat()` or `pprint()`.
    '''
    if d is None:
      d = {}
    d.update(fspath=self.fspath)
    d.update(config=HasConfigIni.info_dict(self))
    TimeSeriesMapping.info_dict(self, d)
    return d

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

  def make_ts(self, key):
    ''' Create a `TimeSeriesPartitioned` for `key`.
    '''
    if key not in self:
      self[key] = self._tsfactory(key)
    return self[key]

  def _tsfactory(self, key):
    ''' Create a `TimeSeriesPartitioned` for `key`.
    '''
    self.validate_key(key)
    keypath = self.pathto(key)
    needdir(keypath)
    ts = TimeSeriesPartitioned(
        keypath,
        self.key_typecode(key),
        policy=self.policy,
        fstags=self.fstags,
    )
    ts.tags['key'] = key
    ts.tags['step'] = ts.step
    ts.tags['typecode'] = ts.typecode
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
    ''' The `policy.name` config value, usually a key from
        `TimespanPolicy.FACTORIES`.
    '''
    name = self.config.auto.policy.name
    if not name:
      name = TimespanPolicy.DEFAULT_NAME
      self.policy_name = name
    return name

  @policy_name.setter
  def policy_name(self, new_policy_name: str):
    ''' Set the `policy.name` config value, usually a key from
        `TimespanPolicy.FACTORIES`.
    '''
    self.config['policy.name'] = new_policy_name

  @property
  def tz(self):
    ''' The `policy.tz` config value, a timezone name.
    '''
    return self.policy.tz

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
    return ks

class TimeSeriesPartitioned(TimeSeries, HasFSPath):
  ''' A collection of `TimeSeries` files in a subdirectory.
      We have one of these for each `TimeSeriesDataDir` key.

      This class manages a collection of files
      named by the partition from a `TimespanPolicy`,
      which dictates which partition holds the datum for a UNIX time.
  '''

  @typechecked
  def __init__(
      self,
      dirpath: str,
      typecode: str,
      *,
      epoch: OptionalEpochy = None,
      policy,  # :TimespanPolicy,
      fstags: Optional[FSTags] = None,
  ):
    ''' Initialise the `TimeSeriesPartitioned` instance.

        Parameters:
        * `dirpath`: the directory filesystem path,
          known as `.fspath` within the instance
        * `typecode`: the `array` type code for the data
        * `epoch`: the time series `Epoch`
        * `policy`: the partitioning `TimespanPolicy`

        The instance requires a reference epoch
        because the `policy` start times will almost always
        not fall on exact multiples of `epoch.step`.
        The reference allows for reliable placement of times
        which fall within `epoch.step` of a partition boundary.
        For example, if `epoch.start==0` and `epoch.step==6` and a
        partition boundary came at `19` due to some calendar based
        policy then a time of `20` would fall in the partion left
        of the boundary because it belongs to the time slot commencing
        at `18`.

        If `epoch` or `typecode` are omitted the file's
        fstags will be consulted for their values.
        The `start` parameter will further fall back to `0`.
        This class does not set these tags (that would presume write
        access to the parent directory or its `.fstags` file)
        when a `TimeSeriesPartitioned` is made by a `TimeSeriesDataDir`
        instance it sets these flags.
    '''
    epoch = Epoch.promote(epoch)
    policy = TimespanPolicy.promote(policy, epoch)
    HasFSPath.__init__(self, dirpath)
    if fstags is None:
      fstags = FSTags()
    if typecode is None:
      typecode = TypeCode(self.tags.typecode)
    else:
      typecode = TypeCode.promote(typecode)
    policy = TimespanPolicy.promote(policy, epoch=epoch)
    assert isinstance(policy, ArrowBasedTimespanPolicy)
    TimeSeries.__init__(self, policy.epoch, typecode)
    self.policy = policy
    self.fstags = fstags
    self._ts_by_partition = {}

  def __str__(self):
    return "%s(%s,%r,%s,%s)" % (
        type(self).__name__,
        shortpath(self.fspath),
        getattr(self, 'typecode', 'NO_TYPECODE_YET'),
        getattr(self, 'epoch', 'NO_EPOCH_YET'),
        getattr(self, 'policy', 'NO_POLICY_YET'),
    )

  __repr__ = __str__

  def info_dict(self, d=None):
    ''' Return an informational `dict` containing salient information
        about this `TimeSeriesPartitioned`, handy for use with `pformat()` or `pprint()`.
    '''
    if d is None:
      d = {}
    filenames = sorted(self.tsfilenames())
    d.update(
        typecode=self.typecode,
        time_typecode=self.time_typecode,
        epoch_start=self.epoch.start,
        epoch_step=self.epoch.step,
        filenames=filenames,
        partitions={
            span_name: self.subseries(span_name).info_dict()
            for span_name in map(self.partition_name_from_filename, filenames)
        },
    )
    return d

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
  def subseries(self, spec: Union[str, Numeric]):
    ''' Return the `TimeSeries` for `spec`,
        which may be a partition name or a UNIX time.
    '''
    if isinstance(spec, str):
      # partition name
      span = self.policy.span_for_name(spec)
    else:
      # numeric UNIX time
      span = self.policy.span_for_time(spec)
    assert span.step == self.step
    return self.timeseriesfile_from_partition_name(span.name)

  def __getitem__(self, index: Union[Numeric, slice, str]):
    ''' Obtain various things from this `TimeSeriesPartitioned`
        according to the type of `index`:
        * `int` or `float`: the value for the UNIX timestamp `index`
        * `slice`: a list of the values for the UNIX timestamp slice `index`
        * `*.csts`: the `TimeSeriesFile` named `index` within this
          `TimeSeriesPartitioned`
        * partition name: the `TimeSeriesFile` for the policy time partition
    '''
    if isinstance(index, numeric_types):
      # UNIX timestamp
      span = self.policy.span_for_time(index)
      tsf = self.timeseriesfile_from_partition_name(span.name)
      return tsf[index]
    if isinstance(index, slice):
      # slice of UNIX timestamps
      if index.step is not None and index.step != self.step:
        raise IndexError(
            "slice.step:%r should be None or ==self.step:%r" %
            (index.step, self.step)
        )
      values = []
      for span in self.policy.partitioned_spans(index.start, index.stop):
        ts = self.timeseriesfile_from_partition_name(span.name)
        ts_values = ts[span.start:span.stop]
        values.extend(ts_values)
        difflen = (span.end_offset - span.start_offset) - len(ts_values)
        if difflen > 0:
          warning(
              "span:%s:%s: %d values, pad with %d fill values",
              span.start,
              span.stop,
              len(ts_values),
              difflen,
          )
          values.extend([ts.fill] * difflen)
        else:
          assert difflen == 0, "difflen should be 0, but is %r" % difflen
      return values
    if isinstance(index, str):
      if index.endswith(TimeSeriesFile.DOTEXT):
        # a .csts filename
        partition_name = self.partition_name_from_filename(index)
        return self.timeseriesfile_from_partition_name(partition_name)
      # a partition name
      return self.timeseriesfile_from_partition_name(index)
    raise TypeError("invalid type for index %s" % r(index))

  def __setitem__(self, when: Numeric, value):
    self.subseries(when)[when] = value

  def tsfilenames(self):
    ''' Return a list of the time series data filenames.
    '''
    return self.fnmatch('*' + TimeSeriesFile.DOTEXT)

  def timeseriesfiles(self):
    ''' Return a mapping of partition name to associated `TimeSeriesFile`
        for the existing time series data files.
    '''
    timeseriesfiles = {}
    for filename in self.tsfilenames():
      partition_name = self.partition_name_from_filename(filename)
      tsf = self.timeseriesfile_from_partition_name(partition_name)
      assert partition_name not in timeseriesfiles
      timeseriesfiles[partition_name] = tsf
    return timeseriesfiles

  def timeseriesfile_from_partition_name(self, partition_name):
    ''' Return the `TimeSeriesFile` associated with the supplied partition_name.
    '''
    partition_span = self.policy.span_for_name(partition_name)
    try:
      ts = self._ts_by_partition[partition_span.name]
    except KeyError:
      tsepoch = Epoch(partition_span.start, partition_span.step)
      filepath = self.pathto(partition_span.name + TimeSeriesFile.DOTEXT)
      ts = self._ts_by_partition[partition_span.name] = TimeSeriesFile(
          filepath,
          self.typecode,
          epoch=tsepoch,
      )
      ts.epoch = tsepoch
      ts.partition_span = partition_span  # pylint: disable=attribute-defined-outside-init
      ts.tags['partition'] = partition_span.name
      ts.tags['start'] = partition_span.start
      ts.tags['stop'] = partition_span.stop
      ts.tags['step'] = self.step
      ts.open()
    return ts

  @property
  def start(self):
    ''' The earliest time in any component `TimeSeriesFile`.
    '''
    tsf_map = self.timeseriesfiles()
    if not tsf_map:
      return None
    return min(tsf.start for tsf in tsf_map.values())

  @property
  def stop(self):
    ''' The latest time in any component `TimeSeriesFile`.
    '''
    tsf_map = self.timeseriesfiles()
    if not tsf_map:
      return None
    return max(tsf.stop for tsf in tsf_map.values())

  @staticmethod
  def partition_name_from_filename(tsfilename: str) -> str:
    ''' Return the time span name from a `TimeSeriesFile` filename.
    '''
    name, ext = splitext(basename(tsfilename))
    if ext != TimeSeriesFile.DOTEXT:
      raise ValueError(
          "expected extension %r, got %r; tsfilename=%r" %
          (TimeSeriesFile.DOTEXT, ext, tsfilename)
      )
    return name

  def partition(self, start, stop):
    ''' Return an iterable of `(when,subseries)` for each time `when`
        from `start` to `stop`.
    '''
    ts = None
    span = None
    for when in self.range(start, stop):
      if span is not None and when not in span:
        # different range, invalidate the current bounds
        span = None
      if span is None:
        ts = self.subseries(when)
        span = ts.span
      yield when, ts

  def setitems(self, whens, values, *, skipNone=False):
    ''' Store `values` against the UNIX times `whens`.

        This is most efficient if `whens` are ordered.
    '''
    ts = None
    span = None
    when_group, value_group = None, None
    for when, value in zip(whens, values):
      if skipNone and value is None:
        continue
      if span is None or when not in span:
        # flush data to the current time series
        if ts is not None:
          ts.setitems(when_group, value_group, skipNone=skipNone)
        when_group, value_group = [], []
        # new partition required, sets ts as well
        ts = self.subseries(when)
        span = ts.partition_span
      when_group.append(when)
      value_group.append(value)
    if ts is not None:
      # flush data to the current time series
      ts.setitems(when_group, value_group, skipNone=skipNone)

  def partitioned_spans(self, start, stop):
    ''' Generator yielding a sequence of `TimePartition`s covering
        the range `start:stop` such that `start` falls within the first
        partition via `self.policy`.
    '''
    return self.policy.partitioned_spans(start, stop)

  def data(self, start, stop):
    ''' Return a list of `(when,datum)` tuples for the slot times from `start` to `stop`.
    '''
    xydata = []
    for span in self.partitioned_spans(start, stop):
      ts = self.subseries(span.name)
      xydata.extend(ts.data(span.start, span.stop))
    return xydata

  @plotrange
  def plot(self, start, stop, *, label=None, runstate=None, **plot_kw):
    ''' Convenience shim for `DataFrame.plot` to plot data from
        `start` to `stop`.  Return the plot `Axes`.

        Parameters:
        * `start`,`stop`: the time range
        * `ax`: optional `Axes`; new `Axes` will be made if not specified
        * `label`: optional label for the graph
        Other keyword parameters are passed to `Axes.plot`
        or `DataFrame.plot` for new axes.
    '''
    if label is None:
      label = self.tags.get('csv.header')
    return super().plot(start, stop, label=label, runstate=runstate, **plot_kw)

@typechecked
def timeseries_from_path(
    tspath: str, epoch: OptionalEpochy = None, typecode=None
):
  ''' Turn a time series filesystem path into a time series:
      * a file: a `TimeSeriesFile`
      * a directory holding `.csts` files: a `TimeSeriesPartitioned`
      * a directory: a `TimeSeriesDataDir`
  '''
  epoch = Epoch.promote(epoch)
  if isfilepath(tspath):
    if not tspath.endswith(TimeSeriesFile.DOTEXT):
      raise ValueError(
          "%s does not end in %s" % (shortpath(tspath), TimeSeriesFile.DOTEXT)
      )
    return TimeSeriesFile(tspath, typecode, epoch=epoch)
  if isdirpath(tspath):
    tsfilenames = fnmatchdir(tspath, '*' + TimeSeriesFile.DOTEXT)
    if tsfilenames:
      # contains some .csts files
      tsfilepath = joinpath(tspath, tsfilenames[0])
      f0_typecode = TimeSeriesFile(tsfilepath).typecode
      if typecode is not None and typecode != f0_typecode:
        warning(
            "supplied typecode %r does not match typecode %r from file %r",
            typecode, f0_typecode, tsfilepath
        )
      typecode = f0_typecode
      return TimeSeriesPartitioned(
          tspath, typecode, policy='annual', epoch=epoch
      )
    return TimeSeriesDataDir(tspath, policy='annual', epoch=epoch)
  raise ValueError("cannot deduce time series type from tspath %r" % (tspath,))

# pylint: disable=redefined-builtin
@contextmanager
def saved_figure(figure_or_ax, dir=None, ext=None):
  ''' Context manager to save a `Figure` to a file and yield the file path.

      Parameters:
      * `figure_or_ax`: a `matplotlib.figure.Figure` or an object
        with a `.figure` attribute such as a set of `Axes`
      * `dir`: passed to `tempfile.TemporaryDirectory`
      * `ext`: optional file extension, default `'png'`
  '''
  figure = getattr(figure_or_ax, 'figure', figure_or_ax)
  if dir is None:
    dir = '.'
  if ext is None:
    ext = 'png'
  with TemporaryDirectory(dir=dir or '.') as tmppath:
    tmpimgpath = joinpath(tmppath, f'plot.{ext}')
    pfx_call(figure.savefig, tmpimgpath)
    yield tmpimgpath

def save_figure(figure_or_ax, imgpath: str, force=False):
  ''' Save a `Figure` to the file `imgpath`.

      Parameters:
      * `figure_or_ax`: a `matplotlib.figure.Figure` or an object
        with a `.figure` attribute such as a set of `Axes`
      * `imgpath`: the filesystem path to which to save the image
      * `force`: optional flag, default `False`: if true the `imgpath`
        will be written to even if it exists
  '''
  if not force and existspath(imgpath):
    raise ValueError("image path already exists: %r" % (imgpath,))
  _, imgext = splitext(basename(imgpath))
  ext = imgext[1:] if imgext else 'png'
  with saved_figure(figure_or_ax, dir=dirname(imgpath), ext=ext) as tmpimgpath:
    if not force and existspath(imgpath):
      raise ValueError("image path already exists: %r" % (imgpath,))
    pfx_call(os.link, tmpimgpath, imgpath)

def print_figure(figure_or_ax, imgformat=None, file=None):
  ''' Print `figure_or_ax` to a file.

      Parameters:
      * `figure_or_ax`: a `matplotlib.figure.Figure` or an object
        with a `.figure` attribute such as a set of `Axes`
      * `imgformat`: optional output format; if omitted use `'sixel'`
        if `file` is a terminal, otherwise `'png'`
      * `file`: the output file, default `sys.stdout`
  '''
  if file is None:
    file = sys.stdout
  if imgformat is None:
    if file.isatty():
      imgformat = 'sixel'
    else:
      imgformat = 'png'
  with saved_figure(figure_or_ax) as tmpimgpath:
    with open(tmpimgpath, 'rb') as imgf:
      if imgformat == 'sixel':
        run(['img2sixel'], stdin=imgf, stdout=file.fileno(), check=True)
      else:
        for bs in CornuCopyBuffer.from_file(imgf):
          file.write(bs)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
