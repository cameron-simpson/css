#!/usr/bin/python
#
# CSV file backend.
#       - Cameron Simpson <cs@zip.com.au> 02may2010
#

import csv
import io
import os
import os.path
from types import StringTypes
import sys
from cs.logutils import Pfx, error, warning , info, D
from . import NodeDB
from .backend import Backend

def csv_rows(fp, skipHeaders=False, noHeaders=False):
  ''' Read a CSV file in vertical format (TYPE,NAME,ATTR,VALUE) and fill
      in the values implied by empty TYPE, NAME or ATTR columns (previous
      row's value).
      `fp` is the name of a CSV file to parse or an open file.
      `skipHeaders` disables validation of the column header row if true.
      `noHeaders` indicates there is no column header row if true.
  '''
  if isinstance(fp, (str, unicode)):
    with Pfx("csv_rows(%s)" % (fp,)):
      with open(fp, "rb") as csvfp:
        for row in csv_rows(csvfp,
                            skipHeaders=skipHeaders,
                            noHeaders=noHeaders):
          yield row
    return
  with Pfx("csvreader(%s)" % (fp,)):
    r = csv.reader(fp)
    rownum = 0
    if not noHeaders:
      hdrrow = r.next()
      rownum += 1
      with Pfx("row %d" % (rownum,)):
        if not skipHeaders:
          if hdrrow != ['TYPE', 'NAME', 'ATTR', 'VALUE']:
            raise ValueError(
                    "bad header row, expected TYPE, NAME, ATTR, VALUE but got: %s"
                    % (hdrrow,))
    otype = None
    oname = None
    oattr = None
    for row in r:
      rownum += 1
      with Pfx("row %d" % (rownum,)):
        t, name, attr, value = row
        try:
          value = value.decode('utf-8')
        except UnicodeDecodeError, e:
          warning("%s, using errors=replace", e)
          value = value.decode('utf-8', errors='replace')
        if t == "":
          if otype is None:
            raise ValueError("empty TYPE with no preceeding TYPE")
          t = otype
        else:
          otype = t
        if name == "":
          if oname is None:
            raise ValueError("empty NAME with no preceeding NAME")
          name = oname
        else:
          oname = name
        if attr == "":
          if oattr is None:
            raise ValueError("empty ATTR with no preceeding ATTR")
          attr = oattr
        else:
          oattr = attr
        yield t, name, attr, value

def apply_csv_rows(nodedb, fp, skipHeaders=False, noHeaders=False):
  ''' Read CSV data from `fp` as for csv_rows().
      Apply values to the specified `nodedb`.
      Honour the incremental notional for data:
      - a NAME commencing with '=' discards any existing (TYPE, NAME)
        and begins anew.
      - an ATTR commencing with '=' discards any existing ATTR and
        commences the ATTR anew
      - an ATTR commencing with '-' discards any existing ATTR;
        VALUE must be empty
      Otherwise each VALUE is appended to any existing ATTR VALUEs.
  '''
  for t, name, attr, value in csv_rows(fp, skipHeaders=skipHeaders, noHeaders=noHeaders):
    if name.startswith('='):
      name = name[1:]
      nodedb[t, name] = {}
    if attr.startswith('='):
      attr = attr[1:]
      nodedb.setdefault((t, name), {})[attr] = ()
    elif attr.startswith('-'):
      if value != "":
        raise ValueError("ATTR = \"%s\" but non-empty VALUE: %r" % (attr, value))
      nodedb.setdefault((t, name), {})[attr] = ()
      continue
    N = nodedb.get((t, name), doCreate=True)
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
    with Pfx("write_csv_file(%s)" % (fp,)):
      ##with io.open(fp, 'w', io.DEFAULT_BUFFER_SIZE, 'utf-8') as csvfp:
      with open(fp, 'wb') as csvfp:
        write_csv_file(csvfp, nodedata, noHeaders=noHeaders)
    return

  csvw = csv.writer(fp)
  if not noHeaders:
    csvw.writerow( ('TYPE', 'NAME', 'ATTR', 'VALUE') )
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
          wt = u''
        write_csvrow(csvw, wt, name, attr, valuetext)
        attr = u''
        name = u''

def write_csvrow(csvw, t, name, attr, valuetext):
  ''' Encode and write a CSV row.
      Note that `valuetext` is a preserialised value as computed by
      NodeDB.totext(value).
  '''
  # hideous workaround for CSV C module forcing ASCII text :-(
  # compute flat 8-bit encodings for supplied strings
  wt8 = t.encode('utf-8')
  name8 = name.encode('utf-8')
  attr8 = attr.encode('utf-8')
  uvalue = valuetext if isinstance(valuetext, unicode) else unicode(valuetext, 'iso8859-1')
  value8 = uvalue.encode('utf-8')
  csvw.writerow( (wt8, name8, attr8, value8) )

class Backend_CSVFile(Backend):

  def __init__(self, csvpath, readonly=False):
    Backend.__init__(self, readonly=readonly)
    self.csvpath = csvpath

  def close(self):
    self.sync()

  def sync(self):
    ''' Update the CSV file.
    '''
    if self.nodedb.readonly:
      error("sync on readonly %s", self)
    else:
      write_csv_file(self.csvpath, self.nodedb.nodedata())

  def apply_nodedata(self):
    raise NotImplementedError("no %s.apply_nodedata(), apply_to uses incremental mode" % (type(self),))

  def apply_to(self, nodedb):
    apply_csv_rows(nodedb, self.csvpath)
    
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
    self.changed = True

  def delAttr(self, t, name, attr):
    self._append_csv_row(t, name, '-'+attr, '')

  def extendAttr(self, t, name, attr, values):
    for value in values:
      self._append_csv_row(t, name, attr, value)

  def _append_csv_row(self, t, name, attr, value):
    with open(self.csvpath, "ab") as fp:
      ##print >>sys.stderr, "_append_csv_row: t=%r, name=%r, attr=%r, value=%r" % (t, name, attr, value)
      csvw = csv.writer(fp)
      write_csvrow(csvw, t, name, attr, self.nodedb.totext(value))

if __name__ == '__main__':
  import cs.nodedb.csvdb_tests
  cs.nodedb.csvdb_tests.selftest(sys.argv)
