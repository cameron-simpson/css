#!/usr/bin/python
#
# Unit tests for cs.nodedb.export.
#       - Cameron Simpson <cs@cskk.id.au>
#

import sys
import unittest
from . import NodeDB
from .export import export_rows_wide, import_rows_wide
from .mappingdb import MappingBackend
from .node_tests import TestAll as NodeTestAll

class TestAll(NodeTestAll):
  ''' Tests for `cs.nodedb.export`.
  '''

  def nodedb(self):
    self.backend = MappingBackend(self.mapping)
    db = self.db = NodeDB(backend=self.backend)
    self.N1 = db['HOST:host1']
    self.N2 = db['HOST:host2']
    return db

  def setUp(self):
    self.mapping = {
        ('HOST', 'host1'): {
            'ATTR1': [1]
        },
        ('HOST', 'host2'): {
            'ATTR2': [2]
        },
    }

  def test01export_rows_wide_01raw(self):
    with self.nodedb() as db:
      N1, N2 = self.N1, self.N2
      rows = tuple(export_rows_wide((N1, N2)))
      expected_rows = (
          ['TYPE', 'NAME', 'ATTR1', 'ATTR2'],
          [N1.type, N1.name, N1.ATTR1, None],
          [N2.type, N2.name, None, N2.ATTR2],
      )
      self.assertTrue(rows == expected_rows)

  def test01export_rows_wide_02row_attrs(self):
    with self.nodedb() as db:
      N1, N2 = self.N1, self.N2
      rows = tuple(export_rows_wide((N1, N2), attrs=('ATTR1',)))
      expected_rows = (
          ['TYPE', 'NAME', 'ATTR1'],
          [N1.type, N1.name, N1.ATTR1],
      )
      self.assertTrue(rows == expected_rows)

  def test01export_rows_wide_03row_attrs_allnodes(self):
    with self.nodedb() as db:
      N1, N2 = self.N1, self.N2
      rows = tuple(
          export_rows_wide((N1, N2), attrs=('ATTR1',), all_nodes=True)
      )
      expected_rows = (
          ['TYPE', 'NAME', 'ATTR1'],
          [N1.type, N1.name, N1.ATTR1],
          [
              N2.type,
              N2.name,
              None,
          ],
      )
      self.assertTrue(rows == expected_rows)

  def test02import_rows_wide_01(self):
    with self.nodedb() as db:
      input_rows = (
          ['TYPE', 'NAME', 'ATTR1', 'ATTR2'],
          ['HOST', 'host1', 1, None],
          ['HOST', 'host2', None, 2],
          [None, None, None, 3],
      )
      data = tuple(import_rows_wide(input_rows))
      expected_data = (
          ('HOST', 'host1', {
              'ATTR1': [1]
          }),
          ('HOST', 'host2', {
              'ATTR2': [2, 3]
          }),
      )
      self.assertTrue(data == expected_data)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
