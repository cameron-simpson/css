#!/usr/bin/python

''' A few caching data structures and other lossy things with capped sizes.
'''

from collections import deque
from collections.abc import MutableMapping
from contextlib import contextmanager
import errno
from functools import partial
from itertools import chain
import os
from os.path import (
    dirname,
    exists as existspath,
    expanduser,
    isabs as isabspath,
    join as joinpath,
    realpath,
    split as splitpath,
)
from stat import S_ISREG
from threading import Lock, RLock, Thread
import time
from typing import Any, Callable, Mapping, Optional

from cs.context import stackattrs, withif
from cs.deco import decorator, fmtdoc
from cs.fileutils import atomic_filename, DEFAULT_POLL_INTERVAL, FileState
from cs.fs import needdir, HasFSPath, validate_rpath
from cs.gimmicks import warning
from cs.hashindex import file_checksum, HASHNAME_DEFAULT
from cs.lex import r, s
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin
from cs.result import Result
from cs.seq import splitoff, unrepeated

from icontract import require

__version__ = '20250103'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.context',
        'cs.deco',
        'cs.fileutils',
        'cs.fs',
        'cs.gimmicks',
        'cs.hashindex',
        'cs.lex',
        'cs.pfx',
        'cs.queues',
        'cs.resources',
        'cs.result',
        'cs.seq',
        'icontract',
    ],
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

          class Store:
            """ A key value store with a slower backend.
            """
            def __init__(self, mapping:Mapping):
              self.mapping = CachingMapping(mapping)

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
      missing_fallthrough: bool = False,
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
        * `missing_fallthrough`: is true (default `False`) always
          fall back to the backing mapping if a key is not in the cache
    '''
    # the backing mapping
    self.mapping = mapping
    self.queue_length = queue_length
    self.delitem_bg = delitem_bg
    self.setitem_bg = setitem_bg
    self.missing_fallthrough = missing_fallthrough
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
      if self.mssing_fallthrough:
        try:
          del self._cache[k]
        except KeyError:
          pass
      else:
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

@fmtdoc
class ConvCache(HasFSPath):
  ''' A cache for conversions of file contents such as thumbnails
      or transcoded media, etc. This keeps cached results in a file
      tree based on a content key, whose default function is
      `cs.hashutils.file_checksum({HASHNAME_DEFAULT!r})`.
  '''

  # TODO: XDG path? ~/.cache/convof ?
  DEFAULT_CACHE_BASEPATH = '~/var/cache/convof'

  @fmtdoc
  def __init__(self, fspath: Optional[str] = None, content_key_func=None):
    ''' Initialise a `ConvCache`.

        Parameters:
        * `fspath`: optional base path of the cache, default from
          `ConvCache.DEFAULT_CACHE_BASEPATH`;
          if this does not exist it will be created using `os.mkdir`
        * `content_key_func`: optional function to compute a key
          from the contents of a file, default `cs.hashindex.file_checksum`
          (the {HASHNAME_DEFAULT} hash of the contents)
    '''
    if fspath is None:
      fspath = expanduser(self.DEFAULT_CACHE_BASEPATH)
    HasFSPath.__init__(self, fspath)
    if content_key_func is None:
      content_key_func = partial(file_checksum, hashname=HASHNAME_DEFAULT)
    self._content_key_func = content_key_func
    needdir(fspath)
    self._content_keys = {}

  @pfx_method
  def content_key(self, srcpath):
    ''' Return a content key for the filesystem path `srcpath`.
    '''
    srcpath = realpath(srcpath)
    S = os.stat(srcpath)
    if not S_ISREG(S.st_mode):
      raise ValueError("not a regular file")
    signature = S.st_mtime, S.st_size
    try:
      content_key, cached_signature = self._content_keys[srcpath]
    except KeyError:
      content_key = None
    else:
      if cached_signature != signature:
        content_key = None
    if content_key is None:
      content_key = self._content_key_func(srcpath)
      self._content_keys[srcpath] = content_key, signature
    return content_key

  def content_subpath(self, srcpath) -> str:
    ''' Return the content key based subpath component.

        This default assumes the content key is a hash code and
        breaks it hex representation into a 3 level hierarchy
        such as `'d6/d9/c510785c468c9aa4b7bda343fb79'`.
    '''
    content_key = self.content_key(srcpath)
    hashname, hashhex = str(content_key).split(':', 1)
    return joinpath(hashname, *splitoff(hashhex, 2, 2))

  @require(lambda conv_subpath: not isabspath(conv_subpath))
  def convof(
      self,
      srcpath,
      conv_subpath,
      conv_func,
      *,
      ext=None,
      force=False,
  ) -> str:
    ''' Return the filesystem path of the cached conversion of
        `srcpath` via `conv_func`.

        Parameters:
        * `srcpath`: the source filesystem path
        * `conv_subpath`: a name for the conversion which encompasses
          the salient aspaects such as `'png/64/64'` for a 64x64 pixel
          thumbnail in PNG format
        * `conv_func`: a callable of the form `conv_func(srcpath,dstpath)`
          to convert the contents of `srcpath` and write the result
          to the filesystem path `dstpath`
        * `ext`: an optional filename extension, default from the
          first component of `conv_subpath`
        * `force`: option flag to require conversion even if the
          cache has an entry
    '''
    # ensure that conv_subpath is a clean normalised subpath
    validate_rpath(conv_subpath)
    conv_subparts = splitpath(conv_subpath)
    if ext is None:
      # assume the first component is a file extension
      # works for things like png/64/64
      ext = conv_subparts[0]
    suffix = '.' + ext
    dstpath = self.pathto(conv_subpath, self.content_subpath(srcpath) + suffix)
    dstdirpath = dirname(dstpath)
    needdir(dstdirpath, use_makedirs=True)
    if force or not existspath(dstpath):
      with Pfx('<%s %s >%s', srcpath, conv_func, dstpath):
        with atomic_filename(
            dstpath,
            prefix=
            f'.{self.__class__.__name__}.convof--{conv_subpath.replace(os.sep, "--")}--',
            suffix=suffix,
            exists_ok=force,
        ) as T:
          pfx_call(conv_func, srcpath, T.name)
    return dstpath

_default_conv_cache = ConvCache()

def convof(srcpath, conv_subpath, conv_func, *, ext=None, force=False):
  ''' `ConvCache.convof` using the default cache.
  '''
  return _default_conv_cache.convof(
      srcpath, conv_subpath, conv_func, ext=ext, force=force
  )

@decorator
def cachedmethod(
    method, attr_name=None, poll_delay=None, sig_func=None, unset_value=None
):
  ''' Decorator to cache the result of an instance or class method
      and keep a revision counter for changes.

      The cached values are stored on the instance (`self`).
      The revision counter supports the `@revised` decorator.

      This decorator may be used in 2 modes.
      Directly:

          @cachedmethod
          def method(self, ...)

      or indirectly:

          @cachedmethod(poll_delay=0.25)
          def method(self, ...)

      Optional keyword arguments:
      * `attr_name`: the basis name for the supporting attributes.
        Default: the name of the method.
      * `poll_delay`: minimum time between polls; after the first
        access, subsequent accesses before the `poll_delay` has elapsed
        will return the cached value.
        Default: `None`, meaning the value never becomes stale.
      * `sig_func`: a signature function, which should be significantly
        cheaper than the method. If the signature is unchanged, the
        cached value will be returned. The signature function
        expects the instance (`self`) as its first parameter.
        Default: `None`, meaning no signature function;
        the first computed value will be kept and never updated.
      * `unset_value`: the value to return before the method has been
        called successfully.
        Default: `None`.

      If the method raises an exception, this will be logged and
      the method will return the previously cached value,
      unless there is not yet a cached value
      in which case the exception will be reraised.

      If the signature function raises an exception
      then a log message is issued and the signature is considered unchanged.

      An example use of this decorator might be to keep a "live"
      configuration data structure, parsed from a configuration
      file which might be modified after the program starts. One
      might provide a signature function which called `os.stat()` on
      the file to check for changes before invoking a full read and
      parse of the file.

      *Note*: use of this decorator requires the `cs.pfx` module.
  '''
  from cs.pfx import Pfx  # pylint: disable=import-outside-toplevel
  if poll_delay is not None and poll_delay <= 0:
    raise ValueError("poll_delay <= 0: %r" % (poll_delay,))
  if poll_delay is not None and poll_delay <= 0:
    raise ValueError(
        "invalid poll_delay, should be >0, got: %r" % (poll_delay,)
    )

  attr = attr_name if attr_name else method.__name__
  val_attr = '_' + attr
  sig_attr = val_attr + '__signature'
  rev_attr = val_attr + '__revision'
  lastpoll_attr = val_attr + '__lastpoll'

  # pylint: disable=too-many-branches
  def cachedmethod_wrapper(self, *a, **kw):
    with Pfx("%s.%s", self, attr):
      now = None
      value0 = getattr(self, val_attr, unset_value)
      sig0 = getattr(self, sig_attr, None)
      sig = getattr(self, sig_attr, None)
      if value0 is unset_value:
        # value unknown, needs compute
        pass
      # we have a cached value for return in the following logic
      elif poll_delay is None:
        # no repoll time, the cache is always good
        return value0
      # see if the value is stale
      lastpoll = getattr(self, lastpoll_attr, None)
      now = time.time()
      if (poll_delay is not None and lastpoll is not None
          and now - lastpoll < poll_delay):
        # reuse cache
        return value0
      # never polled or the cached value is stale, poll now
      # update the poll time
      setattr(self, lastpoll_attr, now)
      # check the signature if provided
      # see if the signature is unchanged
      if sig_func is not None:
        try:
          sig = sig_func(self)
        except Exception as e:  # pylint: disable=broad-except
          # signature function fails, use the cache
          warning("sig func %s(self): %s", sig_func, e, exc_info=True)
          return value0
        if sig0 is not None and sig0 == sig:
          # signature unchanged
          return value0
        # update signature
        setattr(self, sig_attr, sig)
      # compute the current value
      try:
        value = method(self, *a, **kw)
      except Exception as e:  # pylint: disable=broad-except
        # computation fails, return cached value
        if value0 is unset_value:
          # no cached value
          raise
        warning("exception calling %s(self): %s", method, e, exc_info=True)
        return value0
      # update the cache
      setattr(self, val_attr, value)
      # bump revision if the value changes
      # noncomparable values are always presumed changed
      changed = value0 is unset_value or value0 is not value
      if not changed:
        try:
          changed = value0 != value
        except TypeError:
          changed = True
      if changed:
        setattr(self, rev_attr, (getattr(self, rev_attr, 0) or 0) + 1)
      return value

  ##  Doesn't work, has no access to self. :-(
  ##  TODO: provide a .flush() function to clear the cached value
  ##  cachedmethod_wrapper.flush = lambda: setattr(self, val_attr, unset_value)

  return cachedmethod_wrapper

@decorator
def file_based(
    func,
    attr_name=None,
    filename=None,
    poll_delay=None,
    sig_func=None,
    **dkw
):
  ''' A decorator which caches a value obtained from a file.

      In addition to all the keyword arguments for `@cs.cache.cachedmethod`,
      this decorator also accepts the following arguments:
      * `attr_name`: the name for the associated attribute, used as
        the basis for the internal cache value attribute
      * `filename`: the filename to monitor.
        Default from the `._{attr_name}__filename` attribute.
        This value will be passed to the method as the `filename` keyword
        parameter.
      * `poll_delay`: delay between file polls, default `DEFAULT_POLL_INTERVAL`.
      * `sig_func`: signature function used to encapsulate the relevant
        information about the file; default
        cs.filestate.FileState({filename}).

      If the decorated function raises OSError with errno == ENOENT,
      this returns None. Other exceptions are reraised.
  '''
  if attr_name is None:
    attr_name = func.__name__
  filename_attr = '_' + attr_name + '__filename'
  filename0 = filename
  if poll_delay is None:
    poll_delay = DEFAULT_POLL_INTERVAL
  sig_func = dkw.pop('sig_func', None)
  if sig_func is None:

    def sig_func(self):
      ''' The default signature function: `FileState(filename,missing_ok=True)`.
      '''
      filename = filename0
      if filename is None:
        filename = getattr(self, filename_attr)
      return FileState(filename, missing_ok=True)

  def wrap0(self, *a, **kw):
    ''' Inner wrapper for `func`.
    '''
    filename = kw.pop('filename', None)
    if filename is None:
      if filename0 is None:
        filename = getattr(self, filename_attr)
      else:
        filename = filename0
    kw['filename'] = filename
    try:
      return func(self, *a, **kw)
    except OSError as e:
      if e.errno == errno.ENOENT:
        return None
      raise

  dkw['attr_name'] = attr_name
  dkw['poll_delay'] = poll_delay
  dkw['sig_func'] = sig_func
  return cachedmethod(**dkw)(wrap0)

@decorator
def file_property(func, **dkw):
  ''' A property whose value reloads if a file changes.
  '''
  return property(file_based(func, **dkw))
