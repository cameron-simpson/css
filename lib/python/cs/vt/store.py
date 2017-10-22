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
from cs.result import report as reportLFs
from cs.fileutils import shortpath
from cs.later import Later
from cs.logutils import info, debug, warning, error
from cs.pfx import Pfx
from cs.progress import Progress
from cs.resources import MultiOpenMixin
from cs.seq import Seq
from cs.x import X
from . import defaults
from .datadir import DataDir
from .hash import DEFAULT_HASHCLASS, HashCodeUtilsMixin

class MissingHashcodeError(KeyError):
  ''' Subclass of KeyError raised when accessing a hashcode not present in the Store.
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
      self.__funcQ = Later(capacity, name="%s:Later(__funcQ)" % (self.name,)).open()
      self.readonly = False
      self.writeonly = False

  def __str__(self):
    params = [
        attr + '=' + str(val) for attr, val in sorted(self._attrs.items())
    ]
    return "%s:%s(%s)" % (
        self.__class__.__name__, self.hashclass.HASHNAME,
        ','.join([repr(self.name)] + params)
    )

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

  def missing(self, hashes):
    ''' Yield hashcodes that are not in the store from an iterable hash
        code list.
    '''
    for h in hashes:
      if h not in self:
        yield h

  @abstractmethod
  def add(self, data):
    pass

  @abstractmethod
  def add_bg(self, data):
    pass

  @abstractmethod
  def get(self, h):
    pass

  @abstractmethod
  def get_bg(self, h):
    pass

  @abstractmethod
  def contains(self, h):
    pass

  @abstractmethod
  def contains_bg(self, h):
    pass

  @abstractmethod
  def flush(self):
    pass

  @abstractmethod
  def flush_bg(self):
    pass

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

class ChainStore(BasicStoreSync):
  ''' A wrapper for a sequence of Stores.
  '''

  def __init__(self, name, stores, save_all=False, parallel=False):
    ''' Initialise a ChainStore.
        `name`: ChainStore name.
        `stores`: sequence of Stores
        `save_all`: add new data to all Stores, not just the first one
        `parallel`: run requests to the Stores in parallel instead of in sequence
    '''
    if not stores:
      raise ValueError("stores may not be empty: %r" %(stores,))
    BasicStoreSync.__init__(self, name)
    self._attrs.update(
        stores='[' + ','.join(str(S) for S in stores) + ']',
        parallel=parallel,
        save_all=save_all,
    )
    self.stores = stores
    self.save_all = save_all
    self.parallel = parallel

  def startup(self):
    for S in self.stores:
      S.open()

  def shutdown(self):
    for S in self.stores:
      S.close()

  def add(self, data):
    ''' Add a block to the first subStore, or to all if elf.save_all.
    '''
    first = True
    for result in self._multicall('add_bg', (data,),
                                  parallel=self.parallel and not self.save_all):
      if result is None:
        raise RuntimeError("None returned from .add")
      if first:
        hashcode = result
        if not self.save_all:
          break
        first = False
      elif result != hashcode:
        warning("different hashcodes returns from .add: %s vs %s", hashcode, result)
    return hashcode

  def get(self, h):
    ''' Fetch a block from the first Store which has it.
    '''
    for result in self._multicall('get_bg', (h,), parallel=False):
      if result is not None:
        return result

  def contains(self, h):
    ''' Is the hashcode `h` in any of the subStores?
    '''
    for result in self._multicall('contains_bg', (h,)):
      if result:
        return True
    return False

  def flush(self):
    ''' Flush all the subStores.
    '''
    for result in self._multicall('flush_bg', (h,)):
      pass

  def _multicall(self, method_name, args, parallel=None):
    ''' Generator yielding results of subcalls.
        `method_name`: name of method on subStore, should return a Result
        `args`: positional arguments for the method call
        `parallel`: controls whether the subStores' methods are
          called and waited for sequentially or in parallel; if
          unspecified or None defaults to `self.parallel`
    '''
    if parallel is None:
      parallel = self.parallel
    LFs = []
    for S in self.stores:
      with Pfx(S):
        LF = getattr(S, method_name)(*args)
        LFs.append( (S, LF) )
        if not parallel:
          # yield early, allowing caller to prevent further calls
          yield LF()
    for S, LF in reportLFs(LFs):
      with Pfx(S):
        result = LF()
        if self.parallel:
          yield result

class DataDirStore(MappingStore):
  ''' A MappingStore using a DataDir as its backend.
  '''

  def __init__(self, name, statedirpath, datadirpath=None, hashclass=None, indexclass=None, rollover=None, **kw):
    datadir = DataDir(statedirpath, datadirpath, hashclass, indexclass, rollover=rollover)
    MappingStore.__init__(self, name, datadir, **kw)
    self._datadir = datadir

  def startup(self, **kw):
    X("DataDirStore.startup: _datadir.open...")
    self._datadir.open()
    super().startup(**kw)

  def shutdown(self):
    super().shutdown()
    self._datadir.close()
    X("DataDirStore.shutdown: _datadir.close...")

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
