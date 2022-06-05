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
from fnmatch import fnmatch
from functools import partial
from getopt import GetoptError
from math import nan  # pylint: disable=no-name-in-module
from mmap import mmap, MAP_PRIVATE, PROT_READ  # pylint: disable=no-name-in-module,c-extension-no-member
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
from icontract import ensure, require, DBC
from matplotlib.figure import Figure
import numpy as np
from numpy import datetime64
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
from cs.pfx import Pfx, pfx, pfx_call, pfx_method
from cs.progress import progressbar
from cs.py.modules import import_extra
from cs.resources import MultiOpenMixin
from cs.result import CancellationError
from cs.upd import Upd, UpdProxy, print  # pylint: disable=redefined-builtin

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 3 - Alpha",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'arrow',
        'cs.cmdutils',
        'cs.configutils>=HasConfigIni',
        'cs.deco',
        'cs.fs',
        'cs.fstags',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.py.modules',
        'cs.resources',
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

# initial support is singled 64 bit integers and double floats
SUPPORTED_TYPECODES = {
    'q': int,
    'd': float,
}
assert all(typecode in typecodes for typecode in SUPPORTED_TYPECODES)
TYPECODE_FOR = {type_: code for code, type_ in SUPPORTED_TYPECODES.items()}
assert len(SUPPORTED_TYPECODES) == len(TYPECODE_FOR)

def typecode_of(type_) -> str:
  ''' Return the `array` typecode for the type `type_`.
      This supports the types in `SUPPORTED_TYPECODES`: `int` and `float`.
  '''
  try:
    return TYPECODE_FOR[type_]
  except KeyError as e:
    raise TypeError(
        "unsupported type %s, SUPPORTED_TYPED=%r" %
        (type_, SUPPORTED_TYPECODES)
    ) from e

def type_of(typecode: str) -> type:
  ''' Return the type associated with `array` `typecode`.
      This supports the types in `SUPPORTED_TYPECODES`: `int` and `float`.
  '''
  try:
    return SUPPORTED_TYPECODES[typecode]
  except KeyError as e:
    raise ValueError(
        "unsupported typecode %r, SUPPORTED_TYPED=%r" %
        (typecode, SUPPORTED_TYPECODES)
    ) from e

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

def _dt64(times):
  return np.array(list(map(int, times))).astype('datetime64[s]')

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
    ''' Usage: {cmd} [-f] [-o imgpath.png] [--show] days [{{glob|fields}}...]
          Plot the most recent days of data from the time series at tspath.
          Options:
          -f              Force. -o will overwrite an existing image file.
          -o imgpath.png  File system path to which to save the plot.
          --show          Show the image in the GUI.
          --stacked       Stack the plot lines/areas.
          glob|fields     If glob is supplied, constrain the keys of
                          a TimeSeriesDataDir by the glob.
    '''
    options = self.options
    runstate = options.runstate
    options.show_image = False
    options.imgpath = None
    options.stacked = False
    options.multi = False
    self.popopts(
        argv,
        options,
        f='force',
        multi=None,
        o_='imgpath',
        show='show_image',
        stacked=None,
    )
    force = options.force
    imgpath = options.imgpath
    if imgpath and not force and existspath(imgpath):
      raise GetoptError("imgpath exists: %r" % (imgpath,))
    days = self.poparg(argv, int, "days to display", lambda days: days > 0)
    xit = 0
    now = time.time()
    start = now - days * 24 * 3600
    ts = options.ts
    plot_dx = 14
    plot_dy = 8
    plot_kw = {}
    if isinstance(ts, TimeSeries):
      if argv:
        raise GetoptError(
            "fields:%r should not be suppplied for a %s" % (argv, s(ts))
        )
      ax = ts.plot(
          start, now, runstate=runstate, figsize=(plot_dx, plot_dy), **plot_kw
      )  # pylint: disable=missing-kwoa
      figure = ax.figure
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
      plot_dy = max(plot_dy, len(keys) // 2)
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
          figsize=(plot_dx, plot_dy),
          **plot_kw,
      )  # pylint: too-many-function-args.disable=missing-kwoa
      if ax is None:
        return 1
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

@decorator
def plotrange(func, needs_start=False, needs_stop=False):
  ''' A decorator for plotting methods with optional `start` and `stop`
      leading positional parameters and an optional `figure` keyword parameter.

      The decorator parameters `needs_start` and `needs_stop`
      may be set to require non-`None` values for `start` and `stop`.

      If `start` is `None` its value is set to `self.start`.
      If `stop` is `None` its value is set to `self.stop`.

      The decorated method is then called as:

          func(self, start, stop, *a, **kw)

      where `*a` and `**kw` are the additional positional and keyword
      parameters respectively, if any.
  '''

  # pylint: disable=keyword-arg-before-vararg
  @require(lambda start: not needs_start or start is not None)
  @require(lambda stop: not needs_stop or stop is not None)
  def plotrange_wrapper(self, start=None, stop=None, *a, **kw):
    import_extra('pandas', DISTINFO)
    import_extra('matplotlib', DISTINFO)
    if start is None:
      start = self.start
    if stop is None:
      stop = self.stop
    return func(self, start, stop, *a, **kw)

  return plotrange_wrapper

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
    # this would be a range but they only work in integers
    return (
        self.start + self.step * offset_step
        for offset_step in self.offset_range(start, stop)
    )

class Epoch(namedtuple('Epoch', 'start step'), TimeStepsMixin):
  ''' The basis of time references with a starting UNIX time, the
      `epoch` and the `step` defining the width of a time slot.
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
        This returns `typecode_of(type(self.start))`.
    '''
    return typecode_of(type(self.start))

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
  def __init__(self, epoch: Epoch, typecode: str):
    self.epoch = epoch
    self.typecode = typecode

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
  def as_pd_series(self, start=None, stop=None):
    ''' Return a `pandas.Series` containing the data from `start` to `stop`,
        default from `self.start` and `self.stop` respectively.
    '''
    pd = import_extra('pandas', DISTINFO)
    if start is None:
      start = self.start  # pylint: disable=no-member
    if stop is None:
      stop = self.stop  # pylint: disable=no-member
    times, data = self.data2(start, stop)
    return pd.Series(data, _dt64(times), self.np_type)

  @plotrange
  def plot(
      self,
      start,
      stop,
      *,
      label=None,
      runstate=None,  # pylint: disable=unused-argument
      **plot_kw,
  ):
    ''' Convenience shim for `DataFrame.plot` to plot data from
        `start` to `stop`.  Return the plot `Axes`.

        Parameters:
        * `start`,`stop`: the time range
        * `runstate`: optional `RunState`, ignored in this implementation
        * `label`: optional label for the graph
        Other keyword parameters are passed to `DataFrame.plot`.
    '''
    pd = import_extra('pandas', DISTINFO)
    if label is None:
      label = "%s[%s:%s]" % (self, arrow.get(start), arrow.get(stop))
    xdata, yaxis = self.data2(start, stop)
    xaxis = _dt64(xdata)
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
    typecode = chr(typecode_b)
    if typecode not in SUPPORTED_TYPECODES:
      raise ValueError(
          "unsupported typecode, expected one of %r, got %r" % (
              SUPPORTED_TYPECODES,
              typecode,
          )
      )
    time_typecode = chr(time_typecode_b)
    if time_typecode not in SUPPORTED_TYPECODES:
      raise ValueError(
          "unsupported time_typecode, expected one of %r, got %r" % (
              SUPPORTED_TYPECODES,
              time_typecode,
          )
      )
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

      Read only users can just instantiate an instance.
      Read/write users should use the instance as a context manager,
      which will automatically rewrite the file with the array data
      on exit.

      Note that the save-on-close is done with `TimeSeries.flush()`
      which ony saves if `self.modified`.
      Use of the `__setitem__` or `pad_to` methods set this flag automatically.
      Direct access via the `.array` will not set it,
      so users working that way for performance should update the flag themselves.

      The data file itself has a header indicating the file data big endianness,
      the datum type and the time type (both `array.array` type codes).
      Following these are the start and step sizes in the time type format.
      This is automatically honoured on load and save.
  '''

  DOTEXT = '.csts'

  # pylint: disable=too-many-branches,too-many-statements
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
        containing machine data for the time series values.

        Parameters:
        * `fspath`: the filename of the data file
        * `typecode` optional expected `array.typecode` value of the data;
          if specified and the data file exists, they must match;
          if not specified then the data file must exist
          and the `typecode` will be obtained from its header
        * `start`: the UNIX epoch time for the first datum
        * `step`: the increment between data times
        * `time_typecode`: the type of the start and step times;
          inferred from the type of the start time value if unspecified
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
    epoch = Epoch.promote(epoch)
    HasFSPath.__init__(self, fspath)
    if fstags is None:
      fstags = FSTags()
    self.fstags = fstags
    try:
      header, = TimeSeriesFileHeader.scan_fspath(self.fspath, max_count=1)
    except FileNotFoundError:
      # a missing file is ok, other exceptions are not
      header = None
    # compare the file against the supplied arguments
    if header is None:
      # no existing file
      if typecode is None:
        raise ValueError(
            "no typecode supplied and no data file %r" % (fspath,)
        )
      if epoch is None:
        raise ValueError("no epoch supplied and no data file %r" % (fspath,))
      header = TimeSeriesFileHeader(
          bigendian=NATIVE_BIGENDIANNESS[typecode],
          typecode=typecode,
          epoch=epoch,
      )
    else:
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
    TimeSeries.__init__(self, epoch, typecode)
    if fill is None:
      if typecode == 'd':
        fill = nan
      elif typecode == 'q':
        fill = 0
      else:
        raise RuntimeError(
            "no default fill value for typecode=%r" % (typecode,)
        )
    self.fill = fill
    self.fill_bs = header.datum_type.transcribe_value(self.fill)
    self._itemsize = array(typecode).itemsize
    assert self._itemsize == self.header.datum_type.length
    self.modified = False
    self._array = None

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

  def file_offset(self, offset):
    ''' Return the file position for the data with position `offset`.
    '''
    return (
        TimeSeriesFileHeader.HEADER_LENGTH +
        self.header.datum_type.length * offset
    )

  def peek(self, when: Numeric, f=None):
    ''' Read a single data value for the UNIX time `when`
        from the file `f`.
        The default file is obtained by opening `self.fspath` for read.
    '''
    if when < self.start:
      raise ValueError("when:%s must be >=self.start:%s" % (when.self.start))
    return self.peek_offset(self.offset(when), f=f)

  def peek_offset(self, offset, f=None):
    ''' Read a single data value from the binary file `f` at _data_
        offset `offset` i.e. the array index.
        Return the value.
        The default file is obtained by opening `self.fspath` for read.
    '''
    if f is None:
      with open(self.fspath, 'rb') as f2:
        return self.peek_offset(offset, f2)
    read_len = self.header.datum_type.length
    bs = f.pread(f.fileno(), self.file_offset(offset), read_len)
    if len(bs) == 0:
      return self.fill
    if len(bs) < read_len:
      raise ValueError(
          "%s.peek(f=%s,%d): expected %d bytes, got %d bytes: %r" %
          (self, f, offset, read_len, len(bs), bs)
      )
    return self.header.parse_value(bs)

  def poke(self, when: Numeric, value: Numeric, f=None):
    ''' Write a single data value for the UNIX time `when` to the file `f`.
        The default file is obtained by opening `self.fspath` for update.
    '''
    if when < self.start:
      raise ValueError("when:%s must be >=self.start:%s" % (when.self.start))
    self.poke_offset(self.offset(when), value, f=f)

  def poke_offset(self, offset: int, value: Numeric, f=None):
    ''' Write a single data value to the binary file `f` at _data_
        offset `offset` i.e. the array offset.
        The default file is obtained by opening `self.fspath` for update.
    '''
    if offset < 0:
      raise ValueError("offset:%d must be >= 0" % (offset,))
    if f is None:
      with open(self.fspath, 'w+b') as f2:
        self.poke_offset(offset, value, f=f2)
      return
    seek_offset = self.file_offset(offset)
    dtype = self.header.datum_type
    S = os.fstat(f.fileno())
    if S.st_size > seek_offset:
      # pad intervening data with self.fill
      pad_length = S.st_size - seek_offset
      assert pad_length % dtype.length == 0
      pad_count = pad_length // dtype.length
      pad_bs = self.fill_bs * pad_count
      nwritten = os.pwrite(f.fileno(), S.st_size, pad_bs)
      if nwritten != len(pad_bs):
        raise IOError(
            "tried to write %d bytes, wrote %d bytes" %
            (len(pad_bs), nwritten)
        )
    datum_bs = dtype.transscribe_value(value)
    assert len(datum_bs) == dtype.length
    nwritten = os.pwrite(f.fileno(), seek_offset, datum_bs)
    if nwritten != len(datum_bs):
      raise IOError(
          "tried to write %d bytes, wrote %d bytes" %
          (len(datum_bs), nwritten)
      )

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
    try:
      hdr, ary = self.load_from(self.fspath)
    except FileNotFoundError:
      # no file, empty array
      ary = array(self.typecode)
    else:
      # sanity check the header
      if hdr.typecode != self.typecode:
        raise ValueError(
            "file typecode %r does not match self.typecode:%r" %
            (hdr.typecode, self.typecode)
        )
      if hdr.time_typecode != self.time_typecode:
        warning(
            "file time typecode %r does not match self.time_typecode:%r",
            hdr.time_typecode, self.time_typecode
        )
      if hdr.step != self.step:
        raise ValueError(
            "file step %r does not match self.step:%r" % (hdr.step, self.step)
        )
    return ary

  @staticmethod
  def load_from(fspath):
    ''' Load the data from `fspath`, return the header and an
        `array.array(typecode)` containing the file data.
        Raises `FileNotFoundError` if the file does not exist.
    '''
    with pfx_open(fspath, 'rb') as tsf:
      bfr = CornuCopyBuffer.from_file(tsf)
      header = TimeSeriesFileHeader.parse(bfr)
      ary = array(header.typecode)
      itemsize = header.datum_type.length
      flen = os.fstat(tsf.fileno()).st_size
      datalen = flen - bfr.offset
      if datalen % ary.itemsize != 0:
        warning(
            "data length:%d is not a multiple of item size:%d", datalen,
            itemsize
        )
      with mmap(tsf.fileno(), flen, MAP_PRIVATE, PROT_READ) as mm:
        mv = memoryview(mm)
        ary.frombytes(mv[bfr.offset:flen])
        mv = None
    if header.bigendian != NATIVE_BIGENDIANNESS[header.typecode]:
      ary.byteswap()
    return header, ary

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
    self.save_to(fspath)

  @typechecked
  def save_to(self, fspath: str):
    ''' Save the time series to `fspath`.

        *Warning*:
        if the file endianness is not the native endianness,
        the array will be byte swapped temporarily
        during the file write operation.
        Concurrent users should avoid using the array during this function.
    '''
    ary = self.array
    header = self.header
    native_bigendian = NATIVE_BIGENDIANNESS[ary.typecode]
    with pfx_open(fspath, 'wb') as tsf:
      for bs in header.transcribe_flat():
        tsf.write(bs)
      if header.bigendian != native_bigendian:
        with array_byteswapped(ary):
          ary.tofile(tsf)
      else:
        ary.tofile(tsf)
    fstags = self.fstags[fspath]
    fstags['start'] = self.epoch.start
    fstags['step'] = self.epoch.step
    fstags['datatype'] = SUPPORTED_TYPECODES[self.typecode].__name__
    fstags['timetype'] = type(self.epoch.start).__name__

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
        If `prepad` is true, pad the resulting list at the beginning
    '''
    astart, astop = self.offset_bounds(start, stop)
    return self.offset_slice(astart, astop)

  def offset_slice(self, astart, astop):
    ''' Return a slice of the underlying array
        for the array indices `astart:astop`.
    '''
    if astart < 0:
      raise IndexError(
          "%s slice index %s starts at a negative offset" %
          (type(self).__name__, astart)
      )
    ary = self.array
    values = ary[astart:astop]
    if astop > len(ary):
      # pad with nan
      values.extend([self.fill] * (astop - len(ary)))
    return values

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
    ary = self.array
    # avoid confusion with negative indices
    if when < 0:
      raise ValueError("invalid when:%s, must be >= 0" % (when,))
    try:
      return ary[self.array_index(when)]
    except IndexError:
      return nan

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

class TimePartition(namedtuple('TimePartition', 'epoch name offset0 steps'),
                    TimeStepsMixin):
  ''' A `namedtuple` for a slice of time with the following attributes:
      * `epoch`: the reference `Epoch`
      * `name`: the name for this slice
      * `offset0`: the epoch offset of the start time (`self.start`)
      * `steps`: the number of time slots in this partition

      These are used by `TimespanPolicy` instances to express the partitions
      into which they divide time.
  '''

  @property
  def start(self):
    ''' The start UNIX time derived from `self.epoch` and `self.offset0`.
    '''
    return self.epoch.when(self.offset0)

  @property
  def stop(self):
    ''' The start UNIX time derived from `self.epoch` and `self.offset0` and `self.steps`.
    '''
    return self.epoch.when(self.offset0 + self.steps)

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
    offset = self.offset0
    epoch = self.epoch
    for offset in self.offsets():
      yield epoch.when(offset)

  def offsets(self):
    ''' Return an iterable of the epoch offsets from `self.start` to `self.stop`.
    '''
    return range(self.offset0, self.offset0 + self.steps)

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
  def promote(cls, policy, epoch: OptionalEpochy = None):
    ''' Factory to promote `policy` to a `TimespanPolicy` instance.

        The supplied `policy` may be:
        * `str`: return an instance of the named policy
        * `TimespanPolicy` subclass: return an instance of the subclass
        * `TimespanPolicy` instance: return the instance
    '''
    if cls is not TimespanPolicy:
      raise TypeError(
          "TimespanPolicy.from_name is not meaningful from a subclass (%s)" %
          (cls.__name__,)
      )
    epoch = Epoch.promote(epoch)
    if not isinstance(policy, TimespanPolicy):
      if epoch is None:
        raise ValueError("epoch may not be None if promotion is required")
      if isinstance(policy, str):
        policy = TimespanPolicy.from_name(policy, epoch=epoch)
      elif isinstance(policy, type) and issubclass(policy, TimespanPolicy):
        policy = policy(epoch=epoch)
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
        offset0=start_offset,
        steps=end_offset - start_offset
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
      offset0 = epoch.offset(max(span.start, when))
      offset1 = epoch.offset(min(span.stop, stop))
      yield TimePartition(epoch, span.name, offset0, offset1 - offset0)
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
  def __init__(self, epoch: Epochy, *, tzinfo: Optional[str] = None):
    super().__init__(epoch)
    if tzinfo is None:
      tzinfo = get_default_timezone_name()
    self.tzinfo = tzinfo

  def __str__(self):
    return "%s:%s" % (super().__str__(), self.tzinfo)

  def Arrow(self, when):
    ''' Return an `arrow.Arrow` instance for the UNIX time `when`
        in the policy timezone.
    '''
    return arrow.Arrow.fromtimestamp(when, tzinfo=self.tzinfo)

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
    a = arrow.get(span_name, self.PARTITION_FORMAT, tzinfo=self.tzinfo)
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
    calendar_start = pfx_call(arrow.get, name, tzinfo=self.tzinfo)
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
      tzinfo: Optional[str] = None,
  ):
    super().__init__()
    self.epoch = epoch
    if tzinfo is None:
      tzinfo = get_default_timezone_name()
    if policy is None:
      policy_name = self.DEFAULT_POLICY_NAME
      policy = TimespanPolicy.from_name(
          policy_name, epoch=self.epoch, tzinfo=tzinfo
      )
    self.policy = policy
    self._rules = {}

  def __str__(self):
    return "%s(%s,%s)" % (
        type(self).__name__,
        getattr(self, 'epoch', 'STEP_UNDEFINED'),
        getattr(self, 'policy', 'POLICY_UNDEFINED'),
    )

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
      runstate=None,
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
    elif not isinstance(keys, (tuple, list)):
      keys = tuple(keys)
    indices = _dt64(self.range(start, stop))
    data_dict = {}
    with UpdProxy(prefix="gather fields: ") as proxy:
      for key in progressbar(keys, "gather fields"):
        if runstate and runstate.cancelled:
          raise CancellationError
        proxy.text = key
        with Pfx(key):
          if key not in self:
            raise KeyError("no such key")
          data_dict[key] = self[key].as_pd_series(start, stop)
    if runstate and runstate.cancelled:
      raise CancellationError
    return pfx_call(
        pd.DataFrame,
        data=data_dict,
        index=indices,
        columns=keys,
        copy=False,
    )

  @plotrange
  def plot(
      self, start, stop, keys=None, *, label=None, runstate=None, **plot_kw
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
    df = self.as_pd_dataframe(start, stop, keys, runstate=runstate)
    for key in keys:
      with Pfx(key):
        ts = self[key]
        kname = ts.tags.get('csv.header', key)
        if label:
          kname = label + ': ' + kname
        if kname != key:
          df.rename(columns={key: kname}, inplace=True)
    print(df)
    if runstate and runstate.cancelled:
      raise CancellationError
    return df.plot(**plot_kw)

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
      tzinfo: Optional[str] = None,
      fstags: Optional[FSTags] = None,
  ):
    epoch = Epoch.promote(epoch)
    self.epoch = epoch
    if not isdirpath(fspath):
      pfx_call(needdir, fspath)
    HasFSPath.__init__(self, fspath)
    if fstags is None:
      fstags = FSTags()
    HasConfigIni.__init__(self, self.CONFIG_SECTION_NAME)
    self.fstags = fstags
    config = self.config
    cfg_start = config.start
    cfg_step = config.step
    if epoch is None:
      if cfg_start is None or cfg_step is None:
        raise ValueError(
            "no epoch provided and start or step missing from config %s[%s]: %r"
            % (
                shortpath(self.configpath),
                self.CONFIG_SECTION_NAME,
                self.config,
            )
        )

      epoch = Epoch(cfg_start, cfg_step)
    start, step = epoch.start, epoch.step
    if start is None:
      start = 0 if cfg_start is None else cfg_start
      config.start = start
    elif cfg_start is None:
      config.start = start
    elif start != cfg_start:
      raise ValueError("start:%r != config.start:%r" % (start, cfg_start))
    if step is None:
      step = 0 if cfg_step is None else cfg_step
      config.step = step
    elif cfg_step is None:
      config.step = step
    elif step != cfg_step:
      raise ValueError("step:%r != config.step:%r" % (step, self.step))
    self.epoch = epoch
    tzinfo = tzinfo or self.tzinfo
    if policy is None:
      policy_name = config.auto.policy.name or TimespanPolicy.DEFAULT_NAME
      policy = TimespanPolicy.from_name(policy_name, epoch=epoch)
    else:
      policy = TimespanPolicy.promote(policy, epoch=epoch)
      policy_name = policy.name
    # fill in holes in the config
    if not config.auto.policy.name:
      self.policy_name = policy_name
    if not config.auto.policy.tzinfo:
      self.tzinfo = tzinfo
    TimeSeriesMapping.__init__(self, epoch=epoch, policy=policy, tzinfo=tzinfo)
    self._infill_keys_from_subdirs()

  def __str__(self):
    return "%s(%s,%s,%s)" % (
        type(self).__name__,
        shortpath(self.fspath),
        getattr(self, 'step', 'STEP_UNDEFINED'),
        getattr(self, 'policy', 'POLICY_UNDEFINED'),
    )

  __repr__ = __str__

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
    ''' Set the `policy.tzinfo` config value, usually a key from
        `TimespanPolicy.FACTORIES`.
    '''
    self.config['policy.name'] = new_policy_name

  @property
  def tzinfo(self):
    ''' The `policy.tzinfo` config value, a timezone name.
    '''
    name = self.config.auto.policy.tzinfo
    if not name:
      name = get_default_timezone_name()
      self.tzinfo = name
    return name

  @tzinfo.setter
  def tzinfo(self, new_timezone: str):
    ''' Set the `policy.tzinfo` config value, a timezone name.
    '''
    self.config['policy.tzinfo'] = new_timezone

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
      typecode = self.tags.typecode
      if typecode is None:
        raise ValueError("no typecode and no FSTags 'typecode' tag")
    if typecode not in SUPPORTED_TYPECODES:
      raise ValueError(
          "typecode=%s not in SUPPORTED_TYPECODES:%r" %
          (s(typecode), sorted(SUPPORTED_TYPECODES.keys()))
      )
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
    for when, value in zip(whens, values):
      if value is None and skipNone:
        continue
      if span is None or when not in span:
        # new partition required, sets ts as well
        ts = self.subseries(when)
        span = ts.partition_span
      ts[when] = value

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
