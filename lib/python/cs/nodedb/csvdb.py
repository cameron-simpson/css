#!/usr/bin/python
#

''' The CSV file backend for a NodeDB.
    - Cameron Simpson <cs@cskk.id.au> 02may2010
'''

import csv
import sys
import time
from cs.csvutils import csv_writerow
from cs.fileutils import rewrite_cmgr
from cs.logutils import error, warning
from cs.pfx import Pfx, PfxThread, pfx_method, XP
from cs.sharedfile import SharedCSVFile
from cs.threads import locked
from .backend import Backend, Update as BackendUpdate

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

def write_csv_file(fp, nodedata):
  ''' Iterate over the supplied `nodedata`,
      a sequence of `(type,name,attrmap)`,
      and write to the file-like object `fp` in the "vertical" CSV style.

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
  ''' An interface to a cs.csvutils.SharedCSVFile to store nodedb state.
  '''

  def __init__(self, csvpath, readonly=False, rewrite_inplace=False):
    assert rewrite_inplace is False
    Backend.__init__(self, readonly=readonly)
    self.pathname = csvpath
    self._lastrow = (None, None, None, None)
    self.csv = None

  @pfx_method(use_str=True)
  def init_nodedb(self):
    ''' Open the CSV file and wait for the first update pass to complete.
    '''
    assert self.csv is None
    self.csv = SharedCSVFile(self.pathname, read_only=self.readonly)
    # initial scan of the database
    for row in self.csv:
      self._import_backend_row(row)
    self._monitor = PfxThread(
        name="monitor", target=self._monitor_backend_updates, daemon=True
    )
    self._monitor.start()

  @pfx_method(use_str=True)
  def close(self):
    ''' Final shutdown: stop monitor thread, detach from CSV file.
    '''
    self._close_csv()
    self._monitor.join()

  @locked
  def _close_csv(self):
    self.csv.close()
    self.csv = None

  def _monitor_backend_updates(self):
    for row in self.csv.tail():
      self._import_backend_row(row)
      if self.csv.closed:
        break

  def _import_backend_row(self, row0):
    ''' Apply the values from an individual CSV update row
        to the NodeDB without propagating to the backend.

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
    nodedb = self.nodedb
    value = nodedb.fromtext(value)
    for update in BackendUpdate.from_csv_row((t, name, attr, value)):
      nodedb._update_local(update)

  @pfx_method
  def _update(self, update):
    ''' Update the backing store from an update csvrow.
    '''
    if self.readonly:
      warning("%s: readonly, discarding: %s", self.pathname, update)
      return
    with self.csv.writer() as w:
      for row in update.to_csv():
        w.writerow(row)

  def rewrite(self):
    ''' Force a complete rewrite of the CSV file.
    '''
    if self.readonly:
      error("%s: readonly: rewrite not done", self)
      return
    with rewrite_cmgr(self.pathname, backup_ext='', do_rename=True) as outfp:
      write_csv_file(outfp, self.nodedb.nodedata())

if __name__ == '__main__':
  from cs.logutils import setup_logging
  setup_logging()
  from . import NodeDBFromURL
  NDB = NodeDBFromURL('file-csv://test.csv', readonly=False)
  N = NDB.make('T:1')
  print(N)
  N.A = 1
  print(N)
  time.sleep(2)
  NDB.close()
  sys.exit(0)
  import cs.nodedb.csvdb_tests
  cs.nodedb.csvdb_tests.selftest(sys.argv)
