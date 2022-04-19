#!/usr/bin/env python3

''' Assorted utility functions for working with data
    downloaded from Selectronics' SP-LINK programme
    which communicates with their controllers.
'''

from datetime import datetime
from functools import partial
import os
from os.path import join as joinpath
import sys

import arrow

from cs.cmdutils import BaseCommand
from cs.csvutils import csv_import
from cs.deco import cachedmethod
from cs.fs import HasFSPath, needdir
from cs.pfx import pfx, pfx_call, Pfx
from cs.timeseries import (
    TimeSeriesDataDir,
    TimespanPolicyAnnual,
)

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

SPLINK_LOG_INTERVAL = 900  # really? 15 minutes? ugh

def main(argv=None):
  ''' SP-Link command line mode.
  '''
  return SPLinkCommand(argv).run()

class SPLinkCommand(BaseCommand):
  ''' Command line to wrk with SP-Link data downloads.
  '''

  EVENTS_DATASETS = 'EventData',
  TIMESERIES_DATASETS = 'DetailedData', 'DailySummaryData'
  ALL_DATASETS = EVENTS_DATASETS + TIMESERIES_DATASETS

  USAGE_KEYWORDS = {'ALL_DATASETS': ' '.join(sorted(ALL_DATASETS))}

  def cmd_import(self, argv):
    ''' Usage: {cmd} sp-link-dirpath timeseries-dirpath [datasets...]
          Import CSV data from sp-link-dirpath into the time series data
          directories under timeseries-dirpath.
          Default datasets: {ALL_DATASETS}
    '''
    csv_dirpath = self.popargv(argv, "sp-link-dirpath", str, isdirpath)
    over_tsdirpath = self.popargv(argv, "timeseries-dirpath", str, isdirpath)
    if not argv:
      argv = list(self.ALL_DATASETS)
    csvdir = SPLinkCSVDir(csv_dirpath)
    needdir(over_tsdirpath)
    for dataset in argv:
      with Pfx(dataset):
        if dataset in self.TIMESERIES_DATASETS:
          tsdirpath = joinpath(over_tsdirpath, dataset)
          needdir(tsdirpath)
          with SPLinkDataDir(tsdirpath, dataset) as spd:
            spd.import_from(csv_dirpath)
        elif dataset in self.EVENTS_DATASETS:
          dbpath = joinpath(over_tsdirpath, 'events.sqlite')
          db = SQLTags(dbpath)
          short_csvpath = shortpath(csvdir.csvpath(dataset))
          for when, tags in progressbar(
              csvdir.csv_tagsets(dataset),
              short_csvpath,
              update_frequency=8,
              report_print=True,
          ):
            tags['dataset'] = dataset
            db.default_factory(None, unixtime=when, tags=tags)
        else:
          raise GetoptError(
              "do no know how to process dataset, I know: events=%s, timeseries=%s"
              % (
                  ",".join(self.EVENTS_DATASETS),
                  ",".join(self.TIMESERIES_DATASETS),
              )
          )

  def cmd_plot(self, argv):
    ''' Usage: {cmd} [--show] timeseries-dirpath imagepath.png days {{glob|field}}...
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
    if not argv:
      raise GetoptError("missing datadir")
    datadirpath = self.popargv(argv, "data directory", str, isdirpath)
    imgpath = self.popargv(
        argv, "tspath", str, lambda path: not existspath(path),
        "already exists"
    )
    days = self.popargv(argv, int, "days to display", lambda days: days > 0)
    spd = SPLinkDataDir(datadirpath, 'DetailedData')
    spd_fields = sorted(spd.keys())
    spd_fields_s = ", ".join(spd_fields)
    if not argv:
      raise GetoptError("missing fields, I know: " + spd_fields_s)
    ok = True
    if argv:
      keys = spd.keys(argv)
      if not keys:
        raise GetoptError("no matching keys, I know: " + spd_fields_s)
    else:
      raise GetoptError("missing fields")
    now = time.time()
    start = now - days * 24 * 3600
    figure = spd.plot(start, now, keys)
    figure.update_layout(
        dict(
            title=f"Data from {datadirpath}.",
            showlegend=True,
        )
    )
    with Pfx("write %r", imgpath):
      if existspath(imgpath):
        error("already exists")
      else:
        figure.write_image(imgpath, format="png", width=2048, height=1024)
    if show_image:
      os.system(shlex.join(['open', imgpath]))

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
  ''' A class for working with SP-Link data downloads.
  '''
  DEFAULT_LOG_FREQUENCY = 900

  @property
  @cachedmethod
  def sitename(self):
    ''' The site name inferred from a CSV data filename.
    '''
    return self.fnmatch('?*_*_*.CSV')[0].split('_', 1)[0]

  @pfx
  def csvfilename(self, which: str) -> str:
    ''' Return the CSV filename specified by `which`.

        Example:

            self.csvpath('DetailedData')
    '''
    csvfilename, = self.fnmatch(f'*_{which}_*.CSV')
    return csvfilename

  def csvpath(self, which: str) -> str:
    ''' Return the CSV pathname specified by `which`.

        Example:

            self.csvpath('DetailedData')
    '''
    return self.pathto(self.csvfilename(which))

  def csv_tagsets(self, which: str):
    ts2001_offset = ts2001_unixtime()
    csvpath = self.csvpath(which)
    rowtype, rows = csv_import(csvpath)
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

  # pylint: disable=too-many-locals
  def export_to_timeseries(
      self, which: str, tsd: TimeSeriesDataDir, tzname=None
  ):
    ''' Read the CSV file in `self.fspath` specified by `which`
        and export its contents into the `tsd:TimeSeriesDataDir.
    '''
    nan = float('nan')
    ts2001 = ts2001_unixtime(tzname)
    # load the DetailedData CSV
    csvpath = self.csvpath(which)
    rowtype, rows = csv_import(csvpath)
    # group the values by key
    keys = rowtype.attributes_
    key0 = keys[0]
    key_values = {key: [] for key in keys}
    for row in rows:
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
      tsks = tsd[key]
      tsks.setitems(key_values[key0], key_values[key])

class SPLinkDataDir(TimeSeriesDataDir):
  ''' A `TimeSeriesDataDir` to hold log data from an SP-Link CSV data download.
      This holds the data from a particular CSV log such as `'DetailedData'`.
  '''

  DEFAULT_POLICY_CLASS = TimespanPolicyAnnual
  DEFAULT_LOG_FREQUENCY = SPLinkCSVDir.DEFAULT_LOG_FREQUENCY

  def __init__(self, dirpath, which: str, step=None, policy=None, **kw):
    ''' Initialise the `SPLinkDataDir`.

        Parameters:
        * `dirpath`: the pathname of the directory holding the downloaded CSV files
        * `which`: which CSV file populates this time series, eg `'DetailedData'`
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
    self.which = which

  def import_from(self, csvdir):
    ''' Import the CSV data from `csvdir` specified by `self.which`.

        Parameters:
        * `csvdir`: a `SPLinkCSVDir` instance or the pathname of a directory
          containing SP-Link CSV download data.
    '''
    if isinstance(csvdir, str):
      csvdir = SPLinkCSVDir(csvdir)
    return csvdir.import_csv_data(self.which, self)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
