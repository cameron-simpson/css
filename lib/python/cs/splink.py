#!/usr/bin/env python3

''' Assorted utility functions for working with data
    downloaded from Selectronics' SP-LINK programme
    which communicates with their controllers.
'''

from collections import namedtuple
from contextlib import contextmanager
from datetime import datetime
from functools import partial
from getopt import getopt, GetoptError
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
import shlex
import sys
import time

import arrow
from typeguard import typechecked

from cs.context import stackattrs
from cs.csvutils import csv_import
from cs.deco import cachedmethod
from cs.fs import HasFSPath, fnmatchdir, needdir, shortpath
from cs.fstags import FSTags
from cs.lex import s
from cs.logutils import warning, error
from cs.pfx import pfx, pfx_call, Pfx
from cs.progress import progressbar
from cs.psutils import run
from cs.py.modules import import_extra
from cs.resources import MultiOpenMixin
from cs.sqltags import SQLTags
from cs.tagset import TagSet
from cs.timeseries import (
    plot_events,
    TimeSeriesBaseCommand,
    TimeSeriesDataDir,
    TimespanPolicyAnnual,
)
from cs.upd import print, UpdProxy  # pylint: disable=redefined-builtin

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 3 - Alpha",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
    'entry_points': {
        'console_scripts': [
            'splink = cs.splink:main',
        ],
    },
    'extras_requires': {
        'numpy': ['numpy'],
        'pandas': ['pandas'],
        'plotting': ['kaleido', 'plotly'],
    },
}

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
  DEFAULT_LOG_FREQUENCY = 900

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
        and export its contents into the `tsd:TimeSeriesDataDir.
    '''
    return self.export_csv_to_timeseries(
        self.csvpath(dataset), tsd, tzname=tzname
    )

  @staticmethod
  def export_csv_to_timeseries(csvpath, tsd: TimeSeriesDataDir, tzname=None):
    ''' Read the CSV file specified by `cvspath`
        and export its contents into the `tsd:TimeSeriesDataDir.
    '''
    nan = float('nan')
    ts2001 = ts2001_unixtime(tzname)
    rowtype, rows = csv_import(csvpath)
    # group the values by key
    keys = rowtype.attributes_
    key0 = keys[0]
    key_values = {key: [] for key in keys}
    for row in progressbar(
        rows,
        shortpath(csvpath),
        update_frequency=128,
        report_print=True,
    ):
      for key, value in zip(keys, row):
        if key == key0:
          # seconds since 2001-01-01; make UNIX time
          value = int(value) + ts2001
        else:
          try:
            value = int(value)
          except ValueError:
            try:
              value = float(value)
            except ValueError:
              value = nan
        key_values[key].append(value)
    for key in keys[2:]:
      with Pfx(key):
        ts = tsd.makeitem(key)
        ts.tags['csv.header'] = rowtype.name_of_[key]
        ts.setitems(key_values[key0], key_values[key])

# pylint: disable=too-many-ancestors
class SPLinkDataDir(TimeSeriesDataDir):
  ''' A `TimeSeriesDataDir` to hold log data from an SP-Link CSV data download.
      This holds the data from a particular CSV log such as `'DetailedData'`.
      The `SPLinkData` class manages a couple of these and a downloads
      subdirectory and an events `SQLTags`.
  '''

  DEFAULT_POLICY_CLASS = TimespanPolicyAnnual
  DEFAULT_LOG_FREQUENCY = SPLinkCSVDir.DEFAULT_LOG_FREQUENCY

  def __init__(self, dirpath, dataset: str, step=None, policy=None, **kw):
    ''' Initialise the `SPLinkDataDir`.

        Parameters:
        * `dirpath`: the pathname of the directory holding the downloaded CSV files
        * `dataset`: which CSV file populates this time series, eg `'DetailedData'`
        * `step`: optional time series step size,
          default `SPLinkDataDir.DEFAULT_LOG_FREQUENCY`,
          which comes from `SPLinkCSVDir.DEFAULT_LOG_FREQUENCY`
        * `policy`: optional TimespanPolicy` instance;
          if omitted an `TimespanPolicyAnnual` instance will be made
        Other keyword arguments are passed to the `TimeSeriesDataDir`
        initialiser.
    '''
    if step is None:
      step = self.DEFAULT_LOG_FREQUENCY
    if policy is None:
      policy = self.DEFAULT_POLICY_CLASS()
    super().__init__(dirpath, step=step, policy=policy, **kw)
    self.dataset = dataset

  def import_from(self, csv, tzname=None):
    ''' Import the CSV data from `csv` specified by `self.dataset`.

        Parameters:
        * `csv`: an `SPLinkCSVDir` instance or the pathname of a directory
          containing SP-Link CSV download data, or the pathname of a CSV file.

        Example:

            spd = SPLinkDataDir('spdata/DetailedData')
            spd.import_from(
                'spl/PerformanceData_2021-07-04_13-02-38',
                'DetailedData',
            )
    '''
    if isinstance(csv, str):
      with Pfx(csv):
        if isfilepath(csv):
          if not csv.endswith('.CSV'):
            raise ValueError("filename does not end in .CSV")
          try:
            dsinfo = SPLinkData.parse_dataset_filename(csv)
          except ValueError as e:
            warning("unusual filename: %s", e)
          else:
            if dsinfo.dataset != self.dataset:
              raise ValueError(
                  "filename dataset:%r does not match self.dataset:%r" %
                  (dsinfo.dataset, self.dataset)
              )
            csvdir = SPLinkCSVDir(dirname(csv))
            return csvdir.export_csv_to_timeseries(csv, self, tzname=tzname)
          if isdirpath(csv):
            csvdir = SPLinkCSVDir(csv)
            return csvdir.export_to_timeseries(
                self.dataset, self, tzname=tzname
            )
          raise ValueError("neither a CSV file nor a directory")
    if isinstance(csv, SPLinkCSVDir):
      return csv.export_to_timeseries(self.dataset, self, tzname=tzname)
    raise TypeError(
        "expected filesystem path or SPLinkCSVDir, got: %s" % (s(csv),)
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
      raise ValueError("not a directory: %r" % (dirpath,))
    super().__init__(dirpath)
    self._to_close = []

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
          "%s: no .%s attribute" % (type(self).__name__, tsname)
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
    print("RESOLVE", spec)
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
          print("try tsd", tsd, "spec", field_spec)
          for key in pfx_call(tsd.keys, field_spec):
            yield tsd, key

class SPLinkCommand(TimeSeriesBaseCommand):
  ''' Command line to wrk with SP-Link data downloads.
  '''

  GETOPT_SPEC = 'd:n'
  USAGE_FORMAT = r'''Usage: {cmd} [-d spdpath] [-n] subcommand...
    -d spdpath  Specify the directory containing the SP-LInk downloads
                and time series. Default from ${DEFAULT_SPDPATH_ENVVAR},
                or {DEFAULT_SPDPATH!r}
    -n          No action; recite planned actions.'''

  DEFAULT_SPDPATH = '.'
  DEFAULT_SPDPATH_ENVVAR = 'SPLINK_DATADIR'
  DEFAULT_FETCH_SOURCE_ENVVAR = 'SPLINK_FETCH_SOURCE'

  ALL_DATASETS = SPLinkData.EVENTS_DATASETS + SPLinkData.TIMESERIES_DATASETS

  USAGE_KEYWORDS = {
      'ALL_DATASETS': ' '.join(sorted(ALL_DATASETS)),
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

  def cmd_fetch(self, argv):
    ''' Usage: {cmd} [-F rsync-source] [-nx] [-- [rsync-options...]]
          Rsync everything from rsync-source into the downloads area.
          -F    Fetch rsync source, default from ${DEFAULT_FETCH_SOURCE_ENVVAR}.
          -n    Passed to rsync. Just more convenient than putting it at the end.
          -x    Delete source files.
    '''
    options = self.options
    options.expunge = False
    self.popopts(argv, options, F='fetch_source', n='dry_run', x='expunge')
    doit = options.doit
    expunge = options.expunge
    fetch_source = options.fetch_source
    if not fetch_source:
      raise GetoptError(
          f"no fetch source: no ${self.DEFAULT_FETCH_SOURCE_ENVVAR} and no -F option"
      )
    spd = options.spd
    rsopts = ['-ia']
    rsargv = ['set-x', 'rsync']
    rsargv.extend(rsopts)
    if expunge:
      rsargv.append('--delete-source')
    rsargv.extend(argv)
    rsargv.extend(['--', rsync_source + '/', spd.downloadspath + '/'])
    if not doit:
      print(shlex.join(argv))
      return 0
    print('+', shlex.join(argv))
    return run(rsargv)

  # pylint: disable=too-many-statements,too-many-branches,too-many-locals
  def cmd_import(self, argv):
    ''' Usage: {cmd} [-d dataset,...] [-n] [sp-link-download...]
          Import CSV data from the downloads area into the time series data.
          -d datasets       Comma separated list of datasets to import.
                            Default datasets: {ALL_DATASETS}
          -f                Force. Import datasets even is already marked as
                            imported.
          -n                No action. Recite planned imports.
          sp-link-download  Specify specific individual downloads to import.
                            The default is any download not tagged as already
                            imported.
    '''
    options = self.options
    runstate = options.runstate
    doit = options.doit
    force = False
    fstags = options.fstags
    spd = options.spd
    datasets = self.ALL_DATASETS
    badopts = False
    opts, argv = getopt(argv, 'd:n')
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-d':
          datasets = val.split(',')
        elif opt == '-f':
          force = True
        elif opt == '-n':
          doit = False
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
    for path in argv:
      if runstate.cancelled:
        break
      with Pfx(path):
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
          for filename in filenames:
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
            dataset = dsinfo.dataset
            with Pfx(dspath):
              dstags = fstags[dspath]
              if not force and dstags.imported:
                warning("skip, already imported")
                continue
              if dataset in spd.TIMESERIES_DATASETS:
                ts = getattr(spd, dataset)
                if doit:
                  pfx_call(ts.import_from, dspath)
                  dstags['imported'] = 1
                else:
                  print("import", dspath, "=>", ts)
              elif dataset in spd.EVENTS_DATASETS:
                db = spd.eventsdb
                if doit:
                  with UpdProxy(prefix="import %s: " % (shortpath(dspath),)
                                ) as proxy:
                    with db:
                      short_csvpath = shortpath(dspath)
                      proxy.text = "load " + short_csvpath
                      when_tags = sorted(
                          SPLinkCSVDir.csv_tagsets(dspath),
                          key=lambda wt: wt[0]
                      )
                      if when_tags:
                        # subsequent events overlap previous imports,
                        # make sure we only import new events
                        proxy.text = "load preexisting events in this timeframe"
                        existing = set(
                            (
                                (ev.unixtime, ev.event_description)
                                for ev in db.find(
                                    f'unixtime>={when_tags[0][0]}',
                                    f'unixtime<={when_tags[-1][0]}',
                                )
                            )
                        )
                        proxy.text = "winnow existing events"
                        new_when_tags = [
                            wt for wt in when_tags
                            if (wt[0],
                                wt[1]['event_description']) not in existing
                        ]
                        if new_when_tags:
                          proxy.text = "import %d new events" % (
                              len(new_when_tags,)
                          )
                          for when, tags in progressbar(
                              new_when_tags,
                              short_csvpath,
                              update_frequency=8,
                              report_print=True,
                          ):
                            tags['dataset'] = dataset
                            db.default_factory(None, unixtime=when, tags=tags)
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
    return xit

  # pylint: disable=too-many-locals
  def cmd_plot(self, argv):
    ''' Usage: {cmd} [--show] imagepath.png days {{[dataset:]{{glob|field}}}}...
    '''
    try:
      import_extra('plotly', DISTINFO)
    except ImportError as e:
      raise GetoptError(
          "the plotly package is not installed: %s" % (e,)
      ) from e
    show_image = False
    if argv and argv[0] == '--show':
      show_image = True
      argv.pop(0)
    imgpath = self.poparg(
        argv, "tspath", str, lambda path: not existspath(path),
        "already exists"
    )
    days = self.poparg(argv, int, "days to display", lambda days: days > 0)
    if not argv:
      argv = '*'
    options = self.options
    spd = options.spd
    tsd_keys = []
    with Pfx("fields"):
      for spec in argv:
        with Pfx(spec):
          print("try spec", spec)
          matches = list(spd.resolve(spec))
          if matches:
            tsd_keys.extend(spd.resolve(spec))
          else:
            warning("no matches")
    if not tsd_keys:
      raise GetoptError("no fields were resolved")
    now = time.time()
    start = now - days * 24 * 3600
    figure = None
    with UpdProxy(prefix="plot lines: ") as proxy:
      for tsd, key in tsd_keys:
        name = f'{shortpath(tsd.fspath)}:{key}'
        proxy.text = name
        figure = tsd.plot(start, now, figure=figure, keys=(key,), name=name)
    eventsdb = spd.eventsdb
    with UpdProxy(prefix="plot events: ") as proxy:
      for label in [
          'System - AC Source no longer detected',
          'System - AC Source outside operating range',
          'Bridge negative correction',
          'Bridge positive correction',
          'AC Mode - Synchronised begin',
      ]:
        with proxy.extend_prefix(label + ": "):
          proxy.text = "find events"
          events = list(
              eventsdb.find(
                  f"unixtime>={start}",
                  f"unixtime<{now}",
                  event_description=label,
                  dataset='EventData',
              )
          )
          print(len(events), "events found for", repr(label))
          proxy.text = "add trace"
          plot_events(
              figure,
              events,
              lambda ev: ev.ac_load_voltage_instantaneous_v_ac,
              name=label,
              rescale=False,  # True,
          )
    figure.update_layout(dict(
        title=f"Data from {spd}.",
        showlegend=True,
    ))
    with Pfx("write %r", imgpath):
      if existspath(imgpath):
        error("already exists")
      else:
        figure.write_image(imgpath, format="png", width=2048, height=1024)
    if show_image:
      os.system(shlex.join(['open', imgpath]))

if __name__ == '__main__':
  sys.exit(main(sys.argv))
