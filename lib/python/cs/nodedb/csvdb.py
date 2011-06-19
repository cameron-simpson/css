#!/usr/bin/python
#
# CSV file backend.
#       - Cameron Simpson <cs@zip.com.au> 02may2010
#

import csv
import os
import os.path
from types import StringTypes
import unittest
import sys
from cs.logutils import Pfx, error, warn , info
from . import NodeDB, Backend
from .node import TestAll as NodeTestAll

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
  r = csv.reader(fp)
  if not noHeaders:
    hdrrow = r.next()
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
    t, n, attr, value = row
    if attr.endswith('s'):
      # revert older plural dump format
      warn("loading old plural attribute: %s" % (attr,))
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
      with open(fp, "wb") as csvfp:
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
          wt = ""
        w.writerow( (wt, name, attr, value) )
        attr = ""
        name = ""

class Backend_CSVFile(Backend):

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
    if not self.nodedb.readonly:
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

  def extendAttr(self, t, name, attr, values):
    assert len(values) > 0
    assert not self.nodedb.readonly

  def set1Attr(self, t, name, attr, value):
    assert not self.nodedb.readonly

  def delAttr(self, t, name, attr):
    assert not self.nodedb.readonly

  def __setitem__(self, key, value):
    assert not self.nodedb.readonly

  def __delitem(self, key):
    assert not self.nodedb.readonly

class TestAll(NodeTestAll):

  def setUp(self):
    dbpath = 'test.csv'
    self.dbpath = dbpath
    if os.path.exists(dbpath):
      os.remove(dbpath)
    with open(dbpath, "wb") as fp:
      fp.write("TYPE,NAME,ATTR,VALUE\n")
    self.backend=Backend_CSVFile(dbpath)
    self.db=NodeDB(backend=self.backend)

  def test22persist(self):
    N = self.db.newNode('HOST:foo1')
    N.X=1
    N2 = self.db.newNode('SWITCH:sw1')
    N2.Ys=(9,8,7)
    dbstate = str(self.db)
    self.db.close()
    self.db=NodeDB(backend=Backend_CSVFile(self.dbpath))
    dbstate2 = str(self.db)
    self.assert_(dbstate == dbstate2, "db state differs:\n\t%s\n\t%s" % (dbstate, dbstate2))

  def tearDown(self):
    self.db.close()

if __name__ == '__main__':
  unittest.main()
