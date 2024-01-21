#!/usr/bin/env python3
#
# Store tests.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Tests for Stores.
'''

from contextlib import contextmanager
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
from cs.debug import thread_dump, trace
from cs.logutils import setup_logging, warning
from cs.pfx import Pfx
from cs.randutils import rand0, randbool, make_randblock
from cs.testutils import SetupTeardownMixin

from .block import HashCodeBlock, IndirectBlock
from .index import class_names as get_index_names, class_by_name as get_index_by_name
from .hash import HashCode, HASHCLASS_BY_NAME
from .socket import (
    TCPStoreServer, TCPClientStore, UNIXSocketStoreServer,
    UNIXSocketClientStore
)
from .store import (
    DataDirStore,
    FileCacheStore,
    MappingStore,
    MemoryCacheStore,
    ProxyStore,
)
from .stream import StreamStore
from .testsutil import _TestAdditionsMixin

ALL_STORE_TYPES = (
    MappingStore,
    MemoryCacheStore,
    DataDirStore,
    FileCacheStore,
    StreamStore,
    TCPClientStore,
    UNIXSocketClientStore,
    ProxyStore,
)

HASHCLASS_NAMES_ENVVAR = 'VT_STORE_TESTS__HASHCLASS_NAMES'
INDEXCLASS_NAMES_ENVVAR = 'VT_STORE_TESTS__INDEXCLASS_NAMES'
STORECLASS_NAMES_ENVVAR = 'VT_STORE_TESTS__STORECLASS_NAMES'

# constrain the tests if not empty, try every permutation if empty
HASHCLASS_NAMES = tuple(
    list(filter(None,
                os.environ.get(HASHCLASS_NAMES_ENVVAR, '').split(' ,')))
    or sorted(HashCode.by_name.keys())
)
INDEXCLASS_NAMES = tuple(
    list(
        filter(None,
               os.environ.get(INDEXCLASS_NAMES_ENVVAR, '').split(' ,'))
    ) or get_index_names()
)
STORECLASS_NAMES = tuple(
    list(
        filter(None,
               os.environ.get(STORECLASS_NAMES_ENVVAR, '').split(' ,'))
    ) or (
        'MappingStore', 'MemoryCacheStore', 'DataDirStore', 'FileCacheStore',
        'StreamStore', 'TCPClientStore', 'UNIXSocketClientStore', 'ProxyStore'
    )
)

all_store_type_names = set(
    store_type.__name__ for store_type in ALL_STORE_TYPES
)
assert all(
    store_name in all_store_type_names for store_name in STORECLASS_NAMES
), (
    "unknown Store types in STORECLASS_NAMES:%r, I know %r" %
    (STORECLASS_NAMES, sorted(all_store_type_names))
)

def get_test_stores(prefix):
  ''' Generator of test Stores for various combinations.
      Yield `(subtest,S)` tuples being:
      - `subtest`: is a dict containing descriptive fields for `unittest.subtest()`
      - `S`: an empty `Store` to test
  '''
  subtest = {}
  for hashclass_name in HASHCLASS_NAMES:
    hashclass = HASHCLASS_BY_NAME[hashclass_name]
    with stackkeys(
        subtest,
        hashname=hashclass_name,
        hashclass=hashclass.__name__,
    ):
      for store_type in ALL_STORE_TYPES:
        if store_type.__name__ not in STORECLASS_NAMES:
          continue
        if store_type is MappingStore:
          with stackkeys(subtest, storetype=MappingStore):
            yield subtest, MappingStore(
                'MappingStore', mapping={}, hashclass=hashclass
            )
        elif store_type is MemoryCacheStore:
          with stackkeys(subtest, storetype=MemoryCacheStore):
            yield subtest, MemoryCacheStore(
                'MemoryCacheStore', 1024 * 1024 * 1024, hashclass=hashclass
            )
        elif store_type is DataDirStore:
          with stackkeys(subtest, storetype=DataDirStore):
            for index_name in INDEXCLASS_NAMES:
              indexclass = get_index_by_name(index_name)
              with stackkeys(subtest, indexname=index_name,
                             indexclass=indexclass):
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
        elif store_type is FileCacheStore:
          with stackkeys(subtest, storetype=FileCacheStore):
            T = tempfile.TemporaryDirectory(prefix=prefix)
            with T as tmpdirpath:
              yield subtest, FileCacheStore(
                  'FileCacheStore',
                  MappingStore('MappingStore', {}),
                  tmpdirpath,
                  hashclass=hashclass
              )
        elif store_type is StreamStore:
          with stackkeys(subtest, storetype=StreamStore):
            for addif in False, True:
              with stackkeys(subtest, addif=addif, sync=True):
                local_store = MappingStore(
                    "MappingStore", {}, hashclass=hashclass
                )
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
                    hashclass=hashclass,
                    sync=subtest['sync'],
                )
                with local_store:
                  with remote_S:
                    yield subtest, S
        elif store_type is TCPClientStore:
          with stackkeys(subtest, storetype=TCPClientStore):
            for addif in False, True:
              with stackkeys(subtest, addif=addif, sync=True):
                local_store = MappingStore(
                    "MappingStore", {}, hashclass=hashclass
                )
                base_port = 9999
                while True:
                  bind_addr = ('127.0.0.1', base_port)
                  try:
                    remote_S = TCPStoreServer(
                        bind_addr, local_store=local_store
                    )
                  except OSError as e:
                    if e.errno == errno.EADDRINUSE:
                      base_port += 1
                    else:
                      raise
                  else:
                    break
                S = TCPClientStore(
                    None,
                    bind_addr,
                    addif=addif,
                    hashclass=hashclass,
                    sync=subtest['sync'],
                )
                with local_store:
                  with remote_S:
                    yield subtest, S
        elif store_type is UNIXSocketClientStore:
          with stackkeys(subtest, storetype=UNIXSocketClientStore):
            for addif in False, True:
              with stackkeys(subtest, addif=addif, sync=True):
                local_store = MappingStore(
                    "MappingStore", {}, hashclass=hashclass
                )
                T = tempfile.TemporaryDirectory(prefix=prefix)
                with T as tmpdirpath:
                  socket_path = joinpath(tmpdirpath, 'sock')
                  remote_S = UNIXSocketStoreServer(
                      socket_path, local_store=local_store
                  )
                  S = UNIXSocketClientStore(
                      None,
                      socket_path,
                      addif=addif,
                      sync=subtest['sync'],
                      hashclass=hashclass,
                  )
                  with local_store:
                    with remote_S:
                      yield subtest, S
        elif store_type is ProxyStore:
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
        else:
          raise RuntimeError(
              f'no Stopre subtests for Store type {store_type!r}'
          )

def multitest(method):
  ''' Decorator to permute a test method for multiple Store types and hash classes.
  '''

  def testMethod(self):
    for subtest, S in get_test_stores(prefix=method.__module__ + '.' +
                                      method.__name__):
      if STORECLASS_NAMES and type(S).__name__ not in STORECLASS_NAMES:
        continue
      with Pfx("%s:%s", S, ",".join(["%s=%s" % (k, v)
                                     for k, v in sorted(subtest.items())])):
        with self.subTest(test_store=S, **subtest):
          self.S = S
          self.supports_index_entry = type(self.S) in (DataDirStore,)
          self.hashclass = subtest['hashclass']
          S.init()
          with S:
            method(self)
            S.flush()
          self.assertTrue(S.closed)

  return testMethod

class TestStore(SetupTeardownMixin, unittest.TestCase, _TestAdditionsMixin):
  ''' Tests for Stores.
  '''

  def __init__(self, *a, **kw):
    super().__init__(*a, **kw)
    self.S = None
    self.keys1 = None

  @contextmanager
  def setupTeardown(self):
    S = self.S
    if S is not None:
      with S:
        yield
    else:
      yield
    Ts = [T for T in threading.enumerate() if not T.daemon]
    if len(Ts) > 1:
      with open('/dev/tty', 'w') as tty:
        thread_dump(Ts=Ts, fp=tty)

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
    added_hashes = set()
    # test emptiness
    self.assertLen(M1, 0)
    # add one block
    data = make_randblock(rand0(8193))
    h = M1.add(data)
    self.assertIn(h, M1)
    self.assertIn(h, M1.keys())
    self.assertEqual(M1[h], data)
    added_hashes.add(h)
    self.assertIn(h, M1)
    M1.flush()
    M1_keys = set(M1.keys())
    self.assertIn(h, M1_keys)
    M1_hashcodes = set(M1.hashcodes())
    self.assertIn(h, M1_hashcodes)
    self.assertEqual(M1_hashcodes, added_hashes)
    # add another block
    data2 = make_randblock(rand0(8193))
    h2 = M1.add(data2)
    added_hashes.add(h2)
    self.assertIn(h2, M1)
    self.assertIn(h2, M1.keys())
    M1_hashcodes_2 = set(M1.hashcodes())
    self.assertEqual(M1_hashcodes_2, added_hashes)

  @multitest
  def testhcu01test_hashcodes_from(self):
    ''' Test the hashcodes_from method.
    '''
    # fill map1 with 16 random data blocks
    M1 = self.S
    hashcodes_added = set()
    for _ in range(16):
      data = make_randblock(rand0(8193))
      h = M1.add(data)
      hashcodes_added.add(h)
    # make a block not in the map
    data2 = make_randblock(rand0(8193))
    hashcode_other = self.S.hash(data2)
    self.assertNotIn(
        hashcode_other, hashcodes_added,
        "abort test: %s in previous blocks" % (hashcode_other,)
    )
    #
    # extract hashes using Store.hashcodes_from, check results
    #
    ks = sorted(hashcodes_added)
    for start_hashcode in [None] + list(hashcodes_added) + [hashcode_other]:
      with self.subTest(M1type=type(M1).__name__,
                        start_hashcode=start_hashcode):
        hashcodes_from = list(M1.hashcodes_from(start_hashcode=start_hashcode))
        self.assertIsOrdered(hashcodes_from, strict=True)
        if start_hashcode is not None:
          for h in hashcodes_from:
            self.assertGreaterEqual(
                h, start_hashcode,
                "NOT start_hashocde=%s <= h=%s" % (start_hashcode, h)
            )
          self.assertTrue(
              all(map(lambda h: h >= start_hashcode, hashcodes_from))
          )
        ##hashcodes_expected = sorted(
        ##    h for h in hashcodes_added
        ##    if start_hashcode is None or h >= start_hashcode
        ##)
        ##self.assertEqual(hashcodes_from, hashcodes_expected)

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
      ##self.assertLen(M1, n + 1)
      ##self.assertEqual(len(KS1), n + 1)
      ##self.assertEqual(set(iter(M1)), KS1)
      ##self.assertEqual(set(M1.hashcodes()), KS1)
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
          if not M1ks:
            continue
          M1hash = M1ks[rand0(len(M1ks))]
          data = M1[M1hash]
          h = M2.add(data)
          self.assertEqual(h, M1hash)
          self.assertIn(h, M2)
          KS2.add(h)

  @multitest
  def test_get_index_entry(self):
    ''' Test `get_index_entry`
    '''
    S = self.S
    datas = [make_randblock(rand0(8193)) for _ in range(16)]
    for data in datas:
      h = S.hash(data)
      self.assertIsNone(S.get_index_entry(h))
      h2 = S.add(data)
      self.assertEqual(h, h2)
    sleep(0.5)  # wait for the indexing queue to flush
    for data in datas:
      h = S.hash(data)
      entry = S.get_index_entry(h)
      if self.supports_index_entry:
        self.assertIsNotNone(entry)
      else:
        self.assertIsNone(entry)

  @multitest
  def test_is_complete_indirect(self):
    S = self.S
    data1 = make_randblock(rand0(8193))
    data2 = make_randblock(rand0(8193))
    h1 = S.add(data1)
    B1 = HashCodeBlock(hashcode=h1, span=len(data1), added=True)
    self.assertEqual(len(B1), len(data1))
    h2 = S.hash(data2)
    B2 = HashCodeBlock(hashcode=h2, span=len(data2), added=True)
    self.assertEqual(len(B2), len(data2))
    IB = IndirectBlock.from_subblocks((B1, B2))
    self.assertIn(h1, S)
    sleep(0.5)  # wait for the indexing queue to flush
    with S.modify_index_entry(h1) as entry:
      if self.supports_index_entry:
        self.assertIsNotNone(entry)
        self.assertFalse(entry.flags & entry.INDIRECT_COMPLETE)
      else:
        self.assertIsNone(entry)
    self.assertNotIn(h2, S)
    with S.modify_index_entry(h2) as entry:
      self.assertIsNone(entry)
    ih = IB.hashcode
    with S.modify_index_entry(ih) as entry:
      if self.supports_index_entry:
        self.assertIsNotNone(entry)
        self.assertFalse(entry.flags & entry.INDIRECT_COMPLETE)
      else:
        self.assertIsNone(entry)
    self.assertFalse(S.is_complete_indirect(ih))
    with S.modify_index_entry(ih) as entry:
      if self.supports_index_entry:
        self.assertIsNotNone(entry)
        self.assertFalse(entry.flags & entry.INDIRECT_COMPLETE)
      else:
        self.assertIsNone(entry)
    S.add(data2)
    self.assertTrue(S.is_complete_indirect(ih))
    with S.modify_index_entry(ih) as entry:
      if self.supports_index_entry:
        self.assertIsNotNone(entry)
        self.assertTrue(entry.flags & entry.INDIRECT_COMPLETE)
      else:
        self.assertIsNone(entry)

def selftest(argv):
  ''' Run the unit tests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  setup_logging(__file__)
  selftest(sys.argv)
