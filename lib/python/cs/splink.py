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

pfx_listdir = partial(pfx_call, os.listdir)

SPLINK_LOG_INTERVAL = 900  # really? 15 minutes? ugh

def main(argv=None):
  ''' SP-Link command line mode.
  '''
  return SPLinkCommand(argv).run()

class SPLinkCommand(BaseCommand):
  ''' Command line to wrk with SP-Link data downloads.
  '''

  LOG_DATASETS = 'DetailedData', 'DailySummaryData'

  USAGE_KEYWORDS = {'LOG_DATASETS': ' '.join(sorted(LOG_DATASETS))}

  def cmd_import(self, argv):
    ''' Usage: {cmd} sp-link-dirpath timeseries-dirpath [datasets...]
          Import CSV data from sp-link-dirpath into the time series data
          directories under timeseries-dirpath.
          Default datasets: {LOG_DATASETS}
    '''
    csv_dirpath, over_tsdirpath = argv.pop(0), argv.pop(0)
    if not argv:
      argv = list(self.LOG_DATASETS)
    needdir(over_tsdirpath)
    for dataset in argv:
      with Pfx(dataset):
        tsdirpath = joinpath(over_tsdirpath, dataset)
        needdir(tsdirpath)
        with SPLinkDataDir(tsdirpath, dataset) as spd:
          spd.import_from(csv_dirpath)

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
  def import_csv_data(self, which: str, tsd: TimeSeriesDataDir, tzname=None):
    ''' Read the CSV file in `self.fspath` specified by `which`
        and import them into the `tsd:TimeSeriesDataDir.
    '''
    nan = float('nan')
    ts2001 = ts2001_unixtime(tzname)
    # load the DetailedData CSV
    csvfilename = self.csvpath(which)
    csvpath = self.pathto(csvfilename)
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
  ''' A `TimeSeriesDataDir` to hold CSV log data from an SP-Link data download.
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
