#!/usr/bin/python
#
# CSV file backend.
#       - Cameron Simpson <cs@zip.com.au> 02may2010
#

import csv
import io
import os
import os.path
import sys
import datetime
from shutil import copyfile
from cs.csvutils import csv_reader, csv_writerow, CatchUp as CSV_CatchUp
from cs.fileutils import lockfile, FileState
from cs.logutils import Pfx, error, warning, info, debug, trace, D
from cs.py3 import StringTypes, Queue_Full as Full, Queue_Empty as Empty
from . import NodeDB
from .backend import Backend, CSVRow

def resolve_csv_row(row, lastrow):
  ''' Transmute a CSV row, resolving omitted TYPE, NAME or ATTR fields.
  '''
  with Pfx("row=%r, lastrow=%r", row, lastrow):
    t, name, attr, value = row
    if t == '':
      t = lastrow[0]
    if name == '':
      name = lastrow[1]
    if attr == '':
      attr = lastrow[2]
    return t, name, attr, value

def write_csv_header(fp):
  ''' Write CSV header row. Used for exported CSV data.
  '''
  csvw = csv.writer(fp)
  csv_writerow( csvw, ('TYPE', 'NAME', 'ATTR', 'VALUE') )

def write_csv_file(fp, nodedata):
  ''' Iterate over the supplied `nodedata`, a sequence of (type, name, attrmap) and write to the file-like object `fp` in the vertical" CSV style.
      `fp` may also be a string in which case the named file
      is truncated and rewritten.
      `attrmap` maps attribute names to sequences of preserialised values as
        computed by NodeDB.totext(value).
  '''
  csvw = csv.writer(fp)
  otype = None
  for t, name, attrmap in nodedata:
    attrs = sorted(attrmap.keys())
    for attr in attrs:
      for valuetext in attrmap[attr]:
        if otype is None or otype != t:
          # new type
          otype = t
          wt = t
        else:
          # same type
          wt = ''
        csv_writerow(csvw, (wt, name, attr, valuetext))
        attr = ''
        name = ''

class Backend_CSVFile(Backend):

  def __init__(self, csvpath, readonly=False, rewrite_inplace=False):
    Backend.__init__(self, readonly=readonly)
    self.pathname = csvpath
    self.rewrite_inplace = rewrite_inplace
    self.csvpath = csvpath
    self.keep_backups = False
    self.lastrow = None

  def _open(self):
    mode = "r" if self.readonly else "r+"
    self.csvfp = open(self.csvpath, mode)
    self.csvstate = FileState(self.csvfp.fileno())

  def _close(self):
    self.csvfp.close()
    self.csvfp = None
    self.csvstate = None

  def lockdata(self):
    ''' Obtain an exclusive lock on the CSV file.
    '''
    return lockfile(self.csvpath)

  def _import_nodedata(self):
    with self._updates_off():
      self._rewind()
      for row in self.fetch_updates():
        self.import_csv_row(row)

  def fetch_updates(self, header_row=None):
    ''' Read CSV update rows from the current file position.
        Yield resolved rows.
    '''
    new_state = FileState(self.csvfp.fileno())
    if not self.csvstate.samefile(new_state):
      # CSV file replaced, reload
      self._close()
      self.nodedb._scrub()
      self._open()
      self._rewind()
    raw_rows = CSV_CatchUp(self.csvfp, self.partial)
    if header_row:
      row = raw_rows.next()
      if row != header_row:
        raise RuntimeError(
              "bad header row, expected %r, got: %r"
              % (header_row, row))
    fromtext = self.nodedb.fromtext
    lastrow = self.lastrow
    for row in raw_rows:
      t, name, attr, value = fullrow = resolve_csv_row(row, lastrow)
      value = fromtext(value)
      yield CSVRow(t, name, attr, value)
      lastrow = fullrow
    self.partial = raw_rows.partial
    self.lastrow = lastrow

  def rewrite(self):
    ''' Force a complete rewrite of the CSV file.
    '''
    trace("rewrite(%s)", self.pathname)
    if self.readonly:
      error("%s: readonly: rewrite not done", self)
      return
    with rewrite_cmgr(self.pathname, backup_ext='', do_rename=True) as outfp:
      write_csv_file(fp, self.nodedb.nodedata())

if __name__ == '__main__':
  from cs.logutils import setup_logging
  setup_logging()
  import cs.nodedb.csvdb_tests
  cs.nodedb.csvdb_tests.selftest(sys.argv)
