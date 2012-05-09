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
from .node import _NoBackend

def read_csv_file(fp, skipHeaders=False, noHeaders=False):
  ''' Read a CSV file in vertical format (TYPE,NAME,ATTR,VALUE),
      yield TYPE, NAME, {ATTR: UNPARSED_VALUES}
  '''
  if type(fp) is str:
    with Pfx("read_csv_file(%s)" % (fp,)):
      with open(fp, "rb") as csvfp:
        for csvnode in read_csv_file(csvfp,
                                     skipHeaders=skipHeaders,
                                     noHeaders=noHeaders):
          yield csvnode
    return
  csvnode = None
  with Pfx("csvreader(%s)" % (fp,)):
    r = csv.reader(fp)
    rownum = 0
    if not noHeaders:
      hdrrow = r.next()
      rownum += 1
      if not skipHeaders:
        if hdrrow != ['TYPE', 'NAME', 'ATTR', 'VALUE']:
          raise ValueError, \
                "bad header row, expected TYPE, NAME, ATTR, VALUE but got: %s" \
                % (hdrrow,)
    attrmap = None
    otype = None
    oname = None
    oattr = None
    for row in r:
      rownum += 1
      t, n, attr, value = row
      try:
        value = value.decode('utf-8')
      except UnicodeDecodeError, e:
        warning("row %d: %s, using errors=replace", rownum, e)
        value = value.decode('utf-8', errors='replace')
      if attr.endswith('s'):
        # revert older plural dump format
        warning("loading old plural attribute: %s" % (attr,))
        k, plural = parseUC_sAttr(attr)
        if k is None:
          raise ValueError, "failed to parse attribute name: %s" % (attr,)
        attr = k
      if t == "":
        assert otype is not None
        t = otype
      if n == "":
        assert oname is not None
        n = oname
      if t != otype or n != oname:
        if attrmap is not None:
          yield otype, oname, attrmap
        attrmap = {}
      if attr == "":
        assert oattr is not None
        attr = oattr
        attrmap[attr].append(value)
      else:
        attrmap[attr]=[value]
      otype, oname, oattr = t, n, attr
    if attrmap is not None:
      yield otype, oname, attrmap
    return

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

class Backend_CSVFile(_NoBackend):

  def __init__(self, csvpath, readonly=False):
    self.readonly = readonly
    self.csvpath = csvpath

  def __str__(self):
    return "Backend_CSVFile[%s]" % (self.csvpath,)

  def close(self):
    self.sync()

  def sync(self):
    ''' Update the CSV file.
    '''
    if self.nodedb.readonly:
      error("sync on readonly %s", self)
    else:
      write_csv_file(self.csvpath, self.nodedb.nodedata())

  def iteritems(self):
    for t, name, attrmap in read_csv_file(self.csvpath):
      yield (t, name), attrmap

  def iterkeys(self):
    for item in self.iteritems():
      yield item[0]

  def itervalues(self):
    for item in self.iteritems():
      yield item[1]

if __name__ == '__main__':
  import cs.nodedb.csvdb_tests
  cs.nodedb.csvdb_tests.selftest(sys.argv)
