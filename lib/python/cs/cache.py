#!/usr/bin/python

import sys
from collections import deque
from threading import RLock
from cs.threads import locked

_caches = []
def overallHitRatio():
  if len(_caches) == 0:
    return None

  hits = 0
  misses = 0
  for c in _caches:
    (h, m) = c.hitMiss()
    hits += h
    misses += m

  total = hits + misses
  if total == 0:
    return None

  return float(hits) / float(total)

class RingBuffer(list):

  def __init__(self, size):
    assert size > 0
    list.__init__(None for i in range(size))
    self.__off = 0

  def __len__(self):
    return len(self.__buf)

  def __getitem__(self, i):
    assert i >= 0, "i < 0 (%d)" % i
    blen = len(self.__buf)
    assert i < blen, "i >= buflen (%d >= %d)" % (i, blen)
    i = (i + self.__off) % blen
    return self.__buf[i]

  def __setitem__(self, i, v):
    assert i >= 0, "i < 0 (%d)" % i
    blen = len(self.__buf)
    assert i < blen, "i >= buflen (%d >= %d)" % (i, blen)
    i = (i + self.__off) % blen
    self.__buf[i] = v

  def append(self, v):
    i = self.__off
    self.__buf[i] = v
    i += 1
    if i >= len(self.__buf):
      i = 0
    self.__off = i

class LRU_Cache(object):

  ''' Another simple least recently used cache.
  '''

  def __init__(self, maxsize, on_add=None, on_remove=None):
    ''' Initialise the LRU_Cache with maximum size `max`, additon callback `on_add` and removal callback `on_remove`.
    '''
    if maxsize < 1:
      raise ValueError("maxsize must be >= 1, got: %r" % (maxsize,))
    self.maxsize = maxsize
    self.on_add = on_add
    self.on_remove = on_remove
    self._lock = RLock()
    self._reset()

  def _reset(self):
    self._cache = {}
    self._cache_seq = {}
    self._seq = 0
    self._stash = deque()

  def __repr__(self):
    return "<%s %s>" % (self.__class__.__name__, self._cache)

  def _selfcheck(self):
    ''' Perform various internal self checks, raise on failure.
    '''
    if len(self) > self.maxsize:
      raise RuntimeError(
          "maxsize=%d, len(self)=%d - self too big" % (self.maxsize, len(self)))
    if len(self) < len(self._stash):
      raise RuntimeError(
          "len(self)=%d, len(_stash)=%d - _stash too small" % (len(self), len(self._stash)))
    cache = self._cache
    cache_seq = self._cache_seq
    if len(cache) != len(cache_seq):
      raise RuntimeError(
          "len(_cache)=%d != len(_cache_seq)=%d" % (len(cache), len(cache_seq)))

  def _prune(self, limit=None):
    ''' Reduce the cache to the specified limit, by default the cache maxsize.
    '''
    if limit is None:
      limit = self.maxsize
    cache = self._cache
    cache_seq = self._cache_seq
    cachesize = len(cache)
    stash = self._stash
    while cachesize > limit:
      qseq, qkey = stash.popleft()
      if qkey in cache:
        seq = cache_seq[qkey]
        if seq == qseq:
          # do not del cache[key] directly, we want the callback to fire
          del self[qkey]
          cachesize -= 1
        elif seq < qseq:
          raise RuntimeError("_prune: seq error")

  @locked
  def _winnow(self):
    ''' Remove all obsolete entries from the stash of (seq, key).
        This is called if the stash exceeds double the current size of the
        cache.
    '''
    newstash = deque()
    stash = self._stash
    cache_seq = self._cache_seq
    for qseq, qkey in stash:
      try:
        seq = cache_seq[qkey]
      except KeyError:
        continue
      if qseq == seq:
        newstash.append((qseq, qkey))
    self._stash = newstash

  def __getitem__(self, key):
    return self._cache[key]

  def get(self, key, default=None):
    try:
      return self._cache[key]
    except KeyError:
      return default

  @locked
  def __setitem__(self, key, value):
    ''' Store the item in the cache. Prune if necessary.
    '''
    cache = self._cache
    cache_seq = self._cache_seq
    seq = self._seq
    self._seq = seq + 1
    cache[key] = value
    cache_seq[key] = seq
    callback = self.on_add
    if callback:
      callback(key, value)
    cachesize = len(cache)
    if cachesize > self.maxsize:
      self._prune()
    elif cachesize * 2 < len(self._stash):
      self._winnow()
    self._stash.append((seq, key))

  @locked
  def __delitem__(self, key):
    ''' Delete the specified `key`, calling the on_remove callback.
    '''
    value = self._cache[key]
    callback = self.on_remove
    if callback:
      callback(key, value)
    del self._cache[key]
    del self._cache_seq[key]

  def __len__(self):
    return len(self._cache)

  def __contains__(self, key):
    return key in self._cache

  def __eq__(self, other):
    return self._cache == other

  def __ne__(self, other):
    return not (self == other)

  @locked
  def flush(self):
    ''' Clear the cache.
    '''
    cache = self._cache
    keys = list(cache.keys())
    for key in keys:
      del self[key]
    self._reset()

def lru_cache(maxsize=None, cache=None, on_add=None, on_remove=None):
  ''' Enhanced workalike of @functools.lru_cache.
  '''
  if cache is None:
    if maxsize is None:
      maxsize = 32
    cache = LRU_Cache(maxsize=maxsize, on_add=on_add, on_remove=on_remove)
  elif maxsize is not None:
    raise ValueError("maxsize must be None if cache is not None: maxsize=%r, cache=%r"
                     % (maxsize, cache))

  def caching_func(*a, **kw):
    key = (tuple(a), tuple(kw.keys()), tuple(kw.values()))
    try:
      value = cache[key]
    except KeyError:
      value = func(*a, **kw)
      cache[key] = value
    return value
  return caching_func

class Cache:

  def __init__(self, backend):
    _caches.append(self)
    self.__cache = {}
    self.__seq = 0
    self.__backend = backend
    self.__hits = 0
    self.__misses = 0
    self.__xrefs = []
    self.__preloaded = False

  def preloaded(self, status=True):
    self.__preloaded = status

  def addCrossReference(self, xref):
    self.__xrefs.append(xref)

  def inCache(self, key):
    if key not in self.__cache:
      return False
    c = self.__cache[key]
    return c[0] == self.__seq

  def hitMiss(self):
    return (self.__hits, self.__misses)

  def hitRatio(self):
    gets = self.__hits + self.__misses
    if gets == 0:
      return None
    return float(self.__hits) / float(gets)

  def __getattr__(self, attr):
    ##debug("CACHE GETATTR",repr(attr))
    return getattr(self.__backend, attr)

  def bump(self):
    self.__seq += 1

  def keys(self):
    if self.__preloaded:
      return self.__cache.keys()
    return self.__backend.keys()

  def getitems(self, keylist):
    inKeys = [key for key in keylist if self.inCache(key)]
    outKeys = [key for key in keylist if not self.inCache(key)]

    items = [self.findrowByKey(key) for key in inKeys]
    if outKeys:
      outItems = self.__backend.getitems(outKeys)
      for i in outItems:
        self.store(i)
      items.extend(outItems)

    return items

  def findrowByKey(self, key):
    if self.inCache(key):
      self.__hits += 1
      return self.__cache[key][1]

    self.__misses += 1
    try:
      value = self.__backend[key]
    except IndexError as e:
      value = None

    self.store(value, key)
    return value

  def __getitem__(self, key):
    # Note: we're looking up the backend, _not_ calling some subclass'
    # findrowbykey()
    row = Cache.findrowByKey(self, key)
    if row is None:
      raise IndexError("no entry with key " + repr(key))

    return row

  def store(self, value, key=None):
    if key is not None:
      assert type(key) in (tuple, int, long), "store" + \
          repr(key) + "=" + repr(value)
    else:
      key = value[self.key()]

    self.__cache[key] = (self.__seq, value)
    if value is not None:
      for xref in self.__xrefs:
        xref.store(value)

  def __setitem__(self, key, value):
    self.__backend[key] = value
    self.store(key, value)

  def __delitem__(self, key):
    del self.__backend[key]
    if key in self.__cache:
      # BUG: doesn't undo cross references
      del self.__cache[key]

class CrossReference:

  def __init__(self):
    self.flush()

  def flush(self):
    self.__index = {}

  def __getitem__(self, key):
    value = self.find(key)
    if value is None:
      raise IndexError
    return value

  def __delitem__(self, key):
    if key in self.__index:
      del self.__index[key]

  def find(self, key):
    if key not in self.__index:
      try:
        self.__index[key] = self.byKey(key)
      except IndexError:
        self.__index[key] = None

    return self.__index[key]

  def store(self, value):
    key = self.key(value)
    self.__index[self.key(value)] = value

if __name__ == '__main__':
  import cs.cache_tests
  cs.cache_tests.selftest(sys.argv)
