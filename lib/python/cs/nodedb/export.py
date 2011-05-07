#!/usr/bin/python -tt
#
# Export and import NodeDB data.
#       - Cameron Simpson <cs@zip.com.au> 07may2011
#

import sys
import unittest
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
            elem = N.totoken(elem, attr=attr)
          row.append(elem)
        else:
          row.append(blank)
      yield row

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

if __name__ == '__main__':
  unittest.main()
