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
        t, n, attr, value = row
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
        if n == "":
          if oname is None:
            raise ValueError("empty NAME with no preceeding NAME")
          n = oname
        else:
          oname = n
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
    nodedb[t, name][attr].append(value)

def write_csv_file(fp, nodedata, noHeaders=False):
  ''' Iterate over the supplied `nodedata`, a sequence of:
        type, name, attrmap
      and write to the file-like object `fp` in the vertical" CSV
      style. `fp` may also be a string in which case the named file
      is truncated and rewritten.
      `attrmap` maps attribute names to sequences of serialised values.
  '''
  if type(fp) is str:
    with Pfx("write_csv_file(%s)" % (fp,)):
      ##with io.open(fp, 'w', io.DEFAULT_BUFFER_SIZE, 'utf-8') as csvfp:
      with open(fp, 'wb') as csvfp:
        write_csv_file(csvfp, nodedata, noHeaders=noHeaders)
    return

  w = csv.writer(fp)
  if not noHeaders:
    w.writerow( ('TYPE', 'NAME', 'ATTR', 'VALUE') )
  otype = None
  for t, name, attrmap in nodedata:
    attrs = sorted(attrmap.keys())
    for attr in attrs:
      for value in attrmap[attr]:
        if otype is None or otype != t:
          # new type
          otype = t
          wt = t
        else:
          # same type
          wt = u''
        ## # hideous workaround for CSV C module forcing ascii text :-(
        wt8 = wt.encode('utf-8')
        name8 = name.encode('utf-8')
        attr8 = attr.encode('utf-8')
        uvalue = value if type(value) else unicode(value, 'iso8859-1')
        value8 = uvalue.encode('utf-8')
        w.writerow( (wt8, name8, attr8, value8) )
        attr = u''
        name = u''

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

if __name__ == '__main__':
  import cs.nodedb.csvdb_tests
  cs.nodedb.csvdb_tests.selftest(sys.argv)
