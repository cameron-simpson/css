#!/usr/bin/python
#
# A cache store, connected to a fast cache and a slower backend.
#       - Cameron Simpson <cs@zip.com.au> 07dec2007
#

from __future__ import with_statement
import sys
from threading import Lock, Thread
from cs.fileutils import RWFileBlockCache
import cs.later
from cs.lex import hexify
from cs.logutils import X
from cs.progress import Progress
from cs.queues import IterableQueue
from .datafile import DATAFILE_DOT_EXT
from .store import BasicStoreSync

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

  def get(self, h):
    try:
      data = self.cache[h]
    except KeyError:
      data = None
    else:
      pass
    return data

class FileDataMappingProxy(object):
  ''' Mapping-like to cache data chunks to bypass gdbm indices and the like.
      Data are saved immediately into an in memory cache and an
      asynchronous worker copies new data into a cache file and
      also to the backend storage.
  '''

  def __init__(self, backend, dir=None):
    ''' Initialise the cache.
        `backend`: mapping underlying us
    '''
    self.backend = backend
    self.cached = {}    # map h => data
    self.saved = {}     # map h => offset, length
    self._lock = Lock()
    self.file_cache = RWFileBlockCache(dir=dir, suffix=DATAFILE_DOT_EXT)
    self.workQ = IterableQueue()
    self.worker = Thread(target=self._worker)
    self.worker.start()

  def close(self):
    ''' Shut down the cache.
        Stop the worker, close the file cache.
    '''
    self.workQ.close()
    self.worker.join()
    self.file_cache.close()

  def __contains__(self, h):
    ''' Mapping method supporting "in".
    '''
    with self._lock:
      if h in self.cached:
        return True
      if h in self.saved:
        return True
    return h in self.backend

  def keys(self):
    ''' Mapping method for .keys.
    '''
    seen = set()
    for k in self.cached.keys():
      yield k
      seen.add(k)
    for k in self.saved.keys():
      if k not in seen:
        yield k
        seen.add(k)
    for k in self.backend.keys():
      if k not in seen:
        yield k

  def __getitem__(self, h):
    ''' Fetch the data with key `h`. Raise KeyError if missing.
    '''
    with self._lock:
      # fetch from memory
      try:
        data = self.cached[h]
      except KeyError:
        data = None
        # fetch from file
        try:
          offset, length = self.saved[h]
        except KeyError:
          offset = None
    if data is None:
      if offset is None:
        # fetch from backend, queue store into cache
        data = self.backend[h]
        with self._lock:
          self.cached[h] = data
        self.workQ.put( (h, data) )
      else:
        # fetch from the file cache
        data = self.file_cache.get(offset, length)
    return data

  def __setitem__(self, h, data):
    ''' Store `data` against key `h`.
    '''
    with self._lock:
      if h in self.cached or h in self.saved:
        return
      self.cached[h] = data
    self.workQ.put( (h, data) )

  def _worker(self):
    for h, data in self.workQ:
      with self._lock:
        if h in self.saved:
          return
      offset = self.file_cache.put(data)
      with self._lock:
        self.saved[h] = offset, len(data)
        try:
          del self.cached[h]
        except KeyError:
          pass
      # store into the backend
      self.backend[h] = data

if __name__ == '__main__':
  import cs.venti.cache_tests
  cs.venti.cache_tests.selftest(sys.argv)
