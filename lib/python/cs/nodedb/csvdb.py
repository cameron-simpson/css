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
import time
from shutil import copyfile
from threading import Thread, Lock
from cs.debug import trace
from cs.csvutils import csv_writerow, SharedCSVFile
from cs.fileutils import FileState, rewrite_cmgr
from cs.logutils import Pfx, error, warning, info, debug, D, X
from cs.threads import locked
from cs.py3 import StringTypes, Queue_Full as Full, Queue_Empty as Empty
from . import NodeDB
from .backend import Backend, ResetUpdate, ExtendUpdate

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
    self.keep_backups = False
    self._lastrow = (None, None, None, None)

  def init_nodedb(self):
    ''' Open the CSV file and wait for the first update pass to complete.
    '''
    self._open_csv()
    self.csv.ready()

  def _csv_to_Update(self, row):
    ''' Decode a CSV row into Backend._Update instances.
        Yield _Updates.
        Honour the incremental notation for data:
        - a NAME commencing with '=' discards any existing (TYPE, NAME)
          and begins anew.
        - an ATTR commencing with '=' discards any existing ATTR and
          commences the ATTR anew
        - an ATTR commencing with '-' discards any existing ATTR;
          VALUE must be empty
        Otherwise each VALUE is appended to any existing ATTR VALUEs.
    '''
    t, name, attr, value = row
    if name.startswith('='):
      # reset Node, optionally commence attribute
      yield ResetUpdate(t, name[1:])
      if attr != "":
        yield ExtendUpdate(t, name[1:], attr, (value,))
    elif attr.startswith('='):
      yield ExtendUpdate(t, name, attr[1:], (value,))
    elif attr.startswith('-'):
      if value != "":
        raise ValueError("reset CVS row: value != '': %r" % (row,))
      yield ResetUpdate(t, name, attr[1:])
    else:
      yield ExtendUpdate(t, name, attr, (value,))

  def _Update_to_csv(self, update):
    ''' Encode a Backend._Update into CSV rows.
    '''
    do_append, t, name, attr, values = update
    if do_append:
      # straight value appends
      for value in values:
        yield t, name, attr, value
    else:
      if attr is None:
        # reset whole Node
        if values:
          raise ValueError("values supplied when attr is None: %r" % (values,))
        yield t, '=' + name, "", ""
      else:
        # reset attr values
        if values:
          # reset values
          first = True
          for value in values:
            if first:
              yield t, name, '=' + attr, value
              first = False
            else:
              yield t, name, attr, value
        else:
          # no values - discard whole attr
          yield t, name, '-' + attr, ""

  def import_foreign_row(self, row0):
    ''' Apply the values from an individual CSV update row to the NodeDB without propagating to the backend.
        Each row is expected to be post-resolve_csv_row().
        Honour the incremental notation for data:
        - a NAME commencing with '=' discards any existing (TYPE, NAME)
          and begins anew.
        - an ATTR commencing with '=' discards any existing ATTR and
          commences the ATTR anew
        - an ATTR commencing with '-' discards any existing ATTR;
          VALUE must be empty
        Otherwise each VALUE is appended to any existing ATTR VALUEs.
    '''
    if row0 is None:
      return
    row = resolve_csv_row(row0, self._lastrow)
    self._lastrow = row
    t, name, attr, value = row
    value = nodedb.fromtext(value)
    nodedb = self.nodedb
    for update in self._csv_to_Update( (t, name, attr, value) ):
      nodedb._update_local(update)

  @locked
  def _open_csv(self):
    ''' Attach to the shared CSV file.
    '''
    self.csv = SharedCSVFile(self.pathname,
                             importer=self.import_foreign_row,
                             readonly=self.readonly)

  @locked
  def _close_csv(self):
    self.csv.close()
    self.csv = None

  def close(self):
    ''' Final shutdown: stop monitor thread, detach from CSV file.
    '''
    self._close_csv()

  def _update(self, update):
    ''' Update the backing store from an update csvrow.
    '''
    if self.readonly:
      warning("%s: readonly, discarding: %s", self.pathname, csvrow)
      return
    for row in self._Update_to_csv(update):
      self.csv.put(row)

  def rewrite(self):
    ''' Force a complete rewrite of the CSV file.
    '''
    if self.readonly:
      error("%s: readonly: rewrite not done", self)
      return
    with rewrite_cmgr(self.pathname, backup_ext='', do_rename=True) as outfp:
      write_csv_file(fp, self.nodedb.nodedata())

if __name__ == '__main__':
  import time
  from cs.logutils import setup_logging
  setup_logging()
  from . import NodeDBFromURL
  NDB=NodeDBFromURL('file-csv://test.csv', readonly=False)
  N=NDB.make('T:1')
  print(N)
  N.A=1
  print(N)
  time.sleep(2)
  NDB.close()
  sys.exit(0)
  import cs.nodedb.csvdb_tests
  cs.nodedb.csvdb_tests.selftest(sys.argv)
