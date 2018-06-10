#!/usr/bin/python
#
# A cache store, connected to a fast cache and a slower backend.
#       - Cameron Simpson <cs@cskk.id.au> 07dec2007
#

from __future__ import with_statement
from collections import namedtuple
import sys
from threading import Lock, Thread
from cs.fileutils import RWFileBlockCache
from cs.result import Result
from cs.queues import IterableQueue
from . import MAX_FILE_SIZE
from .store import BasicStoreSync

DEFAULT_CACHEFILE_HIGHWATER = MAX_FILE_SIZE
DEFAULT_MAX_CACHEFILES = 3

class FileCacheStore(BasicStoreSync):
  ''' A Store wrapping another Store that provides fast access to
      previously fetched data and fast storage of new data with
      asynchronous updates to the backing Store.

      This class is a thin Store shaped shim over a FileDataMappingProxy,
      which does the heavy lifting of storing data and ensuring all
      new data is passed to the backend.
  '''

  def __init__(
    self,
    name, backend, dirpath,
    max_cachefile_size=None, max_cachefiles=None,
    **kw):
    ''' Initialise the FileCacheStore.
        `name`: the Store name
        `backend`: the backing Store; this may be None, and the
          property .backend may be switched to another Store at any
          time
        `dirpath`: directory to hold the cache files
    '''
    super().__init__(name, **kw)
    self._attrs.update(backend=backend)
    self._backend = backend
    self.cache = FileDataMappingProxy(
        backend, dirpath=dirpath,
        max_cachefile_size=max_cachefile_size,
        max_cachefiles=max_cachefiles)
    self._attrs.update(
        cachefiles=self.cache.max_cachefiles,
        cachesize=self.cache.max_cachefile_size
    )

  def __getattr__(self, attr):
    return getattr(self.backend, attr)

  @property
  def backend(self):
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
      self.cache.backend = new_backend
      self._attrs.update(backend=new_backend)
      if new_backend:
        new_backend.open()

  def startup(self):
    super().startup()
    if self.backend:
      self.backend.open()

  def shutdown(self):
    self.cache.close()
    if self.backend:
      self.backend.close()
    super().shutdown()

  def flush(self):
    pass

  def sync(self):
    pass

  def keys(self):
    return self.cache.keys()

  def contains(self, h):
    return h in self.cache

  def add(self, data):
    backend = self.backend
    h = backend.hash(data)
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

_CachedData = namedtuple('CachedData', 'cachefile offset length')
class CachedData(_CachedData):
  def fetch(self):
    return self.cachefile.get(self.offset, self.length)

class FileDataMappingProxy(object):
  ''' Mapping-like class to cache data chunks to bypass gdbm indices and the like.
      Data are saved immediately into an in memory cache and an asynchronous
      worker copies new data into a cache file and also to the backend
      storage.
  '''

  def __init__(
      self, backend, dirpath=None,
      max_cachefile_size=None, max_cachefiles=None,
  ):
    ''' Initialise the cache.
        `backend`: mapping underlying us
        `dirpath`: directory to store cache files
        `max_cachefile_size`: maximum cache file size; a new cache
          file is created if this is exceeded; default:
          DEFAULT_CACHEFILE_HIGHWATER
        `max_cachefiles`: number of cache files to keep around; no
          more than this many cache files are kept at a time; default:
          DEFAULT_MAX_CACHEFILES
    '''
    if max_cachefile_size is None:
      max_cachefile_size = DEFAULT_CACHEFILE_HIGHWATER
    if max_cachefiles is None:
      max_cachefiles = DEFAULT_MAX_CACHEFILES
    self.backend = backend
    self.dirpath = dirpath
    self.max_cachefile_size = max_cachefile_size
    self.max_cachefiles = max_cachefiles
    self.cached = {}    # map h => data
    self.saved = {}     # map h => _CachedData(cachefile, offset, length)
    self._lock = Lock()
    self.cachefiles = []
    self._add_cachefile()
    self._workQ = IterableQueue()
    self._worker = Thread(target=self._work)
    self._worker.start()

  def close(self):
    ''' Shut down the cache.
        Stop the worker, close the file cache.
    '''
    self._workQ.close()
    self._worker.join()
    if self.cached:
      error("blocks still in memory cache: %r", self.cached)
    for cachefile in self.cachefiles:
      cachefile.close()

  def _add_cachefile(self):
    cachefile = RWFileBlockCache(dirpath=self.dirpath)
    self.cachefiles.insert(0, cachefile)
    if len(self.cachefiles) > self.max_cachefiles:
      old_cachefile = self.cachefiles.pop()
      old_cachefile.close()

  def _getref(self, h):
    ''' Fetch a cache reference from self.saved, return None if missing.
        Automatically prune stale saved entries if the cachefile is closed.
    '''
    saved = self.saved
    ref = saved.get(h)
    if ref is not None:
      if ref.cachefile.closed:
        ref = None
        del saved[h]
    return ref

  def __contains__(self, h):
    ''' Mapping method supporting "in".
    '''
    with self._lock:
      if h in self.cached:
        return True
      if self._getref(h) is not None:
        return True
    backend = self.backend
    if backend:
      return h in backend
    return False

  def keys(self):
    ''' Mapping method for .keys.
    '''
    seen = set()
    for h in self.cached:
      yield h
      seen.add(h)
    saved = self.saved
    for h in saved:
      if h not in seen and self._getref(h):
        yield h
        seen.add(h)
    backend = self.backend
    if backend:
      for h in backend.keys():
        if h not in seen:
          yield h

  def __getitem__(self, h):
    ''' Fetch the data with key `h`. Raise KeyError if missing.
    '''
    with self._lock:
      # fetch from memory
      try:
        data = self.cached[h]
      except KeyError:
        # fetch from file
        ref = self._getref(h)
        if ref is not None:
          return ref.fetch()
      else:
        # straight from memory cache
        return data
    # not in memory or file cache: fetch from backend, queue store into cache
    backend = self.backend
    if not backend:
      raise KeyError('no backend: h=%s' % (h,))
    data = backend[h]
    with self._lock:
      self.cached[h] = data
    self._workQ.put( (h, data, False) )
    return data

  def __setitem__(self, h, data):
    ''' Store `data` against key `h`.
    '''
    with self._lock:
      if h in self.cached:
        # in memory cache, do not save
        return
      if self._getref(h):
        # in file cache, do not save
        return
      # save in memory cache
      self.cached[h] = data
    # queue for file cache and backend
    self._workQ.put( (h, data, True) )

  def _work(self):
    for h, data, in_backend in self._workQ:
      with self._lock:
        if self._getref(h):
          # already in file cache, therefore already sent to backend
          return
      cachefile = self.cachefiles[0]
      offset = cachefile.put(data)
      with self._lock:
        self.saved[h] = CachedData(cachefile, offset, len(data))
        # release memory cache entry
        try:
          del self.cached[h]
        except KeyError:
          pass
        # roll over to new cache file
        if offset + len(data) >= self.max_cachefile_size:
          self._add_cachefile()
      # store into the backend
      if not in_backend:
        backend = self.backend
        if backend:
          self.backend[h] = data

if __name__ == '__main__':
  from .cache_tests import selftest
  selftest(sys.argv)
