#!/usr/bin/python
#
# A cache store, connected to a fast cache and a slower backend.
#       - Cameron Simpson <cs@cskk.id.au> 07dec2007
#

''' Caching Stores and associated data structures.
'''

from collections import namedtuple
from contextlib import contextmanager
from os.path import isdir as isdirpath
from tempfile import TemporaryFile
from threading import Thread

from icontract import require

from cs.context import stackattrs
from cs.fileutils import RWFileBlockCache, datafrom_fd
from cs.logutils import error
from cs.pfx import pfx_method
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin, RunState, RunStateMixin
from cs.result import Result
from cs.threads import bg as bg_thread

from . import defaults, MAX_FILE_SIZE, Lock, RLock
from .store import _BasicStoreCommon, BasicStoreSync, MappingStore

DEFAULT_CACHEFILE_HIGHWATER = MAX_FILE_SIZE
DEFAULT_MAX_CACHEFILES = 3

class FileCacheStore(BasicStoreSync):
  ''' A Store wrapping another Store that provides fast access to
      previously fetched data and fast storage of new data,
      using asynchronous updates to the backing Store (which may be `None`).

      This class is a thin Store shaped shim over a `FileDataMappingProxy`,
      which does the heavy lifting of storing data.
  '''

  @require(lambda name: isinstance(name, str))
  @require(
      lambda backend: backend is None or
      isinstance(backend, _BasicStoreCommon)
  )
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

        Other keyword arguments are passed to `BasicStoreSync.__init__`.
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

_CachedData = namedtuple('CachedData', 'cachefile offset length')

class CachedData(_CachedData):
  ''' A CachedData record, with cachefile, offset, length and implied data.
  '''

  def fetch(self):
    ''' Fetch the data associated with this CachedData instance.
    '''
    return self.cachefile.get(self.offset, self.length)

class FileDataMappingProxy(MultiOpenMixin, RunStateMixin):
  ''' Mapping-like class to cache data chunks to bypass gdbm indices and the like.
      Data are saved immediately into an in memory cache and an asynchronous
      worker copies new data into a cache file and also to the backend
      storage.
  '''

  @pfx_method
  def __init__(
      self,
      backend,
      *,
      dirpath=None,
      max_cachefile_size=None,
      max_cachefiles=None,
      runstate=None,
  ):
    ''' Initialise the cache.

        Parameters:
        * `backend`: mapping underlying us
        * `dirpath`: directory to store cache files
        * `max_cachefile_size`: maximum cache file size; a new cache
          file is created if this is exceeded; default:
          DEFAULT_CACHEFILE_HIGHWATER
        * `max_cachefiles`: number of cache files to keep around; no
          more than this many cache files are kept at a time; default:
          DEFAULT_MAX_CACHEFILES
    '''
    RunStateMixin.__init__(self, runstate=runstate)
    if max_cachefile_size is None:
      max_cachefile_size = DEFAULT_CACHEFILE_HIGHWATER
    if max_cachefiles is None:
      max_cachefiles = DEFAULT_MAX_CACHEFILES
    self.backend = backend
    if not isdirpath(dirpath):
      raise ValueError("dirpath=%r: not a directory" % (dirpath,))
    self.dirpath = dirpath
    self.max_cachefile_size = max_cachefile_size
    self.max_cachefiles = max_cachefiles
    self.cached = {}  # map h => data
    self.saved = {}  # map h => _CachedData(cachefile, offset, length)
    self._lock = Lock()
    self.cachefiles = []
    self._add_cachefile()
    self.runstate.notify_cancel.add(lambda rs: self.close())

  @contextmanager
  def startup_shutdown(self):
    ''' Startup the proxy.
    '''
    with super().startup_shutdown():
      workQ = IterableQueue()
      worker = bg_thread(self._work, args=(workQ,), name="%s WORKER" % (self,))
      with stackattrs(self, _workQ=workQ):
        try:
          yield
        finally:
          workQ.close()
          worker.join()
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
    for h in list(self.cached.keys()):
      yield h
      seen.add(h)
    saved = self.saved
    with self._lock:
      saved_keys = list(saved.keys())
    for h in saved_keys:
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
    self._workQ.put((h, data, False))
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
    self._workQ.put((h, data, True))

  def _work(self, workQ):
    for h, data, in_backend in workQ:
      with self._lock:
        if self._getref(h):
          # already in file cache, therefore already sent to backend
          continue
      cachefile = self.cachefiles[0]
      offset = cachefile.put(data)
      with self._lock:
        self.saved[h] = CachedData(cachefile, offset, len(data))
        # release memory cache entry
        try:
          del self.cached[h]
        except KeyError:
          pass
        if offset + len(data) >= self.max_cachefile_size:
          # roll over to new cache file
          self._add_cachefile()
      # store into the backend
      if not in_backend:
        backend = self.backend
        if backend:
          self.backend[h] = data

def MemoryCacheStore(name, max_data, hashclass=None):
  ''' Factory to make a MappingStore of a MemoryCacheMapping.
  '''
  return MappingStore(name, MemoryCacheMapping(max_data), hashclass=hashclass)

class MemoryCacheMapping:
  ''' A lossy MT-safe in-memory mapping of hashcode->data.
  '''

  def __init__(self, max_data, lock=None):
    if max_data < 65536:
      raise ValueError("max_data should be >= 65536, got: %s" % (max_data,))
    if lock is None:
      lock = Lock()
    self._lock = lock
    self.max_data = max_data
    self.used_data = 0
    self.mapping = {}
    # keep a revision/use counter for hashcodes, used to decide
    # which hashcodes to purge when self.used_data > self.max_data
    self._ticker = 0
    self._tick = {}
    self._skip_flush = 32

  def __str__(self):
    return (
        "%s[max_data=%d:used_data=%d:hashcodes=%d]" % (
            type(self).__name__, self.max_data, self.used_data,
            len(self.mapping)
        )
    )

  def __len__(self):
    return len(self.mapping)

  def __contains__(self, hashcode):
    mapping = self.mapping
    with self._lock:
      return hashcode in mapping

  def __getitem__(self, hashcode):
    mapping = self.mapping
    with self._lock:
      data = mapping[hashcode]
      self._tick[hashcode] = self._ticker
      self._ticker += 1
    return data

  def get(self, hashcode, default=None):
    ''' Get the data associated with `hashcode`, or `default`.
    '''
    mapping = self.mapping
    with self._lock:
      try:
        data = mapping[hashcode]
      except KeyError:
        return default
      self._tick[hashcode] = self._ticker
      self._ticker += 1
    return data

  def __setitem__(self, hashcode, data):
    mapping = self.mapping
    with self._lock:
      if hashcode in mapping:
        # DEBUG: sanity check
        if data != mapping[hashcode]:
          raise RuntimeError(
              "data mismatch: hashcode=%s, data=%r vs mapping[hashcode]=%r" %
              (hashcode, data, mapping[hashcode])
          )
      else:
        mapping[hashcode] = data
        used_data = self.used_data = self.used_data + len(data)
        self._tick[hashcode] = self._ticker
        self._ticker += 1
        max_data = self.max_data
        if used_data > max_data and len(mapping) > 1:
          if self._skip_flush > 0:
            self._skip_flush -= 1
          else:
            _tick = self._tick
            for _, old_hashcode in sorted(
                (tick, h) for h, tick in _tick.items()):
              old_data = mapping.pop(old_hashcode)
              used_data -= len(old_data)
              del _tick[old_hashcode]
              if used_data <= max_data:
                break
            self._skip_flush = 32

  def keys(self):
    ''' Return an iterator over the mapping;
        required for use of HashCodeUtilsMixin.hashcodes_from.
    '''
    return self.mapping.keys()

  __iter__ = keys

class BlockMapping:
  ''' A Block's contents mapped onto a temporary file.
  '''

  def __init__(self, tempf, offset, size):
    ''' Initialise the mapping.

        Parameters:
        * `tempf`: the BlockTempfile
        * `offset`: where the Block data start
        * `size`: the total size of the Block
    '''
    self.tempf = tempf
    self.offset = offset
    self.size = size
    self.filled = 0

  def pread(self, size, offset):
    ''' Return data from the main tempfile.
    '''
    assert offset >= 0
    assert offset + size <= self.filled
    return self.tempf.pread(size, self.offset + offset)

  def datafrom(self, offset=0, maxlength=None):
    ''' Yield data from the underlying temp file.

        Parameters:
        * `offset`: start of data, default `0`
        * `maxlength`: maximum amount of data to yield,
          default `self.filled - offset`
    '''
    assert offset >= 0
    if maxlength is None:
      maxlength = self.filled - offset
    else:
      maxlength = min(maxlength, self.filled - offset)
    if maxlength <= 0:
      return
    yield from datafrom_fd(
        self.tempf.fileno(), self.offset + offset, maxlength=maxlength
    )

class BlockTempfile:
  ''' Manage a temporary file which contains the contents of various Blocks.
  '''

  def __init__(self, cache, tmpdir, suffix):
    self.cache = cache
    self.tempfile = TemporaryFile(dir=tmpdir, suffix=suffix)
    self.hashcodes = {}
    self.size = 0
    self._lock = RLock()

  def close(self):
    ''' Release the tempfile and unmap the associates hashcodes.
    '''
    blockmap = self.cache.blockmap
    for h in self.hashcodes:
      del blockmap[h]
    self.hashcodes = None
    self.tempfile.close()
    self.tempfile = None

  def pread(self, size, offset):
    ''' Read `size` bytes from the temp file at `offset`.
    '''
    tempf = self.tempfile
    with self._lock:
      if tempf.tell() != offset:
        tempf.seek(offset)
      return tempf.read(size)

  def _pwrite(self, data, offset):
    ''' Write data into the tempfile.

        This is not public as these files are read only.
    '''
    tempf = self.tempfile
    with self._lock:
      if tempf.tell() != offset:
        tempf.seek(offset)
      return tempf.write(data)

  def append_block(self, block, runstate):
    ''' Add a Block to this tempfile.

        Parameters:
        * `block`: the Block to append to this tempfile.
        * `runstate`: a RunState that can be used to cancel the
          tempfile data population Thread

        A Thread is dispatched to load the Block data into the temp file.
    '''
    h = block.hashcode
    bsize = len(block)
    assert bsize > 0
    with self._lock:
      offset = self.size
      self._pwrite(b'\0', offset + bsize - 1)
      self.size = offset + bsize
    bm = BlockMapping(self.tempfile, offset, bsize)
    with self._lock:
      self.hashcodes[h] = bm
    T = Thread(
        name="%s._infill(%s)" % (type(self).__name__, block),
        target=self._infill,
        args=(defaults.S, bm, offset, block, runstate)
    )
    T.daemon = True
    T.start()
    return bm

  def _infill(self, S, bm, offset, block, runstate):
    ''' Load the Block data into the tempfile,
        updating the `BlockMapping.filled` attribute as we go.
    '''
    with S:
      needed = len(block)
      for data in block:
        if runstate.cancelled:
          break
        assert len(data) <= needed
        written = self._pwrite(data, offset)
        assert written == len(data)
        offset += written
        needed -= written
        bm.filled += written

# pylint: disable=too-many-instance-attributes
class BlockCache:
  ''' A temporary file based cache for whole Blocks.

      This is to support filesystems' and files' direct read/write
      actions by passing them straight through to this cache is
      there's a mapping.

      We accrue complete Block contents in unlinked files.
  '''

  # default cace size
  MAX_FILES = 32
  MAX_FILE_SIZE = 1024 * 1024 * 1024

  def __init__(
      self, tmpdir=None, suffix='.dat', max_files=None, max_file_size=None
  ):
    if max_files is None:
      max_files = self.MAX_FILES
    if max_file_size is None:
      max_file_size = self.MAX_FILE_SIZE
    self.tmpdir = tmpdir
    self.suffix = suffix
    self.max_files = max_files
    self.max_file_size = max_file_size
    self.blockmaps = {}  # hashcode -> BlockMapping
    self._tempfiles = []  # in play BlockTempfiles
    self._lock = Lock()
    self.runstate = RunState()
    self.runstate.start()

  def close(self):
    ''' Release all the blockmaps.
    '''
    self.runstate.cancel()
    with self._lock:
      self.blockmaps = {}
      for tempf in self._tempfiles:
        tempf.close()
      self._tempfiles = []

  def __getitem__(self, hashcode):
    ''' Fetch BlockMapping associated with `hashcode`, raise KeyError if missing.
    '''
    return self.blockmaps[hashcode]

  def get_blockmap(self, block):
    ''' Add the specified Block to the cache, return the BlockMapping.
    '''
    blockmaps = self.blockmaps
    h = block.hashcode
    with self._lock:
      bm = blockmaps.get(h)
      if bm is None:
        tempfiles = self._tempfiles
        if tempfiles:
          tempf = tempfiles[-1]
          if tempf.size >= self.max_file_size:
            tempf = None
        else:
          tempf = None
        if tempf is None:
          while len(tempfiles) >= self.max_files:
            otempf = tempfiles.pop(0)
            otempf.close()
          tempf = BlockTempfile(self, self.tmpdir, self.suffix)
          tempfiles.append(tempf)
        bm = tempf.append_block(block, self.runstate)
        blockmaps[h] = bm
    return bm
