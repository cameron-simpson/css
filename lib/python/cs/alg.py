#!/usr/bin/python
#
# Assorted algorithms.
#       - Cameron Simpson <cs@zip.com.au> 26sep2010
#

from types import StringTypes
import unittest

def collate(seq, attr, select=None):
  ''' Collate members of a sequence by some attribute.
      If `select` is supplied and not None, collate only members
      whose attrtibute value is in `select`.
      If `select` is a string or numeric type it is tested for equality.
  '''
  if select is not None:
    t = type(select)
    if t in StringTypes or t in (int, long, float):
      select = (select,)

  collation = {}
  for S in seq:
    key = getattr(S, attr)
    if select is not None and key not in select:
      continue
    collation.setdefault(key, []).append(S)

  return collation

class TestExcUtils(unittest.TestCase):

  class Klass(object):
    def __init__(self, a, b):
      self.a = a
      self.b = b

  def test00nothing(self):
    self.assertEqual(collate( (), 'foo' ), {})

  def test01badAttr(self):
    O = self.Klass(1,2)
    self.assertRaises(AttributeError, collate, (O,), 'foo' )

  def test02basic(self):
    O1 = self.Klass(1,2)
    O2 = self.Klass(3,4)
    self.assertEqual( collate((O1, O2), 'a'), {1: [O1], 3: [O2]} )

  def test02basic(self):
    O1 = self.Klass(1,2)
    O2 = self.Klass(3,4)
    self.assertEqual( collate((O1, O2), 'a'), {1: [O1], 3: [O2]} )

if __name__ == '__main__':
  unittest.main()
