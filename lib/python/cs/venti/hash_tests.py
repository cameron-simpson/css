#!/usr/bin/python
#
# Self tests for cs.venti.hash.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
if sys.hexversion >= 0x02050000:
  from hashlib import sha1
else:
  from sha import new as sha1
import unittest
from .hash import Hash_SHA1

class TestAll(unittest.TestCase):

  def setUp(self):
    import random
    random.seed()

  def testSHA1(self):
    import random
    for _ in range(10):
      rs = ''.join( chr(random.randint(0, 255)) for _ in range(100) )
      H = Hash_SHA1.fromData(rs)
      self.assertEqual( sha1(rs).digest(), H )
      Hencode = H.encode()
      H2, etc = Hash_SHA1.decode(Hencode)
      self.assertEqual(len(etc), 0)
      self.assertEqual(H, H2)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
