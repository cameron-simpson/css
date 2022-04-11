#!/usr/bin/env python3

''' Assorted utility functions for working with data from Selectronics' SP-LINK programme which communicates with their controllers.
'''

import arrow
from datetime import datetime
from fnmatch import fnmatch
from functools import partial
import os
from os.path import join as joinpath
import sys

import pytz

from cs.cmdutils import BaseCommand
from cs.csvutils import csv_import
from cs.fs import HasFSPath
from cs.pfx import pfx, pfx_call
from cs.timeseries import (
    TimeSeriesDataDir,
    TimespanPolicyAnnual,
    get_default_timezone_name,
)

from cs.x import X

pfx_listdir = partial(pfx_call, os.listdir)

SPLINK_LOG_INTERVAL = 900  # really? 15 minutes? ugh

def main(argv=None):
  return SPLinkCommand(argv).run()

class SPLinkCommand(BaseCommand):

  def cmd_import(self, argv):
    ''' Usage: {cmd} sp-link-dirpath timeseries-dirpath
          Import the DetailedData CSV data from sp-link-dirpath into the time
          series data directory at timeseries-dirpath.
    '''
    csv_dirpath, tsdirpath = argv
    with SPLinkDataDir(tsdirpath) as spd:
      spd.import_from(csv_dirpath, 'DetailedData')

def ts2001_unixtime(tzname=None):
  ''' Convert an SP-Link seconds-since-2001-01-01-local-time offset
      into a UNIX time.
  '''
  if tzname is None:
    tzname = 'local'
  a2001 = arrow.get(datetime(2001, 1, 1, 0, 0, 0), tzname)
  unixtime = a2001.timestamp()
  X("a2001 %s, unixtime %s", a2001, unixtime)
  return unixtime

class SPLinkCSVDir(HasFSPath):
  ''' A class for working with SP-Link data downloads.
  '''
  DEFAULT_LOG_FREQUENCY = 900

  def __init__(self, dirpath):
    super().__init__(dirpath)

  @pfx
  def csvpath(self, which: str) -> str:
    ''' Return the CSV filename specified by `which`.
    '''
    csvfilename, = self.fnmatch(f'*_{which}_*.CSV')
    return csvfilename

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
  '''
  DEFAULT_POLICY_CLASS = TimespanPolicyAnnual
  DEFAULT_LOG_FREQUENCY = SPLinkCSVDir.DEFAULT_LOG_FREQUENCY

  def __init__(self, dirpath, step=None, policy=None, **kw):
    if step is None:
      step = self.DEFAULT_LOG_FREQUENCY
    if policy is None:
      policy = self.DEFAULT_POLICY_CLASS()
    super().__init__(dirpath, step=step, policy=policy, **kw)

  def import_from(self, csvdir, which: str):
    ''' Import the CSV data from `csvdir` specified by `which`.

        Parameters:
        * `csvdir`: a `SPLinkCSVDir` instance or the pathname of a directory
          containing SP-Link CSV download data.
        * `which`: which CSV file to import, for example `'DetailedData'`
    '''
    if isinstance(csvdir, str):
      csvdir = SPLinkCSVDir(csvdir)
    return csvdir.import_csv_data(which, self)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
