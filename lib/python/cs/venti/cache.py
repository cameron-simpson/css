#!/usr/bin/python
#
# A cache store, connected to a fast cache and a slower backend.
#       - Cameron Simpson <cs@zip.com.au> 07dec2007
#

from __future__ import with_statement
from collections import namedtuple
import sys
from threading import Lock, Thread
from cs.asynchron import Result
from cs.fileutils import RWFileBlockCache
import cs.later
from cs.lex import hexify
from cs.logutils import X
from cs.progress import Progress
from cs.queues import IterableQueue
from . import MAX_FILE_SIZE
from .datafile import DATAFILE_DOT_EXT
from .store import BasicStoreSync

DEFAULT_CACHEFILE_HIGHWATER = MAX_FILE_SIZE
DEFAULT_MAX_CACHEFILES = 3

class CacheStore(BasicStoreSync):
  ''' A CacheStore is a Store front end to a pair of other Stores, a backend
      store and a cache store. The backend store is the "main" store, perhaps
      remote or slow, while the cache store is normally a faster and possibly
      lossy store such as a MemoryCacheStore or a local disc store.

      A block read is satisfied from the cache if possible, otherwise from
      the backend. A block store is stored to the cache and then
      asynchronously to the backend.
  '''
  def __init__(self, name, backend, cache, **kw):
    hashclass = kw.pop('hashclass', None)
    if hashclass is None:
      hashclass = backend.hashclass
    elif hashclass is not backend.hashclass:
      raise ValueError("hashclass and backend.hashclass are not the same (%s vs %s)"
                       % (hashclass, backend.hashclass))
    if hashclass is not cache.hashclass:
      raise ValueError("backend and cache hashclasses are not the same (%s vs %s)"
                       % (backend.hashclass, cache.hashclass))
    kw['hashclass'] = hashclass
    BasicStoreSync.__init__(self,
                            "CacheStore(backend=%s,cache=%s)"
                            % (backend.name, cache.name),
                            **kw)
    self.backend = backend
    self.cache = cache
    # secondary queue to process background self.backend operations
    self.__closing = False

  def startup(self):
    self.backend.open()
    self.cache.open()

  def shutdown(self):
    self.cache.close()
    self.backend.close()
    BasicStoreSync.shutdown(self)

  def flush(self):
    # dispatch flushes in parallel
    LFs = [
            self.cache.flush_bg(),
            self.backend.flush_bg()
          ]
    # wait for the cache flush and then the backend flush
    for LF in LFs:
      LF()

  def keys(self):
    cache = self.cache
    for h in cache.keys():
      yield h
    for h in self.backend.keys():
      if h not in cache:
        yield h

  def contains(self, h):
    if h in self.cache:
      return True
    return h in self.backend

  def get(self, h):
    try:
      h = self.cache[h]
    except KeyError:
      h = self.backend.get(h)
    return h

  def add(self, data):
    ''' Add the data to the local cache and queue a task to add to the backend.
    '''
    self.backend.add_bg(data)
    return self.cache.add(data)

class MemoryCacheStore(BasicStoreSync):
  ''' A lossy store that keeps an in-memory cache of recent chunks.  It may
      discard older chunks if new ones come in when full and would normally
      be used as the cache part of a CacheStore pair.
      The optional parameter `maxchunks` specifies the maximum number of
      chunks to keep in memory; it defaults to 1024. Specifying 0 keeps
      all chunks in memory.
  '''

  def __init__(self, name, maxchunks=1024, **kw):
    if maxchunks < 1:
      raise ValueError("maxchunks < 1: %s" % (maxchunks,))
    BasicStoreSync.__init__(self, "MemoryCacheStore(%s)" % (name,), **kw)
    self.hashlist = [None for _ in range(maxchunks)]
    self.low = 0                    # offset to oldest hash
    self.used = 0
    self.hmap = {}                  # cached h->(count, chunk) tuples

  def flush(self):
    pass

  def sync(self):
    pass

  def keys(self):
    return self.hmap.keys()

  def _hit(self, h, data):
    #assert type(h) is str, "_hit(%s) - not a string" % h
    hmap = self.hmap
    hlist = self.hashlist
    hlen = len(hlist)

    if self.used >= hlen:
      # empty a slot
      oldh = self.hashlist[self.low]
      assert oldh in hmap, "%s not in hmap" % hexify(h)
      hits = hmap[oldh][0]
      if hits <= 1:
        del hmap[oldh]
      else:
        hmap[oldh][0] -= 1
      self.low = (self.low + 1) % len(hlist)
      self.used -= 1

    if h in self.hmap:
      self.hmap[h][0] += 1
    else:
      self.hmap[h] = [1, data]
    self.used += 1
    high = (self.low + self.used) % hlen
    hlist[high] = h

  def contains(self, h):
    with self._lock:
      return h in self.hmap

  def get(self, h):
    with self._lock:
      hmap = self.hmap
      if h in hmap:
        return hmap[h][1]
    return None

  def add(self, data):
    with self._lock:
      H = self.hash(data)
      self._hit(H, data)
    return H

class FileCacheStore(BasicStoreSync):
  ''' A Store wrapping another Store that provides fast access to
      previously fetched data and fast storage of new data with
      asynchronous updates to the backing Store.
  '''

  def __init__(self, name, backend, dir=None, **kw):
    BasicStoreSync.__init__(self, "%s(%s)" % (self.__class__.__name__, name,), **kw)
    self.backend = backend
    self.cache = FileDataMappingProxy(backend, dir=dir)

  def __getattr__(self, attr):
    return getattr(self.backend, attr)

  def startup(self):
    self.backend.open()

  def shutdown(self):
    self.cache.close()
    self.backend.close()

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

  # add is very fast, don't bother with the Later scheduler
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
class ClachedData(_CachedData):
  def fetch(self):
    return self.cachefile.get(self.offset, self.length)

class FileDataMappingProxy(object):
  ''' Mapping-like class to cache data chunks to bypass gdbm indices and the like.
      Data are saved immediately into an in memory cache and an asynchronous
      worker copies new data into a cache file and also to the backend
      storage.
  '''

  def __init__(self, backend, dir=None,
               max_cachefile_size=None, max_cachefiles=None,
              ):
    ''' Initialise the cache.
        `backend`: mapping underlying us
        `dir`: directory to store cache files
        `max_cachefile_size`: maximum cache file size; a new cache
          file is created if this is exceeded; default:
          DEFAULT_CACHEFILE_HIGHWATER
        `max_cachefiles`: number of cache files to keep around; no
          more than this many cache files are kept at a time; default:
          DEFAULT_MAX_CACHEFILES
    '''
    self.backend = backend
    if max_cachefile_size is None:
      max_cachefile_size = DEFAULT_CACHEFILE_HIGHWATER
    if max_cachefiles is None:
      max_cachefiles = DEFAULT_MAX_CACHEFILES
    self.cached = {}    # map h => data
    self.saved = {}     # map h => _CachedData(cachefile, offset, length)
    self._lock = Lock()
    self.cachefiles = []
    self._add_cachefile()
    self._workQ = IterableQueue()
    self._worker = Thread(target=self._worker)
    self._worker.start()

  def _add_cachefile(self):
    cachefile = RWFileBlockCache(dir=dir)
    self.cachefiles.insert(0, cachefile)
    if len(cachefiles) > self.max_cachefiles:
      old_cachefile = self.cachefile.pop()
      old_cachefile.close()

  @property
  def cachefile(self):
    return self.cachefiles[0]

  @property
  def ncachefiles(self):
    return len(self.cachefiles)

  def close(self):
    ''' Shut down the cache.
        Stop the worker, close the file cache.
    '''
    self._workQ.close()
    self._worker.join()
    for cachefile in cachefiles:
      cachefile.close()

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
    return h in self.backend

  def keys(self):
    ''' Mapping method for .keys.
    '''
    seen = set()
    for h in self.cached.keys():
      yield h
      seen.add(h)
    saved = self.saved
    for h in saved.keys():
      if h not in seen and self._getref(h):
        yield h
        seen.add(h)
    for h in self.backend.keys():
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
    data = self.backend[h]
    with self._lock:
      self.cached[h] = data
    self._workQ.put( (h, data) )
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
    self._workQ.put( (h, data) )

  def _worker(self):
    for h, data in self._workQ:
      with self._lock:
        if self._getref(h):
          # already in file cache, therefore already sent to backend
          return
      cachefile = self.cachefile
      offset = cachefile.put(data)
      with self._lock:
        self.saved[h] = CachedData(cachefile, offset, len(data))
        # release memory cache entry
        try:
          del self.cached[h]
        except KeyError:
          pass
        # roll over to new cache file
        if offset + len(data) >= max_cachefile_size:
          self._add_cachefile()
      # store into the backend
      self.backend[h] = data

if __name__ == '__main__':
  import cs.venti.cache_tests
  cs.venti.cache_tests.selftest(sys.argv)
