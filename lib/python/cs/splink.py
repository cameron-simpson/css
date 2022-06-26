#!/usr/bin/env python3

''' Assorted utility functions for working with data
    downloaded from Selectronics' SP-LINK programme
    which communicates with their controllers.

    I use this to gather and plot data from my solar inverter.
'''

from collections import defaultdict, namedtuple
from contextlib import contextmanager
import csv
from datetime import datetime
from fnmatch import fnmatch
from functools import partial
from getopt import GetoptError
import os
from os.path import (
    basename,
    dirname,
    exists as existspath,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    relpath,
    splitext,
)
from pprint import pprint
import shlex
import sys
import time

import arrow
from dateutil.tz import tzlocal
from matplotlib.figure import Figure
from typeguard import typechecked

from cs.context import stackattrs
from cs.csvutils import csv_import
from cs.deco import cachedmethod
from cs.fs import HasFSPath, fnmatchdir, needdir, shortpath
from cs.fstags import FSTags
from cs.lex import s
from cs.logutils import warning, error
from cs.pfx import Pfx, pfx, pfx_call, pfx_method
from cs.progress import progressbar
from cs.psutils import run
from cs.resources import MultiOpenMixin
from cs.sqltags import SQLTags
from cs.tagset import TagSet
from cs.timeseries import (
    Epoch,
    TimeSeriesBaseCommand,
    TimeSeriesDataDir,
    TimespanPolicyYearly,
    plot_events,
    plotrange,
    print_figure,
    save_figure,
    tzfor,
)
from cs.upd import Upd, print  # pylint: disable=redefined-builtin

__version__ = '20220606-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 3 - Alpha",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'arrow',
        'cs.context',
        'cs.csvutils',
        'cs.deco',
        'cs.fs',
        'cs.fstags',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.progress',
        'cs.psutils',
        'cs.resources',
        'cs.sqltags',
        'cs.tagset',
        'cs.timeseries',
        'cs.upd',
        'matplotlib',
        'typeguard',
    ],
    'entry_points': {
        'console_scripts': [
            'splink = cs.splink:main',
        ],
    },
}

# field name patterns for the named plot modes
DEFAULT_PLOT_MODE_PATTERNS = {
    'energy': '*_kwh',
    'power': '*_average_kw',
    'vac': '*_v_ac',
    'vdc': '*_v_dc',
}

DEFAULT_PLOT_EVENT_LABELS = (
    'System - AC Source no longer detected',
    'System - AC Source outside operating range',
    'Bridge negative correction',
    'Bridge positive correction',
    'AC Mode - Synchronised begin',
)

pfx_listdir = partial(pfx_call, os.listdir)

def main(argv=None):
  ''' SP-Link command line mode.
  '''
  return SPLinkCommand(argv).run()

def ts2001_unixtime(tzname=None):
  ''' Convert an SP-Link seconds-since-2001-01-01-local-time offset
      into a UNIX time.
  '''
  if tzname is None:
    tzname = 'local'
  a2001 = arrow.get(datetime(2001, 1, 1, 0, 0, 0), tzname)
  unixtime = a2001.timestamp()
  return unixtime

class SPLinkCSVDir(HasFSPath):
  ''' A class for working with SP-Link data downloads,
      referring to a particular `PerformanceData*` download directory.
  '''

  COLUMN_SECONDS_2001 = 'Date/Time Stamp [Seconds From The Year 2001]'
  COLUMN_DATE = 'Date/Time Stamp [dd/MM/yyyy]'
  COLUMN_DATE_STRPTIME = '%d/%m/%Y'
  COLUMN_DATETIME = 'Date/Time Stamp [dd/MM/yyyy - HH:mm:ss]'
  COLUMN_DATETIME_STRPTIME = '%d/%m/%Y - %H:%M:%S'

  @property
  @cachedmethod
  def sitename(self):
    ''' The site name inferred from a CSV data filename.
    '''
    return self.fnmatch('?*_*_*.CSV')[0].split('_', 1)[0]

  @pfx
  def csvfilename(self, dataset: str) -> str:
    ''' Return the CSV filename specified by `dataset`.

        Example:

            self.csvpath('DetailedData')
    '''
    csvfilename, = self.fnmatch(f'*_{dataset}_*.CSV')
    return csvfilename

  def csvpath(self, dataset: str) -> str:
    ''' Return the CSV pathname specified by `dataset`.

        Example:

            self.csvpath('DetailedData')
    '''
    return self.pathto(self.csvfilename(dataset))

  @staticmethod
  def csv_tagsets(csvpath):
    ''' Yield `(unixtime,TagSet)` 2-tuples from the CSV file `csvpath`.
    '''
    ts2001_offset = ts2001_unixtime()
    _, rows = csv_import(csvpath)
    for row in rows:
      tags = TagSet()
      for attr, value in row._asdict().items():
        try:
          value = int(value)
        except ValueError:
          try:
            value = float(value)
          except ValueError:
            pass
        tags[attr] = value
      when = float(
          row.date_time_stamp_seconds_from_the_year_2001
      ) + ts2001_offset
      yield when, tags

  def dataset_tagsets(self, dataset: str):
    ''' Yield `(unixtime,TagSet)` 2-tuples from the CSV file
        associated with `dataset`.
    '''
    yield from self.csv_tagsets(self.csvpath(dataset))

  # pylint: disable=too-many-locals
  @typechecked
  def export_to_timeseries(
      self, dataset: str, tsd: TimeSeriesDataDir, tzname=None
  ):
    ''' Read the CSV file in `self.fspath` specified by `dataset`
        and export its contents into the `tsd:TimeSeriesDataDir`.
        Return exported `DataFrame`.
    '''
    return self.export_csv_to_timeseries(
        self.csvpath(dataset), tsd, tzname=tzname
    )

  def export_csv_to_timeseries(
      self, csvpath, tsd: TimeSeriesDataDir, tzname=None
  ):
    ''' Read the CSV file specified by `cvspath`
        and export its contents into the `tsd:TimeSeriesDataDir.
        Return exported `DataFrame`.
    '''
    # group the values by key
    short_csvpath = relpath(csvpath, self.fspath)
    if short_csvpath.startswith('../'):
      short_csvpath = shortpath(csvpath)
    with tsd:
      index_col = self.COLUMN_SECONDS_2001
      skip_cols = (self.COLUMN_DATETIME, self.COLUMN_DATE)
      ts2001_offset = int(ts2001_unixtime(tzname))
      # dataframe indexed by UNIX timestamp
      df, _ = tsd.read_csv(
          csvpath,
          index_col=index_col,
          usecols=lambda column_name: column_name not in skip_cols,
          converters={
              index_col: lambda seconds_s: ts2001_offset + int(seconds_s),
          },
      )
    return df

# pylint: disable=too-many-ancestors
class SPLinkDataDir(TimeSeriesDataDir):
  ''' A `TimeSeriesDataDir` to hold log data from an SP-Link CSV data download.
      This holds the data from a particular CSV log such as `'DetailedData'`.
      The `SPLinkData` class manages a couple of these and a downloads
      subdirectory and an events `SQLTags`.
  '''

  DEFAULT_POLICY_CLASS = TimespanPolicyYearly

  @typechecked
  def __init__(self, dirpath, dataset: str, step: int, policy=None, **kw):
    ''' Initialise the `SPLinkDataDir`.

        Parameters:
        * `dirpath`: the pathname of the directory holding the downloaded CSV files
        * `dataset`: which CSV file populates this time series, eg `'DetailedData'`
        * `step`: optional time series step size,
          default `SPLinkDataDir.DEFAULT_LOG_FREQUENCY`,
          which comes from `SPLinkCSVDir.DEFAULT_LOG_FREQUENCY`
        * `policy`: optional TimespanPolicy` instance;
          if omitted an `TimespanPolicyYearly` instance will be made
        Other keyword arguments are passed to the `TimeSeriesDataDir`
        initialiser.
    '''
    epoch = Epoch.promote((ts2001_unixtime(), step))
    if policy is None:
      policy = self.DEFAULT_POLICY_CLASS(epoch=epoch)
    super().__init__(dirpath, epoch=epoch, policy=policy, **kw)
    self.dataset = dataset

  @pfx_method
  def import_from(self, csvsrc, tzname=None):
    ''' Import the CSV data from `csvsrc` specified by `self.dataset`.
        Return the imported `DataFrame`.

        Parameters:
        * `csvsrc`: an `SPLinkCSVDir` instance or the pathname of a directory
          containing SP-Link CSV download data, or the pathname of a CSV file.

        Example:

            spd = SPLinkDataDir('spdata/DetailedData')
            spd.import_from(
                'spl/PerformanceData_2021-07-04_13-02-38',
                'DetailedData',
            )
    '''
    if isinstance(csvsrc, str):
      if isfilepath(csvsrc):
        # an individual SP-Link CSV download
        if not csvsrc.endswith('.CSV'):
          raise ValueError("filename does not end in .CSV")
        try:
          dsinfo = SPLinkData.parse_dataset_filename(csvsrc)
        except ValueError as e:
          warning("unusual filename: %s", e)
        else:
          if dsinfo.dataset != self.dataset:
            raise ValueError(
                "filename dataset:%r does not match self.dataset:%r" %
                (dsinfo.dataset, self.dataset)
            )
          csvdir = SPLinkCSVDir(dirname(csvsrc))
          return csvdir.export_csv_to_timeseries(csvsrc, self, tzname=tzname)
      if isdirpath(csvsrc):
        # a directory of SP-Link CSV downloads
        csvdir = SPLinkCSVDir(csvsrc)
        return csvdir.export_to_timeseries(self.dataset, self, tzname=tzname)
      raise ValueError("neither a CSV file nor a directory")
    if isinstance(csvsrc, SPLinkCSVDir):
      return csvsrc.export_to_timeseries(self.dataset, self, tzname=tzname)
    raise TypeError(
        "expected filesystem path or SPLinkCSVDir, got: %s" % (s(csvsrc),)
    )

  def to_csv(self, start, stop, f, *, columns=None, key_map=None, **to_csv_kw):
    ''' Return `pandas.DataFrame.to_csv()` for the data between `start` and `stop`.
    '''

    def df_mangle(df):
      ''' Insert the date/datetime and 2001-seconds columns at the
          front of the `DataFrame` before transcription.
      '''
      dt_column = {
          'DetailedData': SPLinkCSVDir.COLUMN_DATETIME,
          'DailySummaryData': SPLinkCSVDir.COLUMN_DATE,
      }[self.dataset]
      dt_strftime_format = {
          'DetailedData': SPLinkCSVDir.COLUMN_DATETIME_STRPTIME,
          'DailySummaryData': SPLinkCSVDir.COLUMN_DATE_STRPTIME,
      }[self.dataset]
      dt_values = [when.strftime(dt_strftime_format) for when in df.index]
      ts2001base = ts2001_unixtime(self.tz)
      dts_values = [int(when.timestamp() - ts2001base) for when in df.index]
      df.insert(0, SPLinkCSVDir.COLUMN_SECONDS_2001, dts_values)
      df.insert(1, dt_column, dt_values)

    return super().to_csv(
        start,
        stop,
        f,
        columns=columns,
        key_map=key_map,
        df_mangle=df_mangle,
        index=False,
        float_format='%g',
        quoting=csv.QUOTE_NONNUMERIC,
        **to_csv_kw,
    )

# information derived from the basename of an SP-Link download filename
SPLinkDataFileInfo = namedtuple(
    'SPLinkDataFileInfo', 'fspath sitename dataset unixtime dotext'
)

class SPLinkData(HasFSPath, MultiOpenMixin):
  ''' A directory containing SP-LInk data.

      This contains:
      - `downloads`: a directory containing copies of various SP-Link
        downloads i.e. this contains directories named `PerformanceData_*`.
      - `events.db`: accrued event data from the `EventData` CSV files
      - `DailySummaryData`: an `SPLinkDataDir` containing accrued
        data from the `DailySummaryData` CSV files
      - `DetailedData`: an `SPLinkDataDir` containing accrued data
        from the `DetailedData` CSV files
  '''

  # where the PerformanceData downloads reside
  DOWNLOADS = 'downloads'

  EVENTS_DATASETS = 'EventData',  # pylint: disable=trailing-comma-tuple
  TIMESERIES_DATASETS = 'DetailedData', 'DailySummaryData'

  TIMESERIES_DEFAULTS = {
      'DetailedData': (900, 'annual'),
      'DailySummaryData': (3600, 'annual'),
  }

  PERFORMANCEDATA_GLOB = 'PerformanceData_????-??-??_??-??-??'
  PERFORMANCEDATA_ARROW_FORMAT = 'YYYY-MM-DD_hh-mm-ss'

  def __init__(
      self,
      dirpath,
  ):
    if not isdirpath(dirpath):
      pfx_call(needdir, dirpath)
    super().__init__(dirpath)
    self._to_close = []

  def __str__(self):
    return f'{type(self).__name__}({shortpath(self.fspath)})'

  def info_dict(self, d=None):
    ''' Return an informational `dict` containing salient information
        about this `SPLinkData`, handy for use with `pformat()` or `pprint()`.
    '''
    if d is None:
      d = {}
    d.update(
        fspath=self.fspath,
        timeseries={
            dsname: getattr(self, dsname).info_dict()
            for dsname in sorted(self.TIMESERIES_DATASETS)
        },
    )
    return d

  @contextmanager
  def startup_shutdown(self):
    ''' Close the subsidiary time series on exit.
    '''
    try:
      yield
    finally:
      for obj in self._to_close:
        obj.close()
      self._to_close = []

  def __getattr__(self, tsname):
    ''' Autodefine attributes for the known time series.
    '''
    try:
      step, policy_name = self.TIMESERIES_DEFAULTS[tsname]
    except KeyError as e:
      raise AttributeError(
          "%s.%s: unknown attribute" % (type(self).__name__, tsname)
      ) from e
    tspath = self.pathto(tsname)
    needdir(tspath)
    ts = SPLinkDataDir(tspath, dataset=tsname, step=step, policy=policy_name)
    setattr(self, tsname, ts)
    return ts

  @property
  def downloadspath(self):
    ''' The filesystem path of the downloads subdirectory.
    '''
    return self.pathto(self.DOWNLOADS)

  def download_subdirs(self):
    ''' Return an iterable of the paths of the top level `PerformanceData_*`
        subdirectories in the downloads subdirectory.
    '''
    return [
        joinpath(self.downloadspath, perfdirname) for perfdirname in
        fnmatchdir(self.downloadspath, self.PERFORMANCEDATA_GLOB)
    ]

  @classmethod
  def parse_dataset_filename(cls, path):
    ''' Parse the filename part of `path` and derive an `SPLinkDataFileInfo`.
        Raises `ValueError` if the filename cannot be recognised.
    '''
    base, ext = splitext(basename(path))
    sitename, dataset, ymd, hms = base.split('_')
    ymd_hms = '_'.join((ymd, hms))
    try:
      when = arrow.get(
          ymd_hms, cls.PERFORMANCEDATA_ARROW_FORMAT, tzinfo='local'
      )
    except ValueError as e:
      warning("%r: %s", e)
      raise

    return SPLinkDataFileInfo(
        fspath=path,
        sitename=sitename,
        dataset=dataset,
        unixtime=when,
        dotext=ext,
    )

  def datasetpath(self, perfdirpath, dataset):
    ''' Return the filesystem path to the named `dataset`
        from the SP-Link download subdirectory `perfdirpath`.
    '''
    if (dataset not in self.TIMESERIES_DATASETS
        and dataset not in self.EVENTS_DATASETS):
      raise ValueError(
          "invalid dataset name %r: expected a time series name from %r or a log name from %r"
          % (dataset, self.TIMESERIES_DATASETS, self.EVENTS_DATASETS)
      )
    dsglob = '*_{dataset}_????-??-??_??-??-??.CSV'
    dsname, = fnmatchdir(perfdirpath, dsglob)
    return joinpath(perfdirpath, dsname)

  @property
  @cachedmethod
  def eventsdb(self):
    ''' The events `SQLTags` database.
    '''
    return SQLTags(self.pathto('events.sqlite'))

  def resolve(self, spec):
    ''' Resolve a field spec into an iterable of `(timeseries,key)`.
    '''
    with Pfx(spec):
      try:
        dsname, field_spec = spec.split(':', 1)
      except ValueError:
        # just a glob, poll all datasets
        dsnames = self.TIMESERIES_DATASETS
        field_spec = spec
      else:
        dsnames = dsname,  # pylint: disable=trailing-comma-tuple
      for dsname in dsnames:
        with Pfx(dsname):
          tsd = getattr(self, dsname)
          for key in pfx_call(tsd.keys, field_spec):
            yield tsd, key

  def to_csv(self, dsname, start, stop, f, **to_csv_kw):
    ''' Export the data from the dataset `dsname`
        between the times `start` to `stop`.
    '''
    if dsname not in self.TIMESERIES_DATASETS:
      raise ValueError(
          "dsname %r not in TIMESERIES_DATASETS:%r" %
          (dsname, self.TIMESERIES_DATASETS)
      )
    tsd = getattr(self, dsname)
    return tsd.to_csv(start, stop, f, **to_csv_kw)

  @plotrange
  def plot(
      self,
      start,
      stop,
      *,
      utcoffset,
      key_specs,
      mode=None,
      figsize=None,
      dpi=None,
      event_labels=None,
      mode_patterns=None,
      stacked=False,
      upd=None,
  ):
    ''' The core logic of the `SPLinkCommand.cmd_plot` method
        to plot arbitrary parameters against a time range.
    '''
    # DF hack: compute the timezone offset for "stop",
    # use it to skew the UNIX timestamps so that UTC tick marks and
    # placements look "local"
    if figsize is None:
      figsize = 14, 8
    if dpi is None:
      dpi = 100
    if event_labels is None:
      event_labels = DEFAULT_PLOT_EVENT_LABELS
    if mode_patterns is None:
      mode_patterns = DEFAULT_PLOT_MODE_PATTERNS
    if upd is None:
      upd = Upd()
    tsd_by_id = {}
    keys_by_id = defaultdict(list)
    with Pfx("key_specs"):
      for key_spec in key_specs:
        with Pfx(key_spec):
          if mode is None and key_spec in mode_patterns:
            mode = key_spec
          key_spec = mode_patterns.get(key_spec, key_spec)
          matched = False
          for tsd, key in self.resolve(key_spec):
            tsd_by_id[id(tsd)] = tsd
            keys_by_id[id(tsd)].append(key)
            matched = True
          if not matched:
            warning("no matches")
    if not tsd_by_id:
      raise ValueError(
          "no fields were resolved by key_specs=%r" % (key_specs,)
      )
    if mode is None:
      # scan the keys looking for a match to a mode
      for keys in keys_by_id.values():
        for key in keys:
          for mode_name, mode_glob in mode_patterns.items():
            if pfx_call(fnmatch, key, mode_glob):
              mode = mode_name
              break
        if mode is not None:
          break
    figure = Figure(figsize=figsize, dpi=dpi)
    figure.add_subplot()
    ax = figure.axes[0]
    with upd.run_task("plot"):
      for tsd_id, tsd in tsd_by_id.items():
        keys = keys_by_id[tsd_id]
        tsd.plot(start, stop, keys=keys, utcoffset=utcoffset, ax=ax)
    ax.set_title(str(self))
    return figure

class SPLinkCommand(TimeSeriesBaseCommand):
  ''' Command line to work with SP-Link data downloads.
  '''

  GETOPT_SPEC = 'd:n'
  USAGE_FORMAT = r'''Usage: {cmd} [-d spdpath] [-n] subcommand...
    -d spdpath  Specify the directory containing the SP-Link downloads
                and time series. Default from ${DEFAULT_SPDPATH_ENVVAR},
                or {DEFAULT_SPDPATH!r}.
    -n          No action; recite planned actions.'''

  SUBCOMMAND_ARGV_DEFAULT = 'info'

  DEFAULT_SPDPATH = '.'
  DEFAULT_SPDPATH_ENVVAR = 'SPLINK_DATADIR'
  DEFAULT_FETCH_SOURCE_ENVVAR = 'SPLINK_FETCH_SOURCE'

  ALL_DATASETS = SPLinkData.EVENTS_DATASETS + SPLinkData.TIMESERIES_DATASETS

  USAGE_KEYWORDS = {
      'ALL_DATASETS': ' '.join(sorted(ALL_DATASETS)),
      'TIMESERIES_DATASETS': ' '.join(sorted(SPLinkData.TIMESERIES_DATASETS)),
      'DEFAULT_SPDPATH': DEFAULT_SPDPATH,
      'DEFAULT_SPDPATH_ENVVAR': DEFAULT_SPDPATH_ENVVAR,
      'DEFAULT_FETCH_SOURCE_ENVVAR': DEFAULT_FETCH_SOURCE_ENVVAR,
  }

  def apply_defaults(self):
    ''' Set the default `spdpath`.
    '''
    self.options.fetch_source = os.environ.get(
        self.DEFAULT_FETCH_SOURCE_ENVVAR
    )
    self.options.fstags = FSTags()
    self.options.spdpath = os.environ.get(
        self.DEFAULT_SPDPATH_ENVVAR, self.DEFAULT_SPDPATH
    )

  def apply_opt(self, opt, val):
    ''' Handle an individual global command line option.
    '''
    options = self.options
    if opt == '-d':
      if not isdirpath(val):
        raise GetoptError("not a directory: %r" % (val,))
      options.spdpath = val
    elif opt == '-n':
      options.doit = False
    else:
      raise RuntimeError("unhandled pre-option")

  @contextmanager
  def run_context(self):
    ''' Define `self.options.spd`.
    '''
    options = self.options
    fstags = options.fstags
    with fstags:
      spd = SPLinkData(options.spdpath)
      with stackattrs(options, spd=spd):
        with spd:
          yield

  def cmd_export(self, argv):
    ''' Usage: {cmd} dataset
          Export the named dataset in the original CSV form.
          Available datasets: {TIMESERIES_DATASETS}
    '''
    options = self.options
    spd = options.spd
    dataset = self.poparg(
        argv,
        'dataset',
        str,
        lambda ds: ds in spd.TIMESERIES_DATASETS,
        f'dataset should be one of {SPLinkData.TIMESERIES_DATASETS}',
    )
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    now = time.time()
    start = now - 3 * 24 * 3600
    spd.to_csv(dataset, start, now, sys.stdout)

  def cmd_fetch(self, argv, fetch_source=None, doit=None, expunge=None):
    ''' Usage: {cmd} [-F rsync-source] [-nx] [-- [rsync-options...]]
          Rsync everything from rsync-source into the downloads area.
          -F    Fetch rsync source, default from ${DEFAULT_FETCH_SOURCE_ENVVAR}.
          -n    Passed to rsync. Just more convenient than putting it at the end.
          -x    Delete source files.
    '''
    options = self.options
    if doit is not None:
      options.doit = doit
    if fetch_source is not None:
      options.fetch_source = fetch_source
    options.expunge = False if expunge is None else expunge
    self.popopts(argv, options, F_='fetch_source', n='dry_run', x='expunge')
    doit = options.doit
    expunge = options.expunge
    fetch_source = options.fetch_source
    if not fetch_source:
      raise GetoptError(
          f"no fetch source: no ${self.DEFAULT_FETCH_SOURCE_ENVVAR} and no -F option"
      )
    spd = options.spd
    rsopts = ['-iaO']
    rsargv = ['set-x', 'rsync']
    if not doit:
      rsargv.append('-n')
    rsargv.extend(rsopts)
    if expunge:
      rsargv.append('--delete-source')
    rsargv.extend(argv)
    rsargv.extend(
        [
            '--', fetch_source + '/' + spd.PERFORMANCEDATA_GLOB,
            spd.downloadspath + '/'
        ]
    )
    print('+', shlex.join(argv))
    return run(rsargv).returncode

  # pylint: disable=too-many-statements,too-many-branches,too-many-locals
  def cmd_import(self, argv, datasets=None, doit=None, force=None):
    ''' Usage: {cmd} [-d dataset,...] [-n] [sp-link-download...]
          Import CSV data from the downloads area into the time series data.
          -d datasets       Comma separated list of datasets to import.
                            Default datasets: {ALL_DATASETS}
          -f                Force. Import datasets even if already marked as
                            imported.
          -n                No action. Recite planned imports.
          sp-link-download  Specify specific individual downloads to import.
                            The default is any download not tagged as already
                            imported.
    '''
    options = self.options
    if doit is not None:
      options.doit = doit
    if force is not None:
      options.force = force
    options.datasets = self.ALL_DATASETS if datasets is None else datasets
    options.once = False
    badopts = False
    options.popopts(
        argv,
        d_=('datasets', lambda opt: opt.split(',')),
        f='force',
        n='dry_run',
        dry_run='dry_run',
    )
    spd = options.spd
    upd = options.upd
    fstags = options.fstags
    runstate = options.runstate
    datasets = options.datasets
    doit = options.doit
    force = options.force
    if not datasets:
      warning("empty dataset list")
    for dataset in datasets:
      if dataset not in self.ALL_DATASETS:
        warning("unknown dataset name: %s", dataset)
        badopts = True
    if not argv:
      argv = spd.downloadspath,  # pylint: disable=trailing-comma-tuple
    if badopts:
      raise GetoptError("bad invocation")
    xit = 0
    seen_events = defaultdict(set)
    upd.cursor_invisible()
    for path in argv:
      if runstate.cancelled:
        break
      with Pfx(shortpath(path)):
        if not existspath(path):
          error("does not exist")
          xit = 1
          continue
        for dirpath, dirnames, filenames in os.walk(path):
          if runstate.cancelled:
            break
          dirnames[:] = sorted(
              (
                  dirname for dirname in dirnames
                  if dirname and not dirname.startswith('.')
              )
          )
          for filename in sorted(filenames):
            if runstate.cancelled:
              break
            if not filename or filename.startswith('.'):
              continue
            try:
              dsinfo = spd.parse_dataset_filename(filename)
            except ValueError:
              continue
            if dsinfo.dataset not in datasets:
              continue
            if dsinfo.dotext != '.CSV':
              continue
            dspath = joinpath(dirpath, filename)
            rdspath = relpath(dspath, path)
            short_dspath = relpath(dspath, spd.fspath)
            if short_dspath.startswith('../'):
              short_dspath = shortpath(dspath)
            with upd.run_task("import %s" % short_dspath):
              dataset = dsinfo.dataset
              with Pfx(rdspath):
                dstags = fstags[dspath]
                if not force and dstags.imported:
                  print("already imported:", short_dspath)
                  continue
                if dataset in spd.TIMESERIES_DATASETS:
                  ts = getattr(spd, dataset)
                  if doit:
                    df = ts.import_from(dspath)
                    dstags['imported'] = 1
                    print(f'{len(df.index)} rows from {rdspath}')
                  else:
                    print("import", dspath, "=>", ts)
                elif dataset in spd.EVENTS_DATASETS:
                  db = spd.eventsdb
                  seen = seen_events[dataset]
                  if doit:
                    with upd.run_task(f'{short_dspath}: read'):
                      when_tags = sorted(
                          SPLinkCSVDir.csv_tagsets(dspath),
                          key=lambda wt: wt[0]
                      )
                    with db:
                      if when_tags:
                        if not seen:
                          with upd.run_task(
                              f'{short_dspath}: load events from {db}',
                              report_print=True,
                              tick_delay=0.15,
                          ):
                            seen.update(
                                (ev.unixtime, ev.event_description)
                                for ev in db.find()
                            )
                          print(f'{len(seen)} old events from {db}')
                          if runstate.cancelled:
                            break
                        when_tags = [
                            (when, tags)
                            for when, tags in when_tags
                            if (when, tags['event_description']) not in seen
                        ]
                        if when_tags:
                          for when, tags in progressbar(
                              when_tags,
                              short_dspath,
                              update_frequency=16,
                              report_print=True,
                          ):
                            key = when, tags['event_description']
                            assert key not in seen
                            tags['dataset'] = dataset
                            db.default_factory(None, unixtime=when, tags=tags)
                            seen.add(key)
                          print(f'{len(when_tags)} new events from {rdspath}')
                    dstags['imported'] = 1
                  else:
                    print("import", dspath, "=>", db)
                else:
                  raise RuntimeError(
                      "do not know how to process dataset,"
                      " I know: events=%s, timeseries=%s" % (
                          ",".join(spd.EVENTS_DATASETS),
                          ",".join(spd.TIMESERIES_DATASETS),
                      )
                  )
    if runstate.cancelled:
      xit = xit or 1
    return xit

  def cmd_info(self, argv):
    ''' Usage: {cmd}
          Report infomation about the time series stored at tspath.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    options = self.options
    spd = options.spd
    print(spd)
    pprint(spd.info_dict())

  # pylint: disable=too-many-locals
  def cmd_plot(self, argv):
    ''' Usage: {cmd} [-e event,...] [-f] [-o imagepath] [--show] start-time [stop-time] {{mode|[dataset:]{{glob|field}}}}...
        -e events,...   Display the specified events.
        -f              Force. Overwirte the image path even if it exists.
        --stacked       Stack graphed values on top of each other.
        -o imagepath    Write the plot to imagepath.
                        If not specified, the image will be written
                        to the standard output in sixel format if
                        it is a terminal, and in PNG format otherwise.
        --show          Open the image path with "open".
        --tz tzspec     Skew the UTC times presented on the graph
                        to emulate the timezone specified by tzspec.
                        The default skew is the system local timezone.
        start-time      An integer number of days before the current time
                        or any datetime specification recognised by
                        dateutil.parser.parse.
        stop-time       Optional stop time, default now.
                        An integer number of days before the current time
                        or any datetime specification recognised by
                        dateutil.parser.parse.
        mode            A named graph mode, implying a group of fields.
    '''
    options = self.options
    options.tz = tzlocal()
    options.show_image = False
    options.imgpath = None
    options.stacked = False
    options.stacked = False
    options.event_labels = None
    self.popopts(
        argv,
        options,
        e_='event_labels',
        f='force',
        o_='imgpath',
        show='show_image',
        stacked=None,
        tz_=('tz', tzfor),
    )
    tz = options.tz
    # mandatory start time
    start = self.poptime(argv, 'start-time')
    # check for optional stop-time, default now
    if argv:
      try:
        stop = self.poptime(argv, 'stop-time', unpop_on_error=True)
      except GetoptError:
        stop_dt = time.time()
    force = options.force
    imgpath = options.imgpath
    spd = options.spd
    show_image = options.show_image
    stacked = options.stacked
    event_labels = options.event_labels
    figure = spd.plot(
        start,
        stop,
        key_specs=argv,
        tz=tz,
        event_labels=event_labels,
        stacked=stacked
    )
    if imgpath:
      save_figure(figure, imgpath, force=force)
      if show_image:
        os.system(shlex.join(['open', imgpath]))
    else:
      print_figure(figure)

  def cmd_pull(self, argv):
    ''' Usage: {cmd} [-d dataset,...] [-F rsync-source] [-nx]
          Fetch and import data.
          -d dataset,...
                Specify the datasets to import.
          -F    Fetch rsync source, default from ${DEFAULT_FETCH_SOURCE_ENVVAR}.
          -n    No action; pass -n to rsync. Just more convenient than putting it at the end.
          -x    Delete source files.
    '''
    options = self.options
    options.datasets = self.ALL_DATASETS
    options.expunge = False
    self.popopts(
        argv,
        options,
        d_=('datasets', lambda opt: opt.split(',')),
        F_='fetch_source',
        n='dry_run',
        x='expunge',
    )
    doit = options.doit
    datasets = options.datasets
    expunge = options.expunge
    fetch_source = options.fetch_source
    fetch_argv = []
    if fetch_source:
      fetch_argv.extend(['-F', fetch_source])
    if not doit:
      fetch_argv.append('-n')
    if expunge:
      fetch_argv.append('-x')
    xit = self.cmd_fetch(fetch_argv)
    if xit == 0:
      import_argv = ['--', *argv]
      xit = self.cmd_import(import_argv, datasets=datasets, doit=doit)
    return xit

if __name__ == '__main__':
  sys.exit(main(sys.argv))
