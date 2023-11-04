#!/usr/bin/python

''' Unit tests for cs.timeseries.
    - Cameron Simpson <cs@cskk.id.au>
'''

from math import nan
import unittest

from .timeseries import TypeCode

from .x import X

class TestTypeCode(unittest.TestCase):
  ''' Test `cs.timeseries.TypeCode`.
  '''

  def test_all(self):
    ''' Test `TypeCode`. '''
    self.assertEqual(TypeCode.promote('d'), 'd')
    self.assertEqual(TypeCode.promote(float), 'd')
    self.assertEqual(TypeCode.promote('q'), 'q')
    self.assertEqual(TypeCode.promote(int), 'q')
    for code, type_ in TypeCode.TYPES:
      with self.subTest(code=code, type=type_):
        self.assertIs(TypeCode.BY_CODE[code], type_)
        self.assertEqual(TypeCode.BY_TYPE[type_], code)
        T_code = TypeCode(code)
        T_type = TypeCode(type_)
        self.assertEqual(T_code, T_type)
        if T_code == 'd':
          self.assertEqual(T_code.default_fill, nan)
        elif T_code == 'q':
          self.assertEqual(T_code.default_fill, 0)
        else:
          raise RuntimeError(f'no test for TypeCode({T_code}).default_fill')
