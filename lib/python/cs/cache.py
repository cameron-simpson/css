#!/usr/bin/python

''' A few caching facilities.
'''

from collections import deque
from threading import RLock

DISTINFO = {
    'description': "caching data structures and other lossy things with capped sizes",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
}

class LRU_Cache(object):
  ''' A simple least recently used cache.

      Unlike `functools.lru_cache`
      this provides `on_add` and `on_remove` callbacks.
  '''

  def __init__(self, maxsize, on_add=None, on_remove=None):
    ''' Initialise the LRU_Cache with maximum size `max`,
        additon callback `on_add` and removal callback `on_remove`.
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
    ''' Mapping method: get value for `key` or `default`.
    '''
    try:
      return self._cache[key]
    except KeyError:
      return default

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
    return self != other

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

  def caching_func(func, *a, **kw):
    key = (func, tuple(a), tuple(kw.keys()), tuple(kw.values()))
    try:
      value = cache[key]
    except KeyError:
      value = func(*a, **kw)
      cache[key] = value
    return value
  return caching_func
