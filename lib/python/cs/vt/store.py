#!/usr/bin/env python3
#
# Block stores.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Various Store classes.
'''

from contextlib import contextmanager
from fnmatch import fnmatch
from functools import partial
from os.path import isfile as isfilepath, splitext
import sys

from icontract import require

from cs.cache import CachingMapping, ConvCache
from cs.deco import fmtdoc, promote
from cs.hashindex import file_checksum
from cs.logutils import warning, error, info
from cs.pfx import Pfx, pfx_method
from cs.progress import Progress, progressbar
from cs.queues import Channel, IterableQueue
from cs.resources import openif, RunState, uses_runstate
from cs.result import Result, report
from cs.seq import get0
from cs.threads import bg as bg_thread

from . import (
    Store,
    Lock,
    RLock,
    StoreSyncBase,
)
from .backingfile import BackingFileIndexEntry, BinaryHashCodeIndex, CompressibleBackingFile
from .cache import FileDataMappingProxy, MemoryCacheMapping
from .datadir import DataDir, PlatonicDir
from .hash import HashCodeType
from .index import choose as choose_indexclass

class StoreError(Exception):
  ''' Raised by Store operation failures.
  '''

  def __init__(self, message, **kw):
    super().__init__(message)
    for k, v in kw.items():
      setattr(self, k, v)

  def __str__(self):
    s = repr(self)
    for k in dir(self):
      if k and k[0].isalpha() and k not in ('args', 'with_traceback'):
        s += ":%s=%r" % (k, getattr(self, k))
    return s

class MappingStore(StoreSyncBase):
  ''' A Store built on an arbitrary mapping object.
  '''

  def __init__(self, name, mapping, **kw):
    super().__init__(name, **kw)
    self.mapping = mapping
    self._str_attrs.update(mapping=type(mapping).__name__)

  @contextmanager
  def startup_shutdown(self):
    ''' Open/close `self.mapping` (if supported).
    '''
    with super().startup_shutdown():
      with openif(self.mapping):
        yield

  def add(self, data):
    ''' Add `data` to the mapping, indexed as `hashclass(data)`.
        Return the hashcode.
    '''
    h = self.hash(data)
    self.mapping[h] = data
    return h

  def flush(self):
    ''' Call the .flush method of the underlying mapping, if any.
    '''
    map_flush = getattr(self.mapping, 'flush', None)
    if map_flush is not None:
      map_flush()

  def hashcodes_from(self, **kw):
    ''' Use the mapping's `.hashcodes_from` if present,
        otherwise use the superclass' `.hashcodes_from`.

        This lets the mapping supply an efficient `hashcodes_from`
        in preference to the default brute force method
        based on the mapping keys.
    '''
    try:
      hashcodes_method = self.mapping.hashcodes_from
    except AttributeError:
      hashcodes_method = super().hashcodes_from
    return hashcodes_method(**kw)

  def __len__(self):
    return len(self.mapping)

  def contains(self, h):
    return h in self.mapping

  def keys(self):
    ''' Proxy to `self.mapping.keys`.
    '''
    return self.mapping.keys()

  def __getitem__(self, h):
    ''' Proxy to `self.mapping[h]`.
    '''
    return self.mapping[h]

  def get(self, h, default=None):
    ''' Proxy to `self.mapping.get`.
    '''
    return self.mapping.get(h, default)

  @uses_runstate
  def pushto_queue(self, Q, runstate: RunState, progress=None):
    ''' Push all the `Store` keys to the queue `Q`.
    '''
    for h in progressbar(self.keys(), f'push {self.name}', total=len(self)):
      runstate.raiseif()
      Q.put(h)
    return True

class ProxyStore(StoreSyncBase):
  ''' A Store managing various subsidiary Stores.

      Three classes of Stores are managed:
      * Save stores. All data added to the Proxy is added to these Stores.
      * Read Stores. Requested data may be obtained from these Stores.
      * Copy Stores. Data retrieved from a `read2` Store is copied to these Stores.

      A example setup utilising a working `ProxyStore` might look like this:

          ProxyStore(
            save=[local,upstream],
            save2=[spool],
            read=[local,spool],
            read2=[upstream],
            copy2=[local],
          )

      In this example:
      * `local`: is a local low latency store such as a `DataDirStore`.
      * `upstream`: is a remote high latency Store such as a `TCPStore`.
      * `spool`: is a local secondary Store, probably a `DataDirStore`.

      This example setup causes all saved data to be saved to `local`
      and `upstream`.
      If a save to `local` or `upstream` fails, for example if the
      upstream is offline, the save is repeated to the `spool`,
      intended as a holding location for data needing a resave.

      Reads are attempted first from the `read` Stores, then from
      the `read2` Stores.
      If there are any `copy2` Stores,
      any data obtained from `read2` are copied into the `copy2` Stores;
      in this way remote data become locally saved.

      Archives may be made available with the `archives` parameter.
      This is an iterable of `(glob,Store)`.
      This supports obtaining an Archive by name
      from the first Store whose glob matches the name.

      TODO: replay and purge the `save2` spool? Probably better as a separate
      pushto operation:

          vt -S spool_store pushto --delete upstream_store
  '''

  def __init__(
      self,
      name,
      save,
      read,
      *,
      save2=(),
      read2=(),
      copy2=(),
      archives=(),
      conv_cache=None,
      **kw
  ):
    ''' Initialise a ProxyStore.

        Parameters:
        * `name`: `ProxyStore` name
        * `save`: iterable of Stores to which to save blocks
        * `read`: iterable of Stores from which to fetch blocks
        * `save2`: fallback Store for saves which fail
        * `read2`: optional fallback iterable of Stores from which
          to fetch blocks if not found via `read`. Typically these
          would be higher latency upstream Stores.
        * `copy2`: optional iterable of Stores to receive copies
          of data obtained via `read2` Stores.
        * `archives`: search path for archive names
        * `conv_cache`: optional `cs.cache.ConvCache`, default will
          be inferred from `read`
    '''
    if conv_cache is None:
      # use the firsy .conv_cache from the read Stores
      read = list(read)
      conv_cache = get0(filter(lambda readStore: readStore.conv_cache, read))
    super().__init__(name, conv_cache=conv_cache, **kw)
    self.save = frozenset(save)
    self.read = frozenset(read)
    self.save2 = frozenset(save2)
    self.read2 = frozenset(read2)
    self.copy2 = frozenset(copy2)
    all_stores = (self.save | self.read | self.save2 | self.read2 | self.copy2)
    assert len(all_stores) > 0
    hashclasses = [S.hashclass for S in all_stores]
    self.hashclass = hashclass0 = hashclasses[0]
    assert all(map(lambda hashclass: hashclass is hashclass0, hashclasses))
    self.archive_path = tuple(archives)
    for S, _ in self.archive_path:
      if not hasattr(S, 'get_Archive'):
        raise ValueError("%s: no get_Archive method" % (S,))
    self._str_attrs.update(save=save, read=read)
    if save2:
      self._str_attrs.update(save2=save2)
    if read2:
      self._str_attrs.update(read2=read2)
    if copy2:
      self._str_attrs.update(copy2=copy2)
    self.readonly = len(self.save) == 0

  def get_Archive(self, name, missing_ok=False):
    ''' Obtain the named Archive from a Store in the archives list.
    '''
    with Pfx("%s.get_Archive(%r)", self, name):
      for S, fnptn in self.archive_path:
        if fnmatch(name, fnptn):
          info(
              "%s.get_Archive(%r): matched %r, fetching from %r", self.name,
              name, fnptn, S.name
          )
          return S.get_Archive(name, missing_ok=missing_ok)
      raise KeyError("no such Archive")

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, self.name)

  def init(self):
    ''' Init the subsidiary Stores.
    '''
    for S in self.save | self.read | self.save2 | self.read2 | self.copy2:
      S.init()

  @contextmanager
  def startup_shutdown(self):
    with super().startup_shutdown():
      for S in self.save | self.read | self.save2 | self.read2 | self.copy2:
        S.open()
      for S, _ in self.archive_path:
        S.open()
      try:
        yield
      finally:
        for S, _ in self.archive_path:
          S.close()
        for S in self.save | self.read | self.save2 | self.read2 | self.copy2:
          S.close()

  def __len__(self):
    ''' The size of the store is the sum of the read stores.
    '''
    return sum(map(len, self.read))

  @staticmethod
  def _multicall0(stores, method_name, args, kwargs=None):
    ''' Basic multicall of _bg methods yielding (LF, S) pairs in the order submitted.
    '''
    assert method_name.endswith('_bg')
    stores = list(stores)
    if kwargs is None:
      kwargs = {}
    for S in stores:
      with Pfx("%s.%s()", S, method_name):
        with S:
          LF = getattr(S, method_name)(*args, **kwargs)
      yield LF, S  # outside Pfx because this is a generator

  def _multicall(self, stores, method_name, args, kwargs=None):
    ''' Generator yielding `(S,result,exc_info)` for each call to
        `S.method_name(*args,**kwargs)` in the order completed.

        The method_name should be one of the *_bg names which return
        `LateFunction`s.
        Methods are called in parallel and values returned as
        completed, so the return tuples may not be in the same
        order as the supplied `stores`.

        Parameters:
        * `stores`: iterable of Stores on which to call `method_name`
        * `method_name`: name of Store method
        * `args`: positional arguments for the method call
        * `kwargs`: optional keyword arguments for the method call
    '''
    LFmap = dict(self._multicall0(stores, method_name, args, kwargs=kwargs))
    for LF in report(LFmap.keys()):
      # locate the corresponding store for context
      S = LFmap[LF]
      yield S, LF.result, LF.exc_info

  def add(self, data):
    ''' Add a data chunk to the save Stores.
        This queues all the saves in the background and returns the
        first hashcode received.
    '''
    ch = Channel()
    self._defer(self._bg_add, data, ch)
    hashcode = ch.get()
    if hashcode is None:
      raise RuntimeError("no hashcode returned from .add")
    return hashcode

  def _bg_add(self, data, ch):
    ''' Add a data chunk to the save Stores.

        Parameters:
        * `data`: the data to add
        * `hashclass`: the hashclass with which to index the data,
          default: `None`, meaning `self.hashclass`
        * `ch`: a channel for hashcode return
    '''
    try:
      if not self.save:
        # no save - allow add if hashcode already present - dubious
        hashcode = self.hash(data)
        if hashcode in self:
          ch.put(hashcode)
          ch = None
          return
        raise RuntimeError("new add but no save Stores")
      ok = True
      fallback = None
      for S, hashcode, exc_info in self._multicall(self.save, 'add_bg',
                                                   (data,)):
        if exc_info is None:
          assert hashcode is not None, "None from .add of %s" % (S,)
          if ch:
            ch.put(hashcode)
            ch = None
        else:
          e = exc_info[1]
          if isinstance(e, StoreError):
            exc_info = None
          error("exception from %s.add: %s", S, e, exc_info=exc_info)
          if ok:
            ok = False
            if self.save2:
              # kick off the fallback saves immediately
              fallback = list(self._multicall0(self.save2, 'add_bg', (data,)))
            else:
              error("no fallback Stores")
          continue
      if not ok:
        if fallback:
          failures = []
          for LF, S in fallback:
            hashcode, exc_info = LF.join()
            if exc_info:
              e = exc_info[1]
              if isinstance(e, StoreError):
                exc_info = None
              error(
                  "exception saving to %s: %s",
                  S,
                  exc_info[1],
                  exc_info=exc_info
              )
              failures.append((S, e))
            else:
              if ch:
                ch.put(hashcode)
                ch = None
          if failures:
            raise RuntimeError("exceptions saving to save2: %r" % (failures,))
    finally:
      # mark end of queue
      if ch:
        ch.put(None)
        ch = None
        ch.close()

  def get(self, h, default=None):
    ''' Fetch a block from the first Store which has it.
    '''
    with Pfx("%s.get", type(self).__name__):
      for stores in self.read, self.read2:
        for S, data, exc_info in self._multicall(stores, 'get_bg', (h,)):
          with Pfx("%s.get_bg(%s)", S, h):
            if exc_info:
              error("exception", exc_info=exc_info)
            elif data is not None:
              if S not in self.read:
                for copyS in self.copy2:
                  copyS.add_bg(data)
              return data
      return default

  def contains(self, h):
    ''' Test whether the hashcode `h` is in any of the read Stores.
    '''
    for stores in self.read, self.read2:
      for S, result, exc_info in self._multicall(stores, 'contains_bg', (h,)):
        if exc_info:
          error("exception fetching from %s: %s", S, exc_info)
        elif result:
          return True
    return False

  def flush(self):
    ''' Flush all the save Stores.
    '''
    for _ in self._multicall(self.save, 'flush_bg', ()):
      pass

  def keys(self):
    seen = set()
    Q = IterableQueue()

    def keys_from(S):
      for h in S.keys():
        Q.put(h)
      Q.put(None)

    busy = 0
    for S in self.read:
      bg_thread(partial(keys_from, S))
      busy += 1
    for h in Q:
      if h is None:
        busy -= 1
        if not busy:
          Q.close()
      elif h not in seen:
        yield h
        seen.add(h)

class DataDirStore(MappingStore):
  ''' A `MappingStore` using a `DataDir` as its backend.
  '''

  @promote
  def __init__(
      self,
      name,
      topdirpath,
      *,
      hashclass: HashCodeType = None,
      indexclass=None,
      rollover=None,
      lock=None,
      conv_cache=None,
      **kw
  ):
    ''' Initialise the DataDirStore.

        Parameters:
        * `name`: Store name
        * `topdirpath`: top directory path
        * `hashclass`: hash class or hash class name,
          default from `Store.get_default_hashclass()`
        * `indexclass`: passed to the data dir
        * `rollover`: passed to the data dir
        * `lock`: optional `RLock`, passed to the `DataDir` mapping
    '''
    if lock is None:
      lock = RLock()
    self._lock = lock
    self.topdirpath = topdirpath
    self.indexclass = indexclass
    self.rollover = rollover
    self._datadir = DataDir(
        self.topdirpath,
        hashclass=hashclass,
        indexclass=indexclass,
        rollover=rollover,
    )
    if conv_cache is None:
      conv_cache = ConvCache(
          self._datadir.pathto('convof'),
          content_key_func=partial(file_checksum, hashname=hashclass.hashname),
      )
    super().__init__(
        name,
        CachingMapping(self._datadir, missing_fallthrough=True),
        hashclass=hashclass,
        conv_cache=conv_cache,
        **kw,
    )
    self._modify_index_lock = Lock()

  @contextmanager
  def startup_shutdown(self):
    ''' Open/close the internal `DataDir`.
    '''
    with super().startup_shutdown():
      with self._datadir:
        yield

  def init(self):
    ''' Init the supporting data dir.
    '''
    self._datadir.initdir()

  def pathto(self, rpath):
    ''' Compute the full path from a relative path.
    '''
    return self._datadir.pathto(rpath)

  def get_Archive(self, name=None, missing_ok=False):
    ''' DataDirStore Archives are associated with the internal DataDir.
    '''
    return self._datadir.get_Archive(name, missing_ok=missing_ok)

  def get_index_entry(self, hashcode):
    ''' Return the index entry for `hashcode`, or `None` if there
        is no index or the index has no entry for `hashcode`.
    '''
    return self._datadir.get_index_entry(hashcode)

  @contextmanager
  def modify_index_entry(self, hashcode):
    ''' Context manager to obtain and yield the `FileDataIndexEntry` for `hashcode`
        and resave it on return.

        Example:

            with index.modify_entry(hashcode) as entry:
                entry.flags |= FileDataIndexEntry.INDIRECT_COMPLETE
    '''
    with self._modify_index_lock:
      self.mapping.flush()  # ensure the index is up to date
      with self._datadir.modify_index_entry(hashcode) as entry:
        yield entry

@promote
def PlatonicStore(
    name,
    topdirpath,
    *a,
    meta_store=None,
    hashclass: HashCodeType = None,
    **kw,
):
  ''' Factory function for platonic Stores.

      This is needed because if a meta_store is specified then it
      must be included as a block source in addition to the core
      platonic Store.
  '''
  if meta_store is None:
    return _PlatonicStore(name, topdirpath, *a, hashclass=hashclass, **kw)
  PS = _PlatonicStore(
      name, topdirpath, *a, meta_store=meta_store, hashclass=hashclass, **kw
  )
  S = ProxyStore(
      name,
      save=(),
      read=(PS, meta_store),
      hashclass=hashclass,
  )
  S.get_Archive = PS.get_Archive
  return S

class _PlatonicStore(MappingStore):
  ''' A `MappingStore` using a `PlatonicDir` as its backend.
  '''

  @promote
  def __init__(
      self,
      name,
      topdirpath,
      *,
      hashclass: HashCodeType = None,
      indexclass=None,
      follow_symlinks=False,
      archive=None,
      meta_store=None,
      flags_prefix=None,
      lock=None,
      **kw
  ):
    if lock is None:
      lock = RLock()
    self.lock = lock
    self.topdirpath = topdirpath
    self._datadir = PlatonicDir(
        self.topdirpath,
        hashclass=hashclass,
        indexclass=indexclass,
        follow_symlinks=follow_symlinks,
        archive=archive,
        meta_store=meta_store,
        flags_prefix=flags_prefix,
        **kw,
    )
    super().__init__(name, self._datadir, hashclass=hashclass, **kw)
    self.readonly = True

  def init(self):
    self._datadir.initdir()

  @contextmanager
  def startup_shutdown(self):
    ''' Open/close the internal `DataDir`.
    '''
    with super().startup_shutdown():
      with self._datadir:
        yield

  def get_Archive(self, name=None, missing_ok=False):
    ''' PlatonicStore Archives are associated with the internal DataDir.
    '''
    return self._datadir.get_Archive(name, missing_ok=missing_ok)

def MemoryCacheStore(name, max_data, hashclass=None):
  ''' Factory to make a MappingStore of a MemoryCacheMapping.
  '''
  return MappingStore(name, MemoryCacheMapping(max_data), hashclass=hashclass)

@pfx_method
@promote
def VTDStore(
    name,
    path,
    *,
    hashclass: HashCodeType = None,
    index=None,
    preferred_indexclass=None,
):
  ''' Factory to return a `MappingStore` using a `BackingFile`
      using a single `.vtd` file.
  '''
  with Pfx(path):
    if not path.endswith('.vtd'):
      warning("does not end with .vtd")
    if not isfilepath(path):
      raise ValueError("missing path %r" % (path,))
    pathbase, _ = splitext(path)
    if index is None:
      index_basepath = f"{pathbase}-index-{hashclass.hashname}"
      indexclass = choose_indexclass(
          index_basepath, preferred_indexclass=preferred_indexclass
      )
      binary_index = indexclass(index_basepath)
      index = BinaryHashCodeIndex(
          hashclass=hashclass,
          binary_index=binary_index,
          index_entry_class=BackingFileIndexEntry
      )
    return MappingStore(
        name,
        CompressibleBackingFile(path, hashclass=hashclass, index=index),
        hashclass=hashclass
    )

class FileCacheStore(StoreSyncBase):
  ''' A Store wrapping another Store that provides fast access to
      previously fetched data and fast storage of new data,
      using asynchronous updates to the backing Store (which may be `None`).

      This class is a thin Store shaped shim over a `FileDataMappingProxy`,
      which does the heavy lifting of storing data.
  '''

  @require(lambda name: isinstance(name, str))
  @require(lambda backend: backend is None or isinstance(backend, Store))
  @require(lambda dirpath: isinstance(dirpath, str))
  def __init__(
      self,
      name,
      backend,
      dirpath,
      max_cachefile_size=None,
      max_cachefiles=None,
      runstate=None,
      **kw
  ):
    ''' Initialise the `FileCacheStore`.

        Parameters:
        * `name`: the Store name
        * `backend`: the backing Store; this may be `None`, and the
          property .backend may be switched to another Store at any
          time
        * `dirpath`: directory to hold the cache files

        Other keyword arguments are passed to `StoreSyncBase.__init__`.
    '''
    super().__init__(name, runstate=runstate, **kw)
    self._str_attrs.update(backend=backend)
    self._backend = None
    self.cache = FileDataMappingProxy(
        backend,
        dirpath=dirpath,
        max_cachefile_size=max_cachefile_size,
        max_cachefiles=max_cachefiles,
        runstate=runstate,
    )
    self._str_attrs.update(
        cachefiles=self.cache.max_cachefiles,
        cachesize=self.cache.max_cachefile_size
    )
    self.backend = backend

  def __getattr__(self, attr):
    return getattr(self.backend, attr)

  @property
  def backend(self):
    ''' Return the current backend Store.
    '''
    return self._backend

  @backend.setter
  def backend(self, new_backend):
    ''' Switch backends.
    '''
    old_backend = self._backend
    if old_backend is not new_backend:
      if old_backend:
        old_backend.close()
      self._backend = new_backend
      cache = self.cache
      if cache:
        cache.backend = new_backend
      self._str_attrs.update(backend=new_backend)
      if new_backend:
        new_backend.open()

  @property
  def conv_cache(self):
    ''' The `conv_cache` from the backend `Store`.
    '''
    return None if self._backend is None else self._backend.conv_cache

  @contextmanager
  def startup_shutdown(self):
    with super().startup_shutdown():
      self.cache.open()
      try:
        yield
      finally:
        self.cache.close()
        self.cache = None
        self.backend = None

  def flush(self):
    ''' Dummy flush operation.
    '''

  def sync(self):
    ''' Dummy sync operation.
    '''

  def __len__(self):
    return len(self.cache) + len(self.backend)

  def keys(self):
    hashclass = self.hashclass
    # pylint: disable=unidiomatic-typecheck
    return (h for h in self.cache.keys() if type(h) is hashclass)

  def __iter__(self):
    return self.keys()

  def contains(self, h):
    return h in self.cache

  def add(self, data):
    h = self.hash(data)
    self.cache[h] = data
    return h

  # add is deliberately very fast; just return a completed Result directly
  def add_bg(self, data):
    return Result(result=self.add(data))

  def get(self, h):
    try:
      data = self.cache[h]
    except KeyError:
      data = None
    else:
      pass
    return data

class ProgressStore(StoreSyncBase):
  ''' A shim for another Store to do progress reporting.

      TODO: planning to redo basic store methods as shims, with
      implementations supplying _foo methods across the board
      instead.
  '''

  def __init__(
      self,
      S,
      **kw,
  ):
    ''' Wrapper for another Store which collects statistics on use.
    '''
    super().__init__(f"{type(self).__name__}({S})", **kw)
    self.S = S
    self.progress_add = Progress(
        name=f"add_bytes:{self.name}", throughput_window=4, total=0
    )
    self.progress_get = Progress(
        name=f"get_bytes:{self.name}", throughput_window=4, total=0
    )

  def __str__(self):
    return self.status_text()

  @contextmanager
  def startup_shutdown(self):
    ''' Open the subStore.
    '''
    with self.S:  # open the substore
      with self:
        yield

  def add(self, data):
    ''' Advance the progress_add total, and the position on completion.
    '''
    progress_add = self.progress_add
    data_len = len(data)
    progress_add.total += data_len
    result = self.S.add(data)
    progress_add.position += data_len
    return result

  def add_bg(self, data):
    ''' Advance the progress_add total, and the position on completion.
    '''
    progress_add = self.progress_add
    data_len = len(data)
    progress_add.total += data_len
    LF = self.S.add_bg(data)
    del data

    def notifier(LF):
      _, exc = LF.join()
      if exc is None:
        progress_add.position += data_len

    LF.notify(notifier)
    return LF

  def get(self, h):
    ''' Request the data for the hashcode `h`,
        advance `self.progress_get.position` by its length on return,
        and return the data.
    '''
    data = self.S.get(h)
    self.progress_get.position += len(data)
    return data

  def contains(self, h):
    return self.S.contains(h)

  def flush(self):
    self.S.flush()

  def __len__(self):
    return len(self.S)

if __name__ == '__main__':
  from .store_tests import selftest
  selftest(sys.argv)
