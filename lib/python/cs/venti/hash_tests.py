#!/usr/bin/python
#
# Self tests for cs.venti.hash.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import random
if sys.hexversion >= 0x02050000:
  from hashlib import sha1
else:
  from sha import new as sha1
import unittest
from cs.logutils import X
from cs.randutils import rand0, randblock
from .hash import Hash_SHA1, decode, HashCodeUtilsMixin, HashUtilDict

class TestHashing(unittest.TestCase):

  def setUp(self):
    random.seed()

  def testSHA1(self):
    import random
    for _ in range(10):
      rs = bytes( random.randint(0, 255) for _ in range(100) )
      H = Hash_SHA1.from_data(rs)
      self.assertEqual( sha1(rs).digest(), bytes(H) )
      # bytes(hash_num + hash_bytes)
      Hencode = H.encode()
      H2, offset = decode(Hencode)
      self.assertEqual(offset, len(Hencode))
      self.assertEqual(H, H2)

class _TestHashCodeUtils:

  MAP_FACTORY = None

  def setUp(self):
    self.map1 = self.MAP_FACTORY()
    self.keys1 = set()
    try:
      h = self.map1.first()
    except NotImplementedError as e:
      raise unittest.SkipTest("no .first: %s" % (e,))
    try:
      keys_method = self.map1.keys
    except AttributeError:
      self.has_keys = False
    else:
      try:
        ks = keys_method()
      except NotImplementedError:
        self.has_keys = False
      else:
        self.has_keys = True

  def test00first(self):
    M1 = self.map1
    KS1 = self.keys1
    # test emptiness
    h = M1.first()
    self.assertIs(h, None)
    self.assertEqual(len(M1), 0)
    # add one block
    data = randblock(rand0(8192))
    h = M1.add(data)
    KS1.add(h)
    self.assertEqual(len(M1), 1)
    self.assertEqual(set(M1.hashcodes()), KS1)
    self.assertEqual(M1.first(), h)
    # add another block
    data2 = randblock(rand0(8192))
    h2 = M1.add(data2)
    KS1.add(h2)
    self.assertEqual(len(M1), 2)
    self.assertEqual(set(M1.hashcodes()), KS1)
    self.assertEqual(M1.first(), min( (h, h2) ))

  def test01hashcodes(self):
    M1 = self.map1
    KS1 = self.keys1
    for n in range(16):
      data = randblock(rand0(8192))
      h = M1.add(data)
      KS1.add(h)
      self.assertEqual(len(M1), n+1)
      self.assertEqual(len(KS1), n+1)
      self.assertEqual(set(M1.hashcodes()), KS1)
      if self.has_keys:
        self.assertEqual(M1.first(), min(M1.keys()))
      self.assertEqual(M1.first(), min(M1.hashcodes()))
    with self.assertRaises(ValueError):
      hs = list(M1.hashcodes(length=0))
    for n in range(1,16):
      hs = list(M1.hashcodes(length=n))
      self.assertEqual(len(hs), n)

  def test02hashcodes_missing(self):
    M1 = self.map1
    KS1 = self.keys1
    for n in range(16):
      data = randblock(rand0(8192))
      h = M1.add(data)
      KS1.add(h)
    M2 = self.MAP_FACTORY()
    KS2 = set()
    # construct M2 as a mix of M1 and random new blocks
    for n in range(16):
      if rand0(1) == 0:
        data = randblock(rand0(8192))
        h = M2.add(data)
        KS2.add(h)
      else:
        M1ks = list(M1.hashcodes())
        M1hash = M1ks[rand0(len(M1ks)-1)]
        data = M1[M1hash]
        h = M2.add(data)
        self.assertEqual(h, M1hash)
        KS2.add(h)
      # compare differences between M1 and M2
      # against differences between key sets KS1 and KS2
      M1missing = set(M1.hashcodes_missing(M2))
      KS1missing = KS2 - KS1
      self.assertEqual(M1missing, KS1missing)
      M2missing = set(M2.hashcodes_missing(M1))
      KS2missing = KS1 - KS2
      self.assertEqual(M2missing, KS2missing)

class TestHashCodeUtils(_TestHashCodeUtils, unittest.TestCase):
  MAP_FACTORY = HashUtilDict

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
