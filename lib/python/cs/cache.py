#!/usr/bin/python

''' A few caching facilities.
'''

from collections import deque
from collections.abc import MutableMapping
from contextlib import contextmanager
from itertools import chain
from threading import Lock, Thread
import time
from typing import Mapping

from cs.context import stackattrs, withif
from cs.lex import r, s
from cs.logutils import warning
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin
from cs.result import Result
from cs.seq import unrepeated

DISTINFO = {
    'description':
    "caching data structures and other lossy things with capped sizes",
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

  def __init__(self, max_size, *, on_add=None, on_remove=None):
    ''' Initialise the LRU_Cache with maximum size `max`,
        additon callback `on_add` and removal callback `on_remove`.
    '''
    if max_size < 1:
      raise ValueError("max_size must be >= 1, got: %r" % (max_size,))
    self.max_size = max_size
    self.on_add = on_add
    self.on_remove = on_remove
    self._lock = Lock()
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
    if len(self) > self.max_size:
      raise RuntimeError(
          "max_size=%d, len(self)=%d - self too big" %
          (self.max_size, len(self))
      )
    if len(self) < len(self._stash):
      raise RuntimeError(
          "len(self)=%d, len(_stash)=%d - _stash too small" %
          (len(self), len(self._stash))
      )
    cache = self._cache
    cache_seq = self._cache_seq
    if len(cache) != len(cache_seq):
      raise RuntimeError(
          "len(_cache)=%d != len(_cache_seq)=%d" %
          (len(cache), len(cache_seq))
      )

  def _prune(self, limit=None):
    ''' Reduce the cache to the specified limit, by default the cache max_size.
    '''
    if limit is None:
      limit = self.max_size
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
    with self._lock:
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
      if cachesize > self.max_size:
        self._prune()
      elif cachesize * 2 < len(self._stash):
        self._winnow()
      self._stash.append((seq, key))

  def __delitem__(self, key):
    ''' Delete the specified `key`, calling the on_remove callback.
    '''
    with self._lock:
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

  def keys(self):
    ''' Keys from the cache.
    '''
    return self._cache.keys()

  def items(self):
    ''' Items from the cache.
    '''
    return self._cache.items()

  def flush(self):
    ''' Clear the cache.
    '''
    with self._lock:
      cache = self._cache
      keys = list(cache.keys())
      for key in keys:
        del self[key]
      self._reset()

# TODO: use @decorator
def lru_cache(max_size=None, cache=None, on_add=None, on_remove=None):
  ''' Enhanced workalike of @functools.lru_cache.
  '''
  if cache is None:
    if max_size is None:
      max_size = 32
    cache = LRU_Cache(max_size=max_size, on_add=on_add, on_remove=on_remove)
  elif max_size is not None:
    raise ValueError(
        "max_size must be None if cache is not None: max_size=%r, cache=%r" %
        (max_size, cache)
    )

  def caching_func(func, *a, **kw):
    key = (func, tuple(a), tuple(kw.keys()), tuple(kw.values()))
    try:
      value = cache[key]
    except KeyError:
      value = func(*a, **kw)
      cache[key] = value
    return value

  return caching_func

# pylint: disable=too-many-instance-attributes
class CachingMapping(MultiOpenMixin, MutableMapping):
  ''' A caching front end for another mapping.
      This is intended as a generic superclass for a proxy to a
      slower mapping such as a database or remote key value store.

      Note that this subclasses `MultiOpenMixin` to start/stop the worker `Thread`.
      Users must enclose use of a `CachingMapping` in a `with` statement.
      If subclasses also subclass `MultiOpenMixin` their `startup_shutdown`
      method needs to also call our `startup_shutdown` method.

      Example:

          class Store(CachingMapping):
            """ A key value store with a slower backend.
            """
            def __init__(self, storage:Mapping):
              super().__init__(storage)

          .....
          S = Store(slow_mapping)
          with S:
            ... work with S ...
  '''

  def __init__(
      self,
      mapping: Mapping,
      *,
      max_size=1024,
      queue_length=1024,
      delitem_bg: Optional[Callable[(Any,), Result]] = None,
      setitem_bg: Optional[Callable[(Any, Any), Result]] = None,
  ):
    ''' Initialise the cache.

        Parameters:
        * `mapping`: the backing store, a mapping
        * `max_size`: optional maximum size for the cache, default 1024
        * `queue_length`: option size for the queue to the worker, default 1024
        * `delitem_bg`: optional callable to queue a delete of a
          key in the backing store; if unset then deleted are
          serialised in the worker thread
        * `setitem_bg`: optional callable to queue setting the value
          for a key in the backing store; if unset then deleted are
          serialised in the worker thread
    '''
    # the backing mapping
    self.mapping = mapping
    self.queue_length = queue_length
    self.delitem_bg = delitem_bg
    self.setitem_bg = setitem_bg
    self._cache = LRU_Cache(max_size)
    # a worker Thread to apply updates
    self._worker = None
    # a Queue to put updates to for processing by the worker
    self._workQ = None
    # the sentinel for a queue flush
    self.FLUSH = object()
    # the sentinel for a deletion update
    self.MISSING = object()
    self._lock = Lock()
    self._backing_lock = Lock()

  def __str__(self):
    return f'{self.__class__.__name__}({s(self.mapping)})'

  def __repr__(self):
    return f'{self.__class__.__name__}({r(self.mapping)})'

  @contextmanager
  def startup_shutdown(self):

    def worker(
        *,
        lock,
        backing_lock,
        mapping,
        cache,
        delitem_bg,
        setitem_bg,
        MISSING,
        FLUSH,
    ):
      ''' The worker function which processes updates.
      '''
      for batch in Q.iter_batch(batch_size=16):
        for k, v in batch:
          with backing_lock:
            if k is FLUSH:
              # v is a Result, complete it
              v.result = time.time()
            elif v is MISSING:
              if delitem_bg:
                delitem_bg(k)
              else:
                try:
                  del mapping[k]
                except KeyError:
                  pass
            elif setitem_bg:
              # dispatch the setitem
              setitem_bg(k, v)
            else:
              mapping[k] = v
              with lock:
                try:
                  del cache[k]
                except KeyError as e:
                  warning(f'{self}: del cache[{r(k)}]: {e}')

    Q = IterableQueue(self.queue_length)
    with withif(self.mapping):
      T = Thread(
          target=worker,
          name=f'{self} worker',
          # pylint: disable=use-dict-literal
          kwargs=dict(
              lock=self._lock,
              backing_lock=self._backing_lock,
              mapping=self.mapping,
              cache=self._cache,
              delitem_bg=self.delitem_bg,
              setitem_bg=self.setitem_bg,
              MISSING=self.MISSING,
              FLUSH=self.FLUSH,
          ),
      )
      T.start()
      with stackattrs(self, _workQ=Q, _worker=T):
        try:
          yield
        finally:
          Q.close()
          T.join()

  def __len__(self):
    return len(self.mapping)

  def __contains__(self, k):
    try:
      v = self._cache[k]
    except KeyError:
      return k in self.mapping
    return v is not self.MISSING

  def __getitem__(self, k):
    try:
      v = self._cache[k]
    except KeyError:
      return self.mapping[k]
    if v is self.MISSING:
      raise KeyError(k)
    return v

  def __setitem__(self, k, v):
    with self._lock:
      self._cache[k] = v
      self._workQ.put((k, v))

  def __delitem__(self, k):
    with self._lock:
      self._cache[k] = self.MISSING
      self._workQ.put((k, self.MISSING))

  def keys(self):
    ''' Generator yielding the keys.
    '''
    MISSING = self.MISSING
    yield from unrepeated(
        chain(
            (k for k, v in self._cache.items() if v is not MISSING),
            self.mapping.keys()
        )
    )

  def __iter__(self):
    yield from self.keys()

  def items(self):
    ''' Generator yielding `(k,v)` pairs.
    '''
    # pylint: disable=consider-using-dict-items
    for k in self.keys():
      try:
        v = self[k]
      except KeyError:  # accomodate race between keys() and contents
        pass
      else:
        yield k, v

  def flush(self):
    ''' Wait for outstanding requests in the queue to complete.
        Return the UNIX time of completion.
    '''
    R = Result()
    self._workQ.put((self.FLUSH, R))
    return R()
