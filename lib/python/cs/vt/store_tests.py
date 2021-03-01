#!/usr/bin/python
#
# Store tests.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Tests for Stores.
'''

import errno
from itertools import accumulate
import os
from os.path import join as joinpath
import random
import sys
import tempfile
import threading
from time import sleep
import unittest
from cs.context import stackkeys
from cs.debug import thread_dump
from cs.logutils import setup_logging
from cs.pfx import Pfx
from cs.randutils import rand0, randbool, make_randblock
from . import _TestAdditionsMixin
from .cache import FileCacheStore, MemoryCacheStore
from .index import class_names as get_index_names, class_by_name as get_index_by_name
from .hash import HashCode, HASHCLASS_BY_NAME
from .socket import (
    TCPStoreServer, TCPClientStore, UNIXSocketStoreServer,
    UNIXSocketClientStore
)
from .store import MappingStore, DataDirStore, ProxyStore
from .stream import StreamStore

HASHCLASS_NAMES_ENVVAR = 'VT_STORE_TESTS__HASHCLASS_NAMES'
INDEXCLASS_NAMES_ENVVAR = 'VT_STORE_TESTS__INDEXCLASS_NAMES'
STORECLASS_NAMES_ENVVAR = 'VT_STORE_TESTS__STORECLASS_NAMES'

# constrain the tests if not empty, try every permutation if empty
HASHCLASS_NAMES = tuple(
    os.environ.get(HASHCLASS_NAMES_ENVVAR, '').split()
    or sorted(HashCode.by_name.keys())
)
INDEXCLASS_NAMES = tuple(
    os.environ.get(INDEXCLASS_NAMES_ENVVAR, '').split() or get_index_names()
)
STORECLASS_NAMES = tuple(os.environ.get(STORECLASS_NAMES_ENVVAR, '').split())

def get_test_stores(prefix):
  ''' Generator of test Stores for various combinations.
      Yield `(subtest,Store)` tuples.

      `subtest` is a dict containing decriptive fields for `unittest.subtest()`.

      `Store` is an empty Store to test.
  '''
  # test all Store types against all the hash classes
  subtest = {}
  for hashclass_name in HASHCLASS_NAMES:
    hashclass = HASHCLASS_BY_NAME[hashclass_name]
    with stackkeys(
        subtest,
        hashname=hashclass_name,
        hashclass=hashclass.__name__,
    ):
      # MappingStore
      with stackkeys(subtest, storetype=MappingStore):
        yield subtest, MappingStore(
            'MappingStore', mapping={}, hashclass=hashclass
        )
      # MemoryCacheStore
      with stackkeys(subtest, storetype=MemoryCacheStore):
        yield subtest, MemoryCacheStore(
            'MemoryCacheStore', 1024 * 1024 * 1024, hashclass=hashclass
        )
      # DataDirStore
      with stackkeys(subtest, storetype=DataDirStore):
        for index_name in INDEXCLASS_NAMES:
          indexclass = get_index_by_name(index_name)
          with stackkeys(subtest, indexname=index_name, indexclass=indexclass):
            for rollover in 200000, :
              with stackkeys(subtest, rollover=rollover):
                T = tempfile.TemporaryDirectory(prefix=prefix)
                with T as tmpdirpath:
                  yield subtest, DataDirStore(
                      'DataDirStore',
                      tmpdirpath,
                      hashclass=hashclass,
                      indexclass=indexclass,
                      rollover=rollover
                  )
      # FileCacheStore
      with stackkeys(subtest, storetype=FileCacheStore):
        T = tempfile.TemporaryDirectory(prefix=prefix)
        with T as tmpdirpath:
          yield subtest, FileCacheStore(
              'FileCacheStore',
              MappingStore('MappingStore', {}),
              tmpdirpath,
              hashclass=hashclass
          )
      # StreamStore
      with stackkeys(subtest, storetype=StreamStore):
        for addif in False, True:
          with stackkeys(subtest, addif=addif):
            local_store = MappingStore("MappingStore", {}, hashclass=hashclass)
            upstream_rd, upstream_wr = os.pipe()
            downstream_rd, downstream_wr = os.pipe()
            remote_S = StreamStore(
                "remote_S",
                upstream_rd,
                downstream_wr,
                local_store=local_store,
                addif=addif,
                hashclass=hashclass
            )
            S = StreamStore(
                "S",
                downstream_rd,
                upstream_wr,
                addif=addif,
                hashclass=hashclass
            )
            with local_store:
              with remote_S:
                yield subtest, S
      # TCPClientStore
      with stackkeys(subtest, storetype=TCPClientStore):
        for addif in False, True:
          with stackkeys(subtest, addif=addif):
            local_store = MappingStore("MappingStore", {}, hashclass=hashclass)
            base_port = 9999
            while True:
              bind_addr = ('127.0.0.1', base_port)
              try:
                remote_S = TCPStoreServer(bind_addr, local_store=local_store)
              except OSError as e:
                if e.errno == errno.EADDRINUSE:
                  base_port += 1
                else:
                  raise
              else:
                break
            S = TCPClientStore(
                None, bind_addr, addif=addif, hashclass=hashclass
            )
            with local_store:
              with remote_S:
                yield subtest, S
      # UNIXSocketClientStore
      with stackkeys(subtest, storetype=UNIXSocketClientStore):
        for addif in False, True:
          with stackkeys(subtest, addif=addif):
            local_store = MappingStore("MappingStore", {}, hashclass=hashclass)
            T = tempfile.TemporaryDirectory(prefix=prefix)
            with T as tmpdirpath:
              socket_path = joinpath(tmpdirpath, 'sock')
              remote_S = UNIXSocketStoreServer(
                  socket_path, local_store=local_store
              )
              S = UNIXSocketClientStore(
                  None, socket_path, addif=addif, hashclass=hashclass
              )
              with local_store:
                with remote_S:
                  yield subtest, S
      # ProxyStore
      with stackkeys(subtest, storetype=ProxyStore):
        main1 = MappingStore("main1", {}, hashclass=hashclass)
        main2 = MappingStore("main2", {}, hashclass=hashclass)
        save2 = MappingStore("save2", {}, hashclass=hashclass)
        S = ProxyStore(
            "ProxyStore", (main1, main2), (main2, save2),
            hashclass=hashclass,
            save2=(save2,)
        )
        yield subtest, S

def multitest(method):
  ''' Decorator to permute a test method for multiple Store types and hash classes.
  '''

  def testMethod(self):
    for subtest, S in get_test_stores(prefix=method.__module__ + '.' +
                                      method.__name__):
      if STORECLASS_NAMES and S.__name__ not in STORECLASS_NAMES:
        continue
      with Pfx("%s:%s", S, ",".join(["%s=%s" % (k, v)
                                     for k, v in sorted(subtest.items())])):
        with self.subTest(test_store=S, **subtest):
          self.S = S
          self.hashclass = subtest['hashclass']
          S.init()
          with S:
            method(self)
            S.flush()
          self.S = None
      S = None

  return testMethod

class TestStore(unittest.TestCase, _TestAdditionsMixin):
  ''' Tests for Stores.
  '''

  def __init__(self, *a, **kw):
    super().__init__(*a, **kw)
    self.S = None
    self.keys1 = None

  def tearDown(self):
    if self.S is not None:
      self.S.close()
    Ts = threading.enumerate()
    if len(Ts) > 1:
      thread_dump(Ts=Ts, fp=open('/dev/tty', 'w'))

  @multitest
  def test00empty(self):
    ''' Test that a new Store is empty.
    '''
    S = self.S
    self.assertLen(S, 0)

  @multitest
  def test01add_new_block(self):
    ''' Add a block and check that it worked.
    '''
    S = self.S
    # compute block hash but do not store
    size = random.randint(127, 16384)
    data = make_randblock(size)
    h = S.hash(data)
    ok = S.contains(h)
    self.assertFalse(ok)
    self.assertNotIn(h, S)
    # now add the block
    h2 = S.add(data)
    self.assertEqual(h, h2)
    ok = S.contains(h)
    self.assertTrue(ok)
    self.assertIn(h, S)

  @multitest
  def test02add_get(self):
    ''' Add random chunks, get them back.
    '''
    S = self.S
    self.assertLen(S, 0)
    random_chunk_map = {}
    for _ in range(16):
      size = random.randint(127, 16384)
      data = make_randblock(size)
      h = S.hash(data)
      h2 = S.add(data)
      self.assertEqual(h, h2)
      random_chunk_map[h] = data
    for h in random_chunk_map:
      chunk = S.get(h)
      self.assertIsNot(chunk, None)
      self.assertEqual(chunk, random_chunk_map[h])

  @multitest
  def testhcu00first(self):
    ''' Trivial test adding 2 blocks.
    '''
    M1 = self.S
    KS1 = set()
    # test emptiness
    self.assertLen(M1, 0)
    # add one block
    data = make_randblock(rand0(8193))
    h = M1.add(data)
    self.assertIn(h, M1)
    hcodes = list(M1.hashcodes())
    self.assertIn(h, M1.hashcodes())
    KS1.add(h)
    self.assertEqual(set(M1.hashcodes()), KS1)
    # add another block
    data2 = make_randblock(rand0(8193))
    h2 = M1.add(data2)
    KS1.add(h2)
    self.assertEqual(set(M1.hashcodes()), KS1)

  @multitest
  def testhcu01test_hashcodes_from(self):
    ''' Test the hashcodes_from method.
    '''
    # fill map1 with 16 random data blocks
    M1 = self.S
    KS1 = set()
    for _ in range(16):
      data = make_randblock(rand0(8193))
      h = M1.add(data)
      KS1.add(h)
    # make a block not in the map
    data2 = make_randblock(rand0(8193))
    h2 = self.S.hash(data2)
    self.assertNotIn(h2, KS1, "abort test: %s in previous blocks" % (h2,))
    #
    # extract hashes, check results
    #
    ks = sorted(KS1)
    for start_hashcode in [None] + ks + [h2]:
      with self.subTest(M1type=type(M1).__name__,
                        start_hashcode=start_hashcode):
        hs = list(M1.hashcodes_from(start_hashcode=start_hashcode))
        self.assertIsOrdered(hs, strict=True)
        hs2 = [h for h in ks if start_hashcode is None or h >= start_hashcode]
        hs = list(sorted(hs))
        hs2 = list(sorted(hs2))
        self.assertEqual(hs, hs2)

  @multitest
  def testhcu02hashcodes(self):
    ''' Various tests.
    '''
    M1 = self.S
    KS1 = set()
    # add 16 random blocks to the map with some sanity checks along the way
    for n in range(16):
      data = make_randblock(rand0(8193))
      h = M1.add(data)
      self.assertIn(h, M1)
      self.assertNotIn(h, KS1)
      KS1.add(h)
      sleep(0.1)
      self.assertLen(M1, n + 1)
      self.assertEqual(len(KS1), n + 1)
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
          hs = list(
              M1.hashcodes(
                  start_hashcode=start_hashcode, after=after, length=n
              )
          )
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
              keys_offset=keys_offset,
              n=n,
          ):
            after = start_hashcode is not None
            hs = list(
                M1.hashcodes(
                    start_hashcode=start_hashcode, length=n, after=after
                )
            )
            # verify that no key has been seen before
            for h in hs:
              self.assertNotIn(h, seen)
            # verify ordering of returned list
            self.assertIsOrdered(hs, strict=True)
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

  @multitest
  def testhcu03hashcodes_missing(self):
    ''' Test the hashcodes_missing function.
    '''
    M1 = self.S
    KS1 = set()
    for _ in range(16):
      data = make_randblock(rand0(8193))
      h = M1.add(data)
      KS1.add(h)
    with MappingStore("M2MappingStore", mapping={},
                      hashclass=M1.hashclass) as M2:
      KS2 = set()
      # construct M2 as a mix of M1 and random new blocks
      for _ in range(16):
        if randbool():
          data = make_randblock(rand0(8193))
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

def selftest(argv):
  ''' Run the unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  setup_logging(__file__)
  selftest(sys.argv)
