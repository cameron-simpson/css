#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Basic Store classes.

    Throughout these classes the term 'channel' means an object with a .get()
    method and usually a .put() method (unless it is instantiated with a
    pre-queued value for the .get()). It may be a Queue, Q1, Channel, Get1
    or any similar object for delivery of a result "later".
'''

from __future__ import with_statement
from abc import ABC, abstractmethod
import sys
from cs.later import Later
from cs.logutils import debug, warning, error
from cs.pfx import Pfx
from cs.progress import Progress
from cs.resources import MultiOpenMixin
from cs.result import Result, report
from cs.seq import Seq
from . import defaults
from .datadir import DataDir, PlatonicDir
from .hash import DEFAULT_HASHCLASS, HashCodeUtilsMixin

class MissingHashcodeError(KeyError):
  ''' Subclass of KeyError raised when accessing a hashcode is not present in the Store.
  '''
  def __init__(self, hashcode):
    KeyError.__init__(self, str(hashcode))
    self.hashcode = hashcode
  def __str__(self):
    return "missing hashcode: %s" % (self.hashcode,)

class _BasicStoreCommon(MultiOpenMixin, HashCodeUtilsMixin, ABC):
  ''' Core functions provided by all Stores.

      Subclasses should not subclass this class but BasicStoreSync
      or BasicStoreAsync; these provide the *_bg or non-*_bg sibling
      methods of those described below so that a subclass need only
      implement the synchronous or asynchronous forms. Most local
      Stores will derive from BasicStoreSync and remote Stores
      derive from BasicStoreAsync.

      A subclass should provide thread-safe implementations of the following
      methods:

        .add(block) -> hashcode
        .get(hashcode, [default=None]) -> block (or default)
        .contains(hashcode) -> boolean
        .flush()

      A subclass _may_ provide thread-safe implementations of the following
      methods:

        .hashcodes(starting_hashcode, length) -> iterable-of-hashcodes

      The background (*_bg) functions return cs.later.LateFunction instances
      for deferred collection of the operation result.

      A convenience .lock attribute is provided for simple mutex use.

      The .readonly attribute may be set to prevent writes and trap
      surprises; it relies on assert statements.

      The .writeonly attribute may be set to trap surprises when no blocks
      are expected to be fetched; it relies on asssert statements.

      The mapping special methods __getitem__ and __contains__ call
      the implementation methods .get() and .contains().
  '''

  _seq = Seq()

  def __init__(self, name, capacity=None, hashclass=None, lock=None):
    with Pfx("_BasicStoreCommon.__init__(%s,..)", name):
      if not isinstance(name, str):
        raise TypeError("initial `name` argument must be a str, got %s", type(name))
      if name is None:
        name = "%s%d" % (self.__class__.__name__, next(_BasicStoreCommon._seq()))
      if capacity is None:
        capacity = 4
      if hashclass is None:
        hashclass = DEFAULT_HASHCLASS
      self._attrs = {}
      MultiOpenMixin.__init__(self, lock=lock)
      self.name = name
      self.hashclass = hashclass
      self.logfp = None
      self.mountdir = None
      self.readonly = False
      self.writeonly = False
      self._archives = {}
      self.__funcQ = Later(capacity, name="%s:Later(__funcQ)" % (self.name,)).open()

  def __str__(self):
    params = [
        attr + '=' + str(val) for attr, val in sorted(self._attrs.items())
    ]
    return "%s:%s(%s)" % (
        self.__class__.__name__, self.hashclass.HASHNAME,
        ','.join([repr(self.name)] + params)
    )

  __repr__ = __str__

  # Basic support for putting Stores in sets.
  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  def _defer(self, func, *args, **kwargs):
    return self.__funcQ.defer(func, *args, **kwargs)

  ###################
  ## Special methods.
  ##

  def __contains__(self, h):
    ''' Test if the supplied hashcode is present in the store.
    '''
    return self.contains(h)

  def __iter__(self):
    return self.hashcodes_from()

  def keys(self):
    return iter(self)

  def __getitem__(self, h):
    ''' Return the data bytes associated with the supplied hashcode.
        Raise KeyError if the hashcode is not present.
    '''
    block = self.get(h)
    if block is None:
      raise MissingHashcodeError(h)
    return block

  def __setitem__(self, h, data):
    ''' Save `data` against hash key `h`.
        Actually saves the data against the Store's hash function
        and raises ValueError if that does not match the supplied
        `h`.
    '''
    h2 = self.add(data)
    if h != h2:
      raise ValueError("h:%s != hash(data):%s" % (h, h2))

  def __enter__(self):
    defaults.pushStore(self)
    return MultiOpenMixin.__enter__(self)

  def __exit__(self, exc_type, exc_value, traceback):
    if exc_value:
      import traceback as TB
      TB.print_tb(traceback, file=sys.stderr)
    defaults.popStore()
    return MultiOpenMixin.__exit__(self, exc_type, exc_value, traceback)

  def hash(self, data):
    ''' Return a Hash object from data bytes.
        NB: does _not_ store the data.
    '''
    return self.hashclass.from_chunk(data)

  def startup(self):
    # Later already open
    pass

  def shutdown(self):
    ''' Called by final MultiOpenMixin.close().
    '''
    self.__funcQ.close()
    if not self.__funcQ.closed:
      debug("%s.shutdown: __funcQ not closed yet", self)
    self.__funcQ.wait()

  def bg(self, func, *a, **kw):
    ''' Dispatch a Thread to run `func` with this Store as the default, return a Result to collect its value.
    '''
    R = Result(name="%s:%s" % (self, func))
    def func2():
      with self:
        return func(*a, **kw)
    R.bg(func2)
    return R

  def missing(self, hashes):
    ''' Yield hashcodes that are not in the store from an iterable hash
        code list.
    '''
    for h in hashes:
      if h not in self:
        yield h

  ##########################################################################
  # Core Store methods, all abstract.
  @abstractmethod
  def add(self, data):
    raise NotImplemented

  @abstractmethod
  def add_bg(self, data):
    raise NotImplemented

  @abstractmethod
  def get(self, h):
    raise NotImplemented

  @abstractmethod
  def get_bg(self, h):
    raise NotImplemented

  @abstractmethod
  def contains(self, h):
    raise NotImplemented

  @abstractmethod
  def contains_bg(self, h):
    raise NotImplemented

  @abstractmethod
  def flush(self):
    raise NotImplemented

  @abstractmethod
  def flush_bg(self):
    raise NotImplemented

  ##########################################################################
  # Archive support.
  def add_archive(self, name, archive):
    ''' Add an `archive` by `name`.
    '''
    archives = self._archives
    with self._lock:
      if name in archives:
        raise KeyError("archive named %r already exists" % (name,))
      archives[name] = archive

  def get_archive(self, name):
    ''' Fetch the named archive or None.
    '''
    return self._archives.get(name)

class BasicStoreSync(_BasicStoreCommon):
  ''' Subclass of _BasicStoreCommon expecting synchronous operations and providing asynchronous hooks, dual of BasicStoreAsync.
  '''

  #####################################
  ## Background versions of operations.
  ##

  def add_bg(self, data):
    return self._defer(self.add, data)

  def get_bg(self, h):
    return self._defer(self.get, h)

  def contains_bg(self, h):
    return self._defer(self.contains, h)

  def flush_bg(self):
    return self._defer(self.flush)

class BasicStoreAsync(_BasicStoreCommon):
  ''' Subclass of _BasicStoreCommon expecting asynchronous operations and providing synchronous hooks, dual of BasicStoreSync.
  '''

  #####################################
  ## Background versions of operations.
  ##

  def add(self, data):
    return self.add_bg(data)()

  def get(self, h):
    return self.get_bg(h)()

  def contains(self, h):
    return self.contains_bg(h)()

  def flush(self):
    return self.flush_bg()()

class MappingStore(BasicStoreSync):
  ''' A Store built on an arbitrary mapping object.
  '''

  def __init__(self, name, mapping, **kw):
    BasicStoreSync.__init__(self, name, **kw)
    self.mapping = mapping
    self._attrs.update(mapping=mapping)

  def startup(self):
    mapping = self.mapping
    try:
      openmap = mapping.open
    except AttributeError:
      pass
    else:
      openmap()
    super().startup()

  def shutdown(self):
    mapping = self.mapping
    try:
      closemap = mapping.close
    except AttributeError:
      pass
    else:
      closemap()
    super().shutdown()

  def add(self, data):
    with Pfx("add %d bytes", len(data)):
      mapping = self.mapping
      h = self.hash(data)
      if h not in mapping:
        mapping[h] = data
      else:
        if False:
          with Pfx("EXISTING HASH"):
            try:
              data2 = mapping[h]
            except Exception as e:
              error("fetch FAILED: %s", e)
            else:
              if data != data2:
                warning("data mismatch: .add data=%r, Store data=%r", data, data2)
      return h

  def get(self, h, default=None):
    try:
      data = self.mapping[h]
    except KeyError:
      return default
    return data

  def contains(self, h):
    return h in self.mapping

  def flush(self):
    ''' Call the .flush method of the underlying mapping, if any.
    '''
    map_flush = getattr(self.mapping, 'flush', None)
    if map_flush is not None:
      map_flush()

  def __len__(self):
    return len(self.mapping)

  def __iter__(self):
    ''' Return iterator over the mapping; required for use of HashCodeUtilsMixin.hashcodes_from.
    '''
    return iter(self.mapping)

  def hashcodes_from(self, start_hashcode=None, reverse=False):
    ''' Use the mapping's .hashcodes_from if present, otherwise use HashCodeUtilsMixin.hashcodes_from.
    '''
    try:
      hashcodes_method = self.mapping.hashcodes_from
    except AttributeError:
      return HashCodeUtilsMixin.hashcodes_from(self, start_hashcode=start_hashcode, reverse=reverse)
    return hashcodes_method(start_hashcode=start_hashcode, reverse=reverse)

class ProxyStore(BasicStoreSync):
  ''' A Store managing various subsidiary Stores.

      Two classes of Stores are managed:

      Save stores. All data added to the Proxy is added to these Stores.

      Read Stores. Requested data may be obtained from these Stores.

      A typical setup utilising a working ProxyStore might look like this:

        FileCacheStore(
          ProxyStore(
            save=local,upstream
            read=local
            read2=upstream
          ),
          cache_dir
        )

      where "local" is a local low latency store such as a DataDirStore
      and "upstream" is a remote high latency Store such as a
      TCPStore. This setup causes all saved data to be saved to
      both Stores and data is fetched from the local Store in
      preference to the remote Store. A FileCacheStore is placed
      in front of the proxy to provide very low latency saves and
      very low latency reads if data are in the cache.
  '''

  def __init__(self, name, save, read, read2=()):
    ''' Initialise a ProxyStore.
        `name`: ProxyStore name.
        `save`: iterable of Stores to which to save blocks
        `read`: iterable of Stores from which to fetch blocks
        `read2`: optional fallback iterable of Stores from which
          to fetch blocks if not found via `read`. Typically these
          would be higher latency upstream Stores.
    '''
    BasicStoreSync.__init__(self, name)
    self.save = frozenset(save)
    self.read = frozenset(read)
    self.read2 = frozenset(read2)
    self._attrs.update(save=save, read=read)
    if read2:
      self._attrs.update(read2=read2)
    self.readonly = len(self.save) == 0

  def startup(self):
    for S in self.save | self.read | self.read2:
      S.open()

  def shutdown(self):
    for S in self.save | self.read | self.read2:
      S.close()

  def _multicall(self, stores, method_name, args):
    ''' Generator yielding (S, value) for each call to S.method_name(args).
        The method_name should be one of the *_bg names which return
        LateFunctions.
        Methods are called in parallel and values returned as
        completed, so the (S, value) returns may not be in the same
        order as the supplied `stores`.
        `stores`: iterable of Stores on which to call `method_name`
        `method_name`: name of Store method
        `args`: positional arguments for the method call
    '''
    assert method_name.endswith('_bg')
    stores = list(stores)
    LFs = []
    for S in stores:
      with Pfx("%s.%s()", S, method_name):
        LF = getattr(S, method_name)(*args)
        LFs.append(LF)
    for LF in report(LFs):
      # locate the corresponding store for context
      S = None
      for i, iLF in enumerate(LFs):
        if iLF is LF:
          S = stores[i]
          break
      if S is None:
        raise RuntimeError("LF %r not one of the original LFs: %r" % (LF, LFs))
      with Pfx(S):
        yield S, LF()

  def add(self, data):
    ''' Add a data chunk to the save Stores.
    '''
    if not self.save:
      hashcode = self.hash(data)
      if hashcode in self:
        return hashcode
      raise RuntimeError("add but no save Stores")
    hashcode = None
    for S, result in self._multicall(self.save, 'add_bg', (data,)):
      if result is None:
        raise RuntimeError("None returned from %s.add" % (S,))
      if hashcode is None:
        hashcode = result
      elif result != hashcode:
        warning("%s: different hashcodes returns from .add: %s vs %s", S, hashcode, result)
    if hashcode is None:
      raise RuntimeError("no hashcodes returned from .add")
    return hashcode

  def get(self, h):
    ''' Fetch a block from the first Store which has it.
    '''
    for stores in self.read, self.read2:
      for S, data in self._multicall(stores, 'get_bg', (h,)):
        if data is not None:
          # save the fetched data into the other save Stores
          def fill():
            for fillS in self.save:
              if fillS is not S:
                fillS.add_bg(data)
          self._defer(fill)
          return data

  def contains(self, h):
    ''' Test whether the hashcode `h` is in any of the read Stores.
    '''
    for stores in self.read, self.read2:
      for result in self._multicall(stores, 'contains_bg', (h,)):
        if result:
          return True
    return False

  def flush(self):
    ''' Flush all the save Stores.
    '''
    for result in self._multicall(self.save, 'flush_bg', ()):
      pass

class DataDirStore(MappingStore):
  ''' A MappingStore using a DataDir as its backend.
  '''

  def __init__(self, name, statedirpath, datadirpath=None, hashclass=None, indexclass=None, rollover=None, **kw):
    datadir = DataDir(statedirpath, datadirpath, hashclass, indexclass=indexclass, rollover=rollover)
    MappingStore.__init__(self, name, datadir, **kw)
    self._datadir = datadir

  def startup(self, **kw):
    self._datadir.open()
    super().startup(**kw)

  def shutdown(self):
    super().shutdown()
    self._datadir.close()

  def get_Archive(self, archive_name=None):
    return self._datadir.get_Archive(archive_name)

def PlatonicStore(name, statedirpath, *a, meta_store=None, **kw):
  ''' Factory function for platonic Stores.
      This is needed because if a meta_store is specified then it
      must be included as a block source in addition to the core
      platonic Store.
  '''
  if meta_store is None:
    return _PlatonicStore(name, statedirpath, *a, **kw)
  PS = _PlatonicStore(name, statedirpath, *a, meta_store=meta_store, **kw)
  S = ProxyStore(
      name,
      save=(),
      read=(PS, meta_store)
  )
  S.get_Archive = PS.get_Archive
  return S

class _PlatonicStore(MappingStore):
  ''' A MappingStore using a PlatonicDir as its backend.
  '''

  def __init__(
      self, name, statedirpath,
      *,
      datadirpath=None, hashclass=None, indexclass=None,
      follow_symlinks=False, archive=None, meta_store=None,
      flag_prefix=None,
      **kw
  ):
    datadir = PlatonicDir(
        statedirpath, datadirpath, hashclass, indexclass,
        follow_symlinks=follow_symlinks,
        archive=archive, meta_store=meta_store,
        flag_prefix=flag_prefix)
    MappingStore.__init__(self, name, datadir, **kw)
    self._datadir = datadir
    self.readonly = True

  def startup(self, **kw):
    self._datadir.open()
    super().startup(**kw)

  def shutdown(self):
    super().shutdown()
    self._datadir.close()

  def get_Archive(self, archive_name=None):
    return self._datadir.get_Archive(archive_name)

class _ProgressStoreTemplateMapping(object):

  def __init__(self, PS):
    self.PS = PS

  def __getitem__(self, key):
    try:
      category, aspect = key.rsplit('_', 1)
    except ValueError:
      category = key
      aspect = 'position'
    P = self.PS._progress[category]
    try:
      value = getattr(P, aspect)
    except AttributeError as e:
      raise KeyError("%s: aspect=%r" % (key, aspect))
    return value

class ProgressStore(BasicStoreSync):

  def __init__(self, name, S, template='rq  {requests_position}  {requests_throughput}/s', **kw):
    ''' Wrapper for a Store which collects statistics on use.
    '''
    lock = kw.pop('lock', None)
    if lock is None:
      lock = S._lock
    BasicStoreAsync.__init__(self, "ProgressStore(%s)" % (name,), lock=lock, **kw)
    self.S = S
    self.template = template
    self.template_mapping = _ProgressStoreTemplateMapping(self)
    Ps = {}
    for category in 'requests', \
                    'adds', 'gets', 'contains', 'flushes', \
                    'bytes_stored', 'bytes_fetched':
      Ps[category] = Progress(name='-'.join((str(S), category)), throughput_window=4)
    self._progress = Ps

  def __str__(self):
    return self.status_text()

  def startup(self):
    super().startup()
    self.S.open()

  def shutdown(self):
    self.S.close()
    super().shutdown()

  def status_text(self, template=None):
    ''' Return a status text utilising the progress statistics.
    '''
    if template is None:
      template = self.template
    return template.format_map(self.template_mapping)

  def add(self, data):
    progress = self._progress
    progress['requests'] += 1
    size = len(data)
    LF = self.S.add_bg(data)
    del data
    progress['adds'] += 1
    progress['bytes_stored'] += size
    return LF()

  def get(self, h):
    progress = self._progress
    progress['requests'] += 1
    LF = self.S.get_bg(h)
    progress['gets'] += 1
    data = LF()
    progress['bytes_fetched'] += len(data)
    return data

  def contains(self, h):
    progress = self._progress
    progress['requests'] += 1
    LF = self.S.contains_bg(h)
    progress['contains'] += 1
    return LF()

  def flush(self):
    progress = self._progress
    progress['requests'] += 1
    LF = self.S.flush_bg()
    progress['flushes'] += 1
    return LF()

  @property
  def requests(self):
    return self._progress['requests'].position

if __name__ == '__main__':
  from .store_tests import selftest
  selftest(sys.argv)
