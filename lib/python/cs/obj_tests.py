#!/usr/bin/python
#

''' Self tests for cs.result.
    - Cameron Simpson <cs@cskk.id.au>
'''

import sys
import unittest
from cs.obj import SingletonMixin

class TestSingletonMixin(unittest.TestCase):
  ''' Testing `SingletonMixin`.
  '''

  def setUp(self):
    ''' Prepare a `SingletonMixin` subclass as `.scls`.
    '''

    class SClass(SingletonMixin):
      ''' Test class.
      '''

      @classmethod
      def _singleton_key(cls, x):
        return x

      def __init__(self, x):
        if hasattr(self, 'x'):
          return
        self.x = x

    self.scls = SClass

  def test00simple(self):
    cls = self.scls
    o1 = cls(1)
    self.assertEqual(o1.x, 1)
    o2 = cls(2)
    self.assertEqual(o2.x, 2)
    self.assertIsNot(o2, o1)
    o3 = cls(1)
    self.assertEqual(o3.x, 1)
    self.assertIs(o3, o1)
    self.assertIsNot(o3, o2)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
