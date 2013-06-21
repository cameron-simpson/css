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
from collections import namedtuple
from shutil import copyfile
from threading import Thread
from cs.csvutils import csv_reader, csv_writerow, catchup as csv_catchup
from cs.debug import Lock, RLock
from cs.fileutils import lockfile, FileState
from cs.logutils import Pfx, error, warning, info, D
from cs.threads import locked_property
from cs.timeutils import sleep
from cs.py3 import StringTypes, Queue, Queue_Full as Full, Queue_Empty as Empty
from . import NodeDB
from .backend import Backend

CSVRow = namedtuple('CSVRow', 'type name attr value')

# used to reset state in the don't-repeat-type/name/attr mode
NullCSVRow = CSVRow(None, None, None, None)

def read_csv_rows(lines, noHeaders=False):
  ''' Read a CSV file in vertical format (TYPE,NAME,ATTR,VALUE) and fill
      in the values implied by empty TYPE, NAME or ATTR columns (previous
      row's value).
      `lines` is an interable or the name of a CSV file.
      `noHeaders` indicates there is no column header row if true.
  '''
  if isinstance(lines, StringTypes):
    # was "rb"
    with open(lines, "r") as csvfp:
      for row in read_csv_rows(csvfp,
                          noHeaders=noHeaders):
        yield row
    return

  r = csv_reader(lines, encoding='utf-8')
  if not noHeaders:
    hdrrow = next(r)
    if hdrrow != ['TYPE', 'NAME', 'ATTR', 'VALUE']:
      raise ValueError(
              "bad header row, expected TYPE, NAME, ATTR, VALUE but got: %s"
              % (hdrrow,))
  oldrow = NullCSVRow
  for row in rows:
    row = resolve_csv_row(row, lastrow)
    yield row
    lastrow = row

def resolve_csv_row(row, lastrow):
  ''' Transmute a CSV row, resolving omitted TYPE, NAME or ATTR fields.
  '''
  D("RCR: row %d =%r, lastrow=%r", len(row), row, lastrow)
  t, name, attr, value = row
  if t == '':
    t = lastrow.type
  if name == '':
    name = lastrow.name
  if attr == '':
    attr = lastrow.attr
  return CSVRow(t, name, attr, value)

def apply_csv_rows(nodedb, rows):
  ''' Apply the `rows` to a NodeDB `nodedb`.
      Rows is expected to be post-resolve_csv_row().
      Honour the incremental notation for data:
      - a NAME commencing with '=' discards any existing (TYPE, NAME)
        and begins anew.
      - an ATTR commencing with '=' discards any existing ATTR and
        commences the ATTR anew
      - an ATTR commencing with '-' discards any existing ATTR;
        VALUE must be empty
      Otherwise each VALUE is appended to any existing ATTR VALUEs.
  '''
  for row in rows:
    apply_csv_row(nodedb, row)

def apply_csv_row(nodedb, row):
  ''' Apply the values from an individual CSV row.
      Apply values to the specified `nodedb`.
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
  D("row = %r", row)
  t, name, attr, value = row
  D("row ok")
  if attr.startswith('-'):
    # remove attribute
    attr = attr[1:]
    if value != "":
      raise ValueError("ATTR = \"%s\" but non-empty VALUE: %r" % (attr, value))
    N[attr] = ()
  else:
    # add attribute
    if name.startswith('='):
      # discard node and start anew
      name = name[1:]
      nodedb[t, name] = {}
    N = nodedb.make( (t, name) )
    if attr.startswith('='):
      # reset attribute completely before appending value
      attr = attr[1:]
      N[attr] = ()
    N.get(attr).append(nodedb.fromtext(value))

def write_csv_file(fp, nodedata, noHeaders=False):
  ''' Iterate over the supplied `nodedata`, a sequence of:
        type, name, attrmap
      and write to the file-like object `fp` in the vertical" CSV
      style. `fp` may also be a string in which case the named file
      is truncated and rewritten.
      `attrmap` maps attribute names to sequences of preserialised values as
        computed by NodeDB.totext(value).
  '''
  if type(fp) is str:
    with Pfx("write_csv_file(%s)", fp):
      ##with io.open(fp, 'w', io.DEFAULT_BUFFER_SIZE, 'utf-8') as csvfp:
      with open(fp, 'w') as csvfp:
        write_csv_file(csvfp, nodedata, noHeaders=noHeaders)
    return

  csvw = csv.writer(fp)
  if not noHeaders:
    csv_writerow( csvw, ('TYPE', 'NAME', 'ATTR', 'VALUE') )
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

  def __init__(self, csvpath, readonly=False):
    Backend.__init__(self, readonly=readonly)
    self.keep_backups = False
    self.csvpath = csvpath
    self._updateQ = Queue(1024)
    self._update_lock = RLock()
    self._rewind()
    self._lock = Lock()

  @locked_property
  def _update_thread(self):
    T = Thread(target=self._monitor, args=(self._updateQ,))
    D("start monitor thread...")
    T.start()
    return T

  def _queue(self, row):
    if self.readonly:
      D("readonly: do not queue %r", row)
    else:
      D("queue %r", row)
      self._updateQ.put(row)

  def close(self):
    Backend.close(self)
    self._update_thread.join()

  def sync(self):
    ''' Update the CSV file.
    '''
    if self.changed:
      if self.readonly:
        error("%s: readonly: sync not done", self)
      else:
        with lockfile(self.csvpath):
          backup = "%s.bak-%s" % (self.csvpath, datetime.datetime.now().isoformat())
          copyfile(self.csvpath, backup)
          write_csv_file(self.csvpath, self.nodedb.nodedata())
        if not self.keep_backups:
          os.remove(backup)
        self.changed = False

  def rewrite(self):
    ''' Force a complete rewrite of the CSV file.
    '''
    self.changed = True
    self.sync()

  def apply_nodedata(self):
    raise NotImplementedError("no %s.apply_nodedata(), apply_to uses incremental mode" % (type(self),))

  def apply_to(self, nodedb):
    ''' Read the CSV file and apply the contents to the NodeDB.
        The CSV file is expected to have a header row.
    '''
    T = self._update_thread
    with self._update_lock:
      self._rewind()
      self._monitor_read_updates()

  def iteritems(self):
    for t, name, attrmap in read_csv_file(self.csvpath):
      yield (t, name), attrmap

  def iterkeys(self):
    for item in self.iteritems():
      yield item[0]

  def itervalues(self):
    for item in self.iteritems():
      yield item[1]

  def __setitem__(self, key, N):
    # CSV DB Nodes only have attributes
    t = N.type
    name = N.name
    for k, v in N.items():
      self.setAttr(t, name, k, v)

  def delAttr(self, t, name, attr):
    ''' Delete an attribute.
    '''
    self._queue(CSVRow(t, name, '-'+attr, ''))

  def extendAttr(self, t, name, attr, values):
    ''' Append values to an attribute.
    '''
    for value in values:
      self._queue(CSVRow(t, name, attr, value))

  def _rewind(self):
    with self._update_lock:
      D("_rewind %r", self.csvpath)
      self.csvstate = None        # tracked by _monitor_read_updates
      self.partial = ''           # tracked by _monitor_read_updates
      self.lastrow = NullCSVRow
      if self.readonly:
        self.fp = open(self.csvpath, "r")
      else:
        self.fp = open(self.csvpath, "r+")

  def _monitor(self, updateQ):
    delay = 0.1
    writing = False
    while True:
      # run until self.closed and updateQ.empty
      # to ensure all updates get written to the CSV file
      is_empty = updateQ.empty()
      if is_empty:
        if self.closed:
          break
        # poll the file for updates
        self._monitor_read_updates()
      else:
        # log updates to the file
        self._monitor_write_updates(updateQ, delay)
      sleep(delay)

  def _monitor_read_updates(self):
    ''' Read CSV data from the current position of self.fp
        and update the NodeDB accordingly.
    '''
    ##D("_monitor_read_updates ...")
    with self._update_lock:
      # optimisation: do nothing if file unchanged
      old_csvstate = self.csvstate
      csvstate = FileState(self.csvpath)
      if old_csvstate is not None and csvstate == old_csvstate:
        # file unchanged - avoid a lot of extra work
        return
      D("CSV file changed: save new state %r for %r", csvstate, self.csvpath)
      self.csvstate = csvstate

      nodedb = self.nodedb
      if nodedb is None:
        raise RuntimeError("self.nodedb = None!")
      lastrow = NullCSVRow
      for row in csv_catchup(self.fp, self.partial):
        if isinstance(row, str):
          # last item is the left over partial line; save it
          self.partial = row
          break
        row = resolve_csv_row(row, lastrow)
        apply_csv_row(nodedb, row)
        lastrow = row

  def _monitor_write_updates(self, updateQ, delay):
    ''' Copy current updates from updateQ and append the the CSV file.
        Process:
          take lock
            catch up on outside updates
            write our updates
          release lock
    '''
    D("_monitor_write_updates ...")
    if updateQ.empty():
      error("_monitor_write_updates: updateQ is empty! should not happen!")
      return
    if self.readonly:
      raise RuntimeError("_monitor_write_updates called but we are readonly!")
    D("getting _update_lock...")
    with self._update_lock:
      D("locked, getting lockfile")
      with lockfile(self.csvpath):
        D("lockfiled, reading updates")
        self._monitor_read_updates()
        D("updated")
        if self.partial:
          error("_monitor_write_updates: incomplete data from _monitor_read_updates: %r", self.partial)
        elif self.fp.tell() != os.fstat(self.fp.fileno()).st_size:
          error("_monitor_write_updates: partial is empty, but fp.tell != ft.st_size: %r vs %r",
                  self.fp.tell(), os.fstat(self.fp.fileno()).st_size)
        else:
          # now write CSV rows to the file and flush
          csvw = csv.writer(self.fp)
          lastrow = NullCSVRow
          while True:
            try:
              t, name, attr, value = updateQ.get(True, delay)
            except Empty:
              break
            if lastrow.type is not None and t == lastrow.type:
              t = ''
            if lastrow.name is not None and name == lastrow.name:
              name = ''
            if attr[0].isalpha() and lastrow.attr is not None and attr == lastrow.attr:
              name = ''
            row = CSVRow(t, name, attr, value)
            warning("_monitor_write_updates: append row: %s", row)
            csv_writerow(csvw, row)
            lastrow = row
          D("flush csv file")
          self.fp.flush()

if __name__ == '__main__':
  from cs.logutils import setup_logging
  setup_logging()
  import cs.nodedb.csvdb_tests
  cs.nodedb.csvdb_tests.selftest(sys.argv)
