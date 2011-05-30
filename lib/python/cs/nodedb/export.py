#!/usr/bin/python -tt
#
# Export and import NodeDB data.
#       - Cameron Simpson <cs@zip.com.au> 07may2011
#

import os
import sys
import tempfile
import unittest
import csv
import cs.sh
from cs.logutils import Pfx

def export_rows_wide(nodes, attrs=None, all_attrs=False, tokenised=False, all_nodes=False):
  ''' Yield node data, unescaped, suitable for use as CSV export data
      in the "wide" format:
        type,name,attr1,attr2...
      with None used for gaps.
      By default, only the specified `attrs` are yielded.
      If the `attrs` are not specified or None, all the attributes
      of the supplied Nodes are yielded.
      If the `attrs` are specified but `all_attrs` is True, the
      specified attrs occupy the first columns and any other
      attributes of the Nodes are supplied in the later columns.
      If the `tokenised` parameter is true, the attribute values
      are yielded as human friendly tokens instead of their raw
      values.
      If the `all_nodes` parameter is true and `attrs` is specified,
      nodes which do not have attributes in `attrs` are still
      included in the exported rows. Otherwise these nodes are
      omitted.
  '''
  other_attrs = set()
  if attrs is None or all_attrs:
    oattrs = attrs
    nodes = tuple(nodes)
    attrs = set()
    for N in nodes:
      if oattrs is None:
        attrs.update(N.keys())
      else:
        for k in N.keys():
          if k not in oattrs:
            other_attrs.add(k)
    if oattrs is None:
      attrs = sorted(attrs)
  if type(attrs) is not list:
    attrs = list(attrs)
  other_attrs = sorted(other_attrs)
  attrs = attrs + other_attrs

  # header row
  yield ['TYPE', 'NAME'] + attrs

  # data
  blank = "" if tokenised else None
  for N in nodes:
    maxlen = max( len(N.get(attr, ())) for attr in attrs )
    if maxlen == 0 and all_nodes:
      maxlen = 1
    for i in range(maxlen):
      if i == 0:
        row = [N.type, N.name]
      else:
        row = [blank, blank]
      for attr in attrs:
        values = N.get(attr, ())
        if len(values) > i:
          elem = values[i]
          if tokenised:
            elem = N.nodedb.totoken(elem, node=N, attr=attr)
          row.append(elem)
        else:
          row.append(blank)
      yield row

def export_csv_wide(csvfile, nodes, attrs=None, all_attrs=False, all_nodes=False):
  if type(csvfile) is str:
    with Pfx(csvfile):
      with open(csvfile, "w") as csvfp:
        export_csv_wide(csvfp, nodes, attrs=attrs, all_attrs=all_attrs, all_nodes=all_nodes)
    return

  # csv.QUOTE_NONNUMERIC
  w = csv.writer(csvfile)
  for row in export_rows_wide(nodes,
                              attrs=attrs,
                              all_attrs=all_attrs,
                              all_nodes=all_nodes,
                              tokenised=True):
    w.writerow(row)
  csvfile.flush()

def import_rows_wide(rows):
    ''' Read "wide" format rows and yield:
          type, name, {attr1:values1, attr2:values2, ...}
        Input row layout is:
          TYPE, NAME, attr1, attr2, ...
          t1, n1, v1, v2, ...
            ,   ,   , v2a, ...
        etc.
    '''
    otype = None
    oname = None
    valuemap = None
    nrows = 0
    for row in rows:
      nrows += 1

      if nrows == 1:
        hdrrow = row
        if len(hdrrow) < 2:
          raise ValueError, "header row too short, expected at least TYPE and NAME: %s" % (hdrrow,)
        if hdrrow[0] != 'TYPE':
          raise ValueError, "header row: element 0 should be 'TYPE', got: %s" % (`hdrrow[0]`,)
        if hdrrow[1] != 'NAME':
          raise ValueError, "header row: element 1 should be 'NAME', got: %s" % (`hdrrow[1]`,)
        continue

      if len(row) > len(hdrrow):
        raise ValueError, "row %d: too many columns - expected %d, got %d" \
                          (nrows, len(hdrrow), len(row))
      if len(row) < len(hdrrow):
        # rows may come from human made CSV data - accept and pad short rows
        row.extend( [ None for i in range(len(hdrrow)-len(row)) ] )

      t, n = row[:2]
      if t == "":
        t = otype
      if n == "":
        n = oname
      if t != otype or n != oname:
        # new Node, yield previous Node data
        if valuemap:
          yield otype, oname, valuemap
        valuemap = {}
        otype = t
        oname = n
      for i in range(2, len(row)):
        value = row[i]
        if value is not None and type(value) is not str and value != '':
          attr = hdrrow[i]
          value = row[i]
          valuemap.setdefault(attr, []).append(value)
    # yield gathers Node data
    if valuemap:
      yield otype, oname, valuemap

def import_csv_wide(nodedb, csvfile, doAppend=False):
    ''' Load a wide format CSV.
        Layout is:
          TYPE, NAME, attr1, attr2, ...
          t1, n1, v1, v2, ...
            ,   ,   , v2a, ...
        etc.
    '''
    if type(csvfile) is str:
      with Pfx(csvfile):
        with open(csvfile) as csvfp:
          import_csv_wide(nodedb, csvfp, doAppend=doAppend)
      return

    for t, n, valuemap in import_rows_wide(csv.reader(csvfile)):
      N = nodedb.get( (t, n), doCreate=True )
      for attr, values in valuemap.items():
        parsed = []
        for value in values:
          if len(value):
            parsed.append(nodedb.fromtoken(value, node=N, attr=attr, doCreate=True))
        if doAppend:
          N[attr].extend(parsed)
        else:
          N[attr] = parsed

def edit_csv_wide(nodedb, nodes=None, attrs=None, all_attrs=False, all_nodes=False, editor=None):
  if editor is None:
    editor = os.environ.get('CSV_EDITOR', os.environ.get('EDITOR', 'vi'))
  with tempfile.NamedTemporaryFile(suffix='.csv') as T:
    with Pfx(T.name):
      export_csv_wide(T.name, nodes, attrs=attrs, all_attrs=all_attrs, all_nodes=all_nodes)
      qname = cs.sh.quotestr(T.name)
      os.system("set -x; cat %s >/dev/tty; %s %s" % (qname, editor, qname))
      import_csv_wide(nodedb, T.name, doAppend=False)

class TestAll(unittest.TestCase):

  def setUp(self):
    from . import NodeDB
    db = self.db = NodeDB(backend=None)
    N1 = self.N1 = db.newNode('HOST', 'host1')
    N1.ATTR1 = 1
    N2 = self.N2 = db.newNode('HOST', 'host2')
    N2.ATTR2 = 2

  def test01export_rows_wide_01raw(self):
    N1, N2 = self.N1, self.N2
    rows = tuple( export_rows_wide( (N1, N2) ) )
    expected_rows = ( [ 'TYPE', 'NAME', 'ATTR1', 'ATTR2' ],
                      [ N1.type, N1.name, N1.ATTR1, None ],
                      [ N2.type, N2.name, None,     N2.ATTR2 ],
                    )
    self.assert_(rows == expected_rows)

  def test01export_rows_wide_02row_attrs(self):
    N1, N2 = self.N1, self.N2
    rows = tuple( export_rows_wide( (N1, N2), attrs=('ATTR1',) ) )
    expected_rows = ( [ 'TYPE', 'NAME', 'ATTR1' ],
                      [ N1.type, N1.name, N1.ATTR1 ],
                    )
    self.assert_(rows == expected_rows)

  def test01export_rows_wide_03row_attrs_allnodes(self):
    N1, N2 = self.N1, self.N2
    rows = tuple( export_rows_wide( (N1, N2), attrs=('ATTR1',), all_nodes=True ) )
    expected_rows = ( [ 'TYPE', 'NAME', 'ATTR1' ],
                      [ N1.type, N1.name, N1.ATTR1 ],
                      [ N2.type, N2.name, None,    ],
                    )
    self.assert_(rows == expected_rows)

  def test02import_rows_wide_01(self):
    input_rows = ( [ 'TYPE', 'NAME', 'ATTR1', 'ATTR2' ],
                   [ 'HOST', 'host1', 1, None ],
                   [ 'HOST', 'host2', None, 2 ],
                   [ None,   None,    None, 3 ],
                 )
    data = tuple( import_rows_wide( input_rows ) )
    expected_data = ( ( 'HOST', 'host1', {'ATTR1': [1]} ),
                      ( 'HOST', 'host2', {'ATTR2': [2,3]} ),
                    )
    self.assert_(data == expected_data)

if __name__ == '__main__':
  unittest.main()
