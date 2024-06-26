#!/usr/bin/env python3
#
# Hash tests.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Hash tests.
'''

import random
import sys
import unittest

from cs.binary_tests import BaseTestBinaryClasses

from . import hash as hash_module
from .hash import HashCode, decode as decode_hash
from .transcribe import Transcriber

class TestHashBinaryClasses(BaseTestBinaryClasses, unittest.TestCase):
  ''' Hook to test the hash `AbstractBinary` subclasses.
  '''
  test_module = hash_module

class TestHashing(unittest.TestCase):
  ''' Tests for the hashcode facility.
  '''

  def setUp(self):
    ''' Initialise the pseudorandom number generator.
    '''
    random.seed()

  def testSHA1(self):
    ''' Test the various hash function.
    '''
    for hash_name, cls in sorted(HashCode.by_hashname.items()):
      with self.subTest(hash_name=hash_name):
        for _ in range(10):
          rs = bytes(random.randint(0, 255) for _ in range(100))
          H = cls.from_data(rs)
          self.assertEqual(cls.hashfunc(rs).digest(), bytes(H))
          self.assertTrue(isinstance(H, Transcriber))
          Hs = str(H)
          H2, offset = Transcriber.parse(Hs)
          self.assertTrue(offset == len(Hs))
          self.assertEqual(H, H2)
          # bytes(hash_num + hash_bytes)
          Hencode = H.encode()
          H2, offset = decode_hash(Hencode)
          self.assertEqual(offset, len(Hencode))
          self.assertEqual(H, H2)

def selftest(argv):
  ''' Run the unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
