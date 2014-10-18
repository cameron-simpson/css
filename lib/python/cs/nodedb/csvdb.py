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
    self.keep_backups = False
    self._loaded = Lock()
    self._loaded.acquire()

  def init_nodedb(self):
    ''' Wait for the first update pass to complete.
    '''
    self._open_csv()
    self._monitor_thread = Thread(target=self._monitor, name="%s._monitor" % (self,))
    self._monitor_thread.daemon = True
    self._monitor_thread.start()
    self._loaded.acquire()
    self._loaded.release()
    self._loaded = None

  @locked
  def _open_csv(self):
    ''' Attach to the shared CSV file.
    '''
    self.csv = SharedCSVFile(self.pathname, eof_markers=True, readonly=self.readonly)

  @locked
  def _close_csv(self):
    self.csv.close()
    self.csv = None

  def close(self):
    ''' Final shutdown: stop monitor thread, detach from CSV file.
    '''
    self._close_csv()
    self._monitor_thread.join()

  def _update(self, csvrow):
    if not isinstance(csvrow, CSVRow):
      raise TypeError("csvrow=%r" % (csvrow,))
    self.csv.put(csvrow)

  def _monitor(self):
    ''' Monitor loop: collect updates from the backend and apply to the NodeDB.
    '''
    first = True
    fromtext = self.nodedb.fromtext
    lastrow = None
    while self.csv is not None:
      csv = self.csv
      if csv is None:
        pass
      else:
        old_state = csv.filestate
        if old_state is not None:
          new_state = FileState(self.pathname)
          if not new_state.samefile(old_state):
            # a new CSV file is there; assume rewritten entirely
            # reconnect and reload
            with self._lock:
              self._close_csv()
              self._open_csv()
            self.nodedb._scrub()
      csv = self.csv
      if csv is None:
        pass
      else:
        for row in csv.foreign_rows(to_eof=True):
          row = resolve_csv_row(row, lastrow)
          t, name, attr, value = row
          value = fromtext(value)
          self.import_csv_row(CSVRow(t, name, attr, value))
          lastrow = row
      if first:
        first = False
        self._loaded.release()

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
