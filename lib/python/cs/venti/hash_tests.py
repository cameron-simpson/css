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
from cs.randutils import rand0, randbool, randblock
from . import _TestAdditionsMixin
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

class _TestHashCodeUtils(_TestAdditionsMixin):

  MAP_FACTORY = None

  def setUp(self):
    MF = self.MAP_FACTORY
    if MF is None:
      raise unittest.SkipTest("no MAP_FACTORY, skipping test")
    self.map1 = self.MAP_FACTORY()
    self.map1.open()
    self.keys1 = set()
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

  def tearDown(self):
    self.map1.close()

  def test00first(self):
    M1 = self.map1
    KS1 = self.keys1
    # test emptiness
    self.assertLen(M1, 0)
    # add one block
    data = randblock(rand0(8193))
    h = M1.add(data)
    KS1.add(h)
    self.assertLen(M1, 1)
    self.assertEqual(set(M1.hashcodes()), KS1)
    # add another block
    data2 = randblock(rand0(8193))
    h2 = M1.add(data2)
    KS1.add(h2)
    self.assertLen(M1, 2)
    self.assertEqual(set(M1.hashcodes()), KS1)

  def test01hashcodes(self):
    M1 = self.map1
    KS1 = self.keys1
    # add 16 random blocks to the map with some sanity checks along the way
    for n in range(16):
      data = randblock(rand0(8193))
      h = M1.add(data)
      self.assertIn(h, M1)
      self.assertNotIn(h, KS1)
      KS1.add(h)
      self.assertIn(h, KS1)
      self.assertLen(M1, n+1)
      self.assertEqual(len(KS1), n+1)
      self.assertEqual(set(M1.hashcodes()), KS1)
    # asking for 0 hashcodes is forbidden
    with self.assertRaises(ValueError):
      hs = list(M1.hashcodes(length=0))
    # fetch the leading n hashcodes from the map, with and without `after`
    for after in False, True:
      with self.subTest(after=after):
        for n in range(1,16):
          hs = list(M1.hashcodes(length=n, after=after))
          hn = min(n, 15 if after else 16)
          self.assertEqual(len(hs), hn)
    # traverse the map in various sized steps, including random
    ks = sorted(KS1)
    for step_size in 1, 2, 3, 7, 8, 15, 16, None:
      with self.subTest(step_size=step_size):
        start_hashcode = None
        ksndx = 0
        seen = set()
        while ksndx < len(ks):
          if step_size is None:
            n = random.randint(1,7)
          else:
            n = step_size
          with self.subTest(start_hashcode=start_hashcode, ksndx=ksndx, n=n):
            hs = list(M1.hashcodes(start_hashcode=start_hashcode, length=n, reverse=False, after=True))
            # check ordering between adjacent hashcodes returns and against start_hashcode if not None
            h0 = None
            for h in hs:
              self.assertNotIn(h, seen)
              seen.add(h)
              if start_hashcode is not None:
                self.assertGreater(h, start_hashcode)
              if h0 is not None:
                self.assertGreater(h, h0)
              h0 = h
            hn = min(len(ks) - ksndx, n)
            self.assertEqual(len(hs), hn)
            for i in range(hn):
              self.assertEqual(ks[ksndx + i], hs[i])
            ksndx += hn
            start_hashcode = hs[-1]
        self.assertEqual(ks, sorted(seen))

  def test02test_hashcodes_from(self):
    # fill map1 with 16 random data blocks
    M1 = self.map1
    KS1 = self.keys1
    for n in range(16):
      data = randblock(rand0(8193))
      h = M1.add(data)
      KS1.add(h)
    # make a block not in the map
    data2 = randblock(rand0(8193))
    h2 = Hash_SHA1.from_data(data2)
    # extract hashes, check results
    ks = sorted(KS1)
    for reverse in False, True:
      for start_hashcode in [None] + ks + [h2]:
        with self.subTest(M1type=type(M1), reverse=reverse, start_hashcode=start_hashcode):
          hs = list(M1.hashcodes_from(start_hashcode=start_hashcode,
                                      reverse=reverse))
          if reverse:
            ksrev = reversed(ks)
            hs2 = [ h for h in ksrev if start_hashcode is None or h <= start_hashcode ]
          else:
            hs2 = [ h for h in ks if start_hashcode is None or h >= start_hashcode ]
          self.assertEqual(hs, hs2)

  def test03hashcodes_missing(self):
    M1 = self.map1
    KS1 = self.keys1
    for n in range(16):
      data = randblock(rand0(8193))
      h = M1.add(data)
      KS1.add(h)
    M2 = self.MAP_FACTORY()
    KS2 = set()
    # construct M2 as a mix of M1 and random new blocks
    for n in range(16):
      if randbool():
        data = randblock(rand0(8193))
        h = M2.add(data)
        KS2.add(h)
      else:
        M1ks = list(M1.hashcodes())
        M1hash = M1ks[rand0(len(M1ks))]
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
