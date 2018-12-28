#!/usr/bin/python
#
# Hash tests.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Hash tests.
'''

from hashlib import sha1
from itertools import accumulate
import random
import sys
import unittest
from cs.binary_tests import _TestPacketFields
from .randutils import rand0, randbool, randblock
from . import _TestAdditionsMixin, hash as hash_module
from .hash import Hash_SHA1, decode as decode_hash, HashUtilDict
from .transcribe import Transcriber, parse

class TestDataFilePacketFields(_TestPacketFields, unittest.TestCase):
  ''' Hook to test the hash PacketFields.
  '''

  def setUp(self):
    ''' Test the hash module PacketField classes.
    '''
    self.module = hash_module

class TestHashing(unittest.TestCase):
  ''' Tests for the hashcode facility.
  '''

  def setUp(self):
    ''' Initialise the pseudorandom number generator.
    '''
    random.seed()

  def testSHA1(self):
    ''' Test the SHA1 hash function.
    '''
    for _ in range(10):
      rs = bytes( random.randint(0, 255) for _ in range(100) )
      H = Hash_SHA1.from_chunk(rs)
      self.assertEqual( sha1(rs).digest(), bytes(H) )
      self.assertTrue( isinstance(H, Transcriber))
      Hs = str(H)
      H2, offset = parse(Hs)
      self.assertTrue( offset == len(Hs) )
      self.assertEqual(H, H2)
      # bytes(hash_num + hash_bytes)
      Hencode = H.encode()
      H2, offset = decode_hash(Hencode)
      self.assertEqual(offset, len(Hencode))
      self.assertEqual(H, H2)

class _TestHashCodeUtils(_TestAdditionsMixin, unittest.TestCase):
  ''' Test for the hashcodeutilities.
  '''

  MAP_FACTORY = lambda _: None

  def setUp(self):
    ''' Prepare for testing using the specified map factory.
    '''
    self.maxDiff = 16384
    self.map1 = self.MAP_FACTORY()
    if self.map1 is None:
      raise unittest.SkipTest("no MAP_FACTORY, skipping test")
    self.map1.open()
    self.keys1 = set()
    try:
      keys_method = self.map1.keys
    except AttributeError:
      self.has_keys = False
    else:
      try:
        _ = keys_method()
      except NotImplementedError:
        self.has_keys = False
      else:
        self.has_keys = True

  def tearDown(self):
    ''' Close map1.
    '''
    self.map1.close()

  def test00first(self):
    ''' Trivial test adding 2 blocks.
    '''
    with self.subTest(map_type=type(self.map1)):
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

  def test01test_hashcodes_from(self):
    ''' Test the hashcodes_from method.
    '''
    with self.subTest(map_type=type(self.map1)):
      # fill map1 with 16 random data blocks
      M1 = self.map1
      KS1 = self.keys1
      for _ in range(16):
        data = randblock(rand0(8193))
        h = M1.add(data)
        KS1.add(h)
      # make a block not in the map
      data2 = randblock(rand0(8193))
      h2 = Hash_SHA1.from_chunk(data2)
      self.assertNotIn(h2, KS1, "abort test: %s in previous blocks" % (h2,))
      #
      # extract hashes, check results
      #
      ks = sorted(KS1)
      for reverse in False, True:
        for start_hashcode in [None] + ks + [h2]:
          with self.subTest(M1type=type(M1), reverse=reverse, start_hashcode=start_hashcode):
            hs = list(M1.hashcodes_from(start_hashcode=start_hashcode,
                                        reverse=reverse))
            self.assertIsOrdered(hs, reverse=reverse, strict=True)
            if reverse:
              ksrev = reversed(ks)
              hs2 = [ h for h in ksrev if start_hashcode is None or h <= start_hashcode ]
            else:
              hs2 = [ h for h in ks if start_hashcode is None or h >= start_hashcode ]
            hs = list(sorted(hs))
            hs2 = list(sorted(hs2))
            self.assertEqual(hs, hs2)

  def test02hashcodes(self):
    ''' Various tests.
    '''
    with self.subTest(map_type=type(self.map1)):
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
        self.assertEqual(set(iter(M1)), KS1)
        self.assertEqual(set(M1.hashcodes()), KS1)
      # asking for 0 hashcodes is forbidden
      with self.assertRaises(ValueError):
        # NB: using list() to iterate over the generator, thus executing .hashcodes
        hs = list(M1.hashcodes(length=0))
      # fetch the leading n hashcodes from the map, with and without `after`
      for after in False, True:
        with self.subTest(after=after):
          for n in range(1, 16):
            if after:
              start_hashcode = None
              for mincode in accumulate(iter(M1), min):
                start_hashcode = mincode
              if start_hashcode is None:
                # no start_hashcode, skip when after is true
                continue
            else:
              start_hashcode = None
            hs = list(M1.hashcodes(start_hashcode=start_hashcode, after=after, length=n))
            self.assertIsOrdered(hs, False)
            hn = min(n, 15 if after else 16)
            self.assertEqual(len(hs), hn)
      # traverse the map in various sized steps, including random
      sorted_keys = sorted(KS1)
      for step_size in 1, 2, 3, 7, 8, 15, 16, None:
        with self.subTest(step_size=step_size):
          start_hashcode = None
          keys_offset = 0
          seen = set()
          while keys_offset < len(sorted_keys):
            if step_size is None:
              n = random.randint(1, 7)
            else:
              n = step_size
            with self.subTest(
                start_hashcode=start_hashcode,
                keys_offset=keys_offset, n=n,
            ):
              after = start_hashcode is not None
              hs = list(
                  M1.hashcodes(
                      start_hashcode=start_hashcode,
                      length=n,
                      reverse=False,
                      after=after))
              # verify that no key has been seen before
              for h in hs:
                self.assertNotIn(h, seen)
              # verify ordering of returned list
              self.assertIsOrdered(hs, reverse=False, strict=True)
              # verify that least key is > start_hashcode
              if start_hashcode is not None:
                self.assertLess(start_hashcode, hs[0])
              hn = min(len(sorted_keys) - keys_offset, n)
              self.assertEqual(len(hs), hn)
              # verify returned keys against master list
              for i in range(hn):
                self.assertEqual(sorted_keys[keys_offset + i], hs[i])
              # note these keys, advance
              seen.update(hs)
              keys_offset += hn
              start_hashcode = hs[-1]
          # verify that all keys have been retrieved
          self.assertEqual(sorted_keys, sorted(seen))

  def test03hashcodes_missing(self):
    ''' Test the hashcodes_missing function.
    '''
    with self.subTest(map_type=type(self.map1)):
      M1 = self.map1
      KS1 = self.keys1
      for _ in range(16):
        data = randblock(rand0(8193))
        h = M1.add(data)
        KS1.add(h)
      with self.MAP_FACTORY() as M2:
        KS2 = set()
        # construct M2 as a mix of M1 and random new blocks
        for _ in range(16):
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
          M1missing = sorted(set(M1.hashcodes_missing(M2)))
          KS1missing = sorted(KS2 - KS1)
          self.assertEqual(M1missing, KS1missing)
          M2missing = set(M2.hashcodes_missing(M1))
          KS2missing = KS1 - KS2
          self.assertEqual(M2missing, KS2missing)

class TestHashCodeUtils(_TestHashCodeUtils, unittest.TestCase):
  ''' Test the HashCodeUtils facilities via a HashUitlDict.
  '''

  MAP_FACTORY = HashUtilDict

def selftest(argv):
  ''' Run the unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
