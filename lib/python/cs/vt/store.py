#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Various Store classes.
'''

from __future__ import with_statement
from abc import ABC, abstractmethod
from fnmatch import fnmatch
from functools import partial
from os.path import expanduser, isabs as isabspath
import sys
from threading import Semaphore
from icontract import require
from cs.excutils import logexc
from cs.later import Later, SubLater
from cs.logutils import warning, error, info
from cs.pfx import Pfx
from cs.progress import Progress
from cs.py.func import prop, funcname
from cs.queues import Channel, IterableQueue
from cs.resources import MultiOpenMixin, RunStateMixin, RunState
from cs.result import report, bg as bg_result
from cs.seq import Seq
from cs.threads import bg as bg_thread
from cs.x import X
from . import defaults, Lock, RLock
from .block import HashCodeBlock, _IndirectBlock, LiteralBlock
from .datadir import DataDir, PlatonicDir, init_datadir
from .hash import (
    HashCode,
    DEFAULT_HASHCLASS, HASHCLASS_BY_NAME,
    HashCodeUtilsMixin, MissingHashcodeError
)

class StoreError(Exception):
  ''' Raised by Store operation failures.
  '''

  def __init__(self, message, **kw):
    super().__init__(message)
    for k, v in kw.items():
      setattr(self, k, v)

  def __str__(self):
    s = repr(self)
    for k in dir(self):
      if k and k[0].isalpha() and k not in ('args', 'with_traceback'):
        s += ":%s=%r" % (k, getattr(self, k))
    return s

class _PerHashclassMapping:
  ''' A mapping which chooses an underlying mapping based on the type of the key.

      This supports Stores which mediate access to hashclass specific backends.
  '''

  @require(lambda default_hashclass: issubclass(default_hashclass, HashCode))
  def __init__(self, factory, default_hashclass, lock):
    self._lock = lock
    self.default_hashclass = default_hashclass
    self._mappings = {}
    self.factory = factory

  def open(self):
    ''' Stub open method to match close.
    '''
    pass

  def close(self):
    ''' Close the mapping by closing all the submappings.
    '''
    for mapping in self._mappings.values():
      mapping.close()

  def mapping_for_hashclass(self, hashclass):
    ''' Return the DataDir for the specified `hashclass`.
        This proxies to the internal mapping.
    '''
    mappings = self._mappings
    with self._lock:
      try:
        mapping = mappings[hashclass]
      except KeyError:
        mappings[hashclass] = mapping = self.factory(hashclass)
        mapping.open()
    return mapping

  def __getitem__(self, hashcode):
    return self.mapping_for_hashclass(type(hashcode))[hashcode]

  def __setitem__(self, hashcode, data):
    self.mapping_for_hashclass(type(hashcode))[hashcode] = data

  def __contains__(self, hashcode):
    return hashcode in self.mapping_for_hashclass(type(hashcode))

  def keys(self, hashclass=None):
    ''' Return an iterable of hashcodes of type `hashclass`.
        This proxies to the internal mapping.
    '''
    if hashclass is None:
      hashclass = self.default_hashclass
    return self.mapping_for_hashclass(hashclass).keys()

  def __iter__(self):
    return iter(self.keys())

  def get_Archive(self, name, **kw):
    ''' Return the `Archive` named `name`.
        This proxies to the internal mapping.
    '''
    return self.mapping_for_hashclass(self.default_hashclass).get_Archive(name, **kw)

  def pathto(self, rpath):
    ''' Return the full path for `rpath`.
        This proxies to the internal mapping.
    '''
    return self.mapping_for_hashclass(self.default_hashclass).pathto(rpath)

class _BasicStoreCommon(MultiOpenMixin, HashCodeUtilsMixin, RunStateMixin, ABC):
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

  def __init__(self, name, capacity=None, hashclass=None, lock=None, runstate=None):
    ''' Initialise the Store.

        Parameters:
        * `name`: a name for this Store;
          if None, a sequential name based on the Store class name
          is generated
        * `capacity`: a capacity for the internal Later queue, default 4
        * `hashclass`: the hash class to use for this Store,
          default: `DEFAULT_HASHCLASS`
        * `lock`: an optional lock for managing concurrency,
          if not supplied a new `threading.RLock` is allocated
        * `runstate`: a `cs.resources.RunState` for external control;
          if not supplied one is allocated
    '''
    with Pfx("_BasicStoreCommon.__init__(%s,..)", name):
      if not isinstance(name, str):
        raise TypeError(
            "initial `name` argument must be a str, got %s"
            % (type(name),))
      if name is None:
        name = "%s%d" % (type(self).__name__, next(_BasicStoreCommon._seq()))
      if hashclass is None:
        hashclass = DEFAULT_HASHCLASS
      elif isinstance(hashclass, str):
        hashclass = HASHCLASS_BY_NAME[hashclass]
      assert issubclass(hashclass, HashCode)
      if capacity is None:
        capacity = 4
      if runstate is None:
        runstate = RunState(name)
      MultiOpenMixin.__init__(self, lock=lock)
      RunStateMixin.__init__(self, runstate=runstate)
      self._str_attrs = {}
      self.name = name
      self._capacity = capacity
      self.hashclass = hashclass
      self.config = None
      self.logfp = None
      self.mountdir = None
      self.readonly = False
      self.writeonly = False
      self._archives = {}
      self._blockmapdir = None
      self.block_cache = None

  def __str__(self):
    ##return "STORE(%s:%s)" % (type(self), self.name)
    params = []
    for attr, val in sorted(self._str_attrs.items()):
      params.append(attr + '=' + str(val))
    return "%s:%s(%s)" % (
        self.__class__.__name__, self.hashclass.HASHNAME,
        ','.join([repr(self.name)] + params)
    )

  __repr__ = __str__

  __bool__ = lambda self: True

  # Basic support for putting Stores in sets.
  def __hash__(self):
    return id(self)

  def hash(self, data, hashclass=None):
    ''' Return a HashCode instance from data bytes.
        NB: this does _not_ store the data.
    '''
    if hashclass is None:
      hashclass = self.hashclass
    return hashclass.from_chunk(data)

  # Stores are equal only to themselves.
  def __eq__(self, other):
    return self is other

  ###################
  ## Mapping methods.
  ##

  def __contains__(self, h):
    ''' Test if the supplied hashcode is present in the store.
    '''
    return self.contains(h)

  def keys(self, hashclass=None):
    ''' Return an iterator over the Store's hashcodes.
    '''
    return self.hashcodes_from(hashclass=hashclass)

  def __iter__(self):
    ''' Return an iterator over the Store's hashcodes.
    '''
    return self.keys()

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
        and raises `ValueError` if that does not match the supplied
        `h`.
    '''
    h2 = self.add(data, type(h))
    if h != h2:
      raise ValueError("h:%s != hash(data):%s" % (h, h2))

  ###########################
  ## Context manager methods.
  ##

  def __enter__(self):
    defaults.pushStore(self)
    return MultiOpenMixin.__enter__(self)

  def __exit__(self, exc_type, exc_value, traceback):
    if exc_value:
      import traceback as TB
      TB.print_tb(traceback, file=sys.stderr)
    defaults.popStore()
    return MultiOpenMixin.__exit__(self, exc_type, exc_value, traceback)

  ##########################
  ## MultiOpenMixin methods.
  ##

  def startup(self):
    ''' Start the Store.
    '''
    self.runstate.start()
    self.__funcQ = Later(self._capacity, name="%s:Later(__funcQ)" % (self.name,))
    self._worker = SubLater(self.__funcQ)
    self._reaper = self._worker.reaper()

  def shutdown(self):
    ''' Called by final MultiOpenMixin.close().
    '''
    self.runstate.cancel()
    self._worker.close()
    self._reaper.join()
    L = self.__funcQ
    L.shutdown()
    L.wait()
    del self.__funcQ
    self.runstate.stop()

  #############################
  ## Function dispatch methods.
  ##

  def _defer(self, func, *args, **kwargs):
    ''' Defer a function via the internal Later queue.
    '''
    self.open()
    def deferred():
      with self:
        result = func(*args, **kwargs)
      return result
    deferred.__name__ = "deferred:" + funcname(func)
    LF = self._worker.defer(deferred)
    LF.notify(lambda LF: self.close())
    return LF

  ##########################################################################
  # Core Store methods, all abstract.
  @abstractmethod
  def add(self, data, hashclass=None):
    ''' Add the `data` to the Store, return its hashcode.
    '''
    raise NotImplementedError()

  @abstractmethod
  def add_bg(self, data, hashclass=None):
    ''' Dispatch the add request in the backgrounmd, return Result.
    '''
    raise NotImplementedError()

  @abstractmethod
  def get(self, h):
    ''' Fetch the data for hashcode `h` from the Store, or None.
    '''
    raise NotImplementedError()

  @abstractmethod
  def get_bg(self, h):
    ''' Dispatch the get request in the backgrounmd, return Result.
    '''
    raise NotImplementedError()

  @abstractmethod
  def contains(self, h):
    ''' Test whether the hashcode `h` is present in the Store.
    '''
    raise NotImplementedError()

  @abstractmethod
  def contains_bg(self, h):
    ''' Dispatch the contains request in the backgrounmd, return Result.
    '''
    raise NotImplementedError()

  @abstractmethod
  def flush(self):
    ''' Flush outstanding tasks to the next lowest abstraction.
    '''
    raise NotImplementedError()

  @abstractmethod
  def flush_bg(self):
    ''' Dispatch the flush request in the backgrounmd, return Result.
    '''
    raise NotImplementedError()

  ##########################################################################
  # Archive support.
  def get_Archive(self, archive_name, missing_ok=False):
    ''' Fetch the named Archive or `None`.
    '''
    warning("no get_Archive for %s", type(self).__name__)
    return None

  ##########################################################################
  # Blockmaps.
  @prop
  def blockmapdir(self):
    ''' The path to this Store's blockmap directory, if specified.
    '''
    with Pfx("%s.blockmapdir", self):
      dirpath = self._blockmapdir
      if dirpath is None:
        cfg = self.config
        dirpath = cfg.get_default('blockmapdir')
        if dirpath is not None:
          if dirpath.startswith('['):
            endpos = dirpath.find(']', 1)
            if endpos < 0:
              # TODO: "GLOBAL" ???
              warning('[GLOBAL].blockmapdir: starts with "[" but no "]": %r', dirpath)
            else:
              clausename = dirpath[1:endpos].strip()
              with Pfx('[%s]', clausename):
                if not clausename:
                  warning('[GLOBAL].blockmapdir: empty clause name: %r', dirpath)
                else:
                  try:
                    S = cfg[clausename]
                  except KeyError:
                    warning("unknown config clause")
                  else:
                    rdirpathpos = endpos + 1
                    if rdirpathpos == len(dirpath):
                      rdirpath = 'blockmaps'
                    elif dirpath.startswith('/', rdirpathpos):
                      rdirpath = dirpath[rdirpathpos+1:]
                      if not rdirpath:
                        rdirpath = 'blockmaps'
                    else:
                      warning(
                          '[GLOBAL].blockmapdir: %r not followed with a slash: %r',
                          dirpath[:endpos+1], dirpath)
                      rdirpath = None
                    if rdirpath:
                      dirpath = S.pathto(rdirpath)
          else:
            # TODO: generic handler for Store subpaths needed
            if not isabspath(dirpath):
              dirpath = expanduser(dirpath)
              if not isabspath(dirpath):
                dirpath = S.pathto(dirpath)
      return dirpath

  @blockmapdir.setter
  def blockmapdir(self, dirpath):
    ''' Set the Blockmap directory path.
    '''
    self._blockmapdir = dirpath

  @require(lambda capacity: capacity >= 1)
  def pushto(
      self, dstS,
      *,
      capacity=64, hashclass=None, progress=None
  ):
    ''' Allocate a Queue for Blocks to push from this Store to another Store `dstS`.
        Return `(Q,T)` where `Q` is the new Queue and `T` is the
        Thread processing the Queue.

        Parameters:
        * `dstS`: the secondary Store to receive Blocks.
        * `capacity`: the Queue capacity, arbitrary default 1024.
        * `progress`: an optional Progress counting submitted and completed data bytes.

        Once called, the caller can then .put Blocks onto the Queue.
        When finished, call Q.close() to indicate end of Blocks and
        T.join() to wait for the processing completion.
    '''
    sem = Semaphore(capacity)
    ##sem = Semaphore(1)
    name = "%s.pushto(%s)" % (self.name, dstS.name)
    with Pfx(name):
      Q = IterableQueue(capacity=capacity, name=name)
      srcS = self
      srcS.open()
      dstS.open()
      T = bg_thread(
          lambda: (
              self.push_blocks(name, Q, srcS, dstS, sem, progress),
              srcS.close(), dstS.close()
          ))
      return Q, T

  @staticmethod
  def push_blocks(name, blocks, srcS, dstS, sem, progress):
    ''' This is a worker function which pushes Blocks or bytes from
        the supplied iterable `blocks` to the second Store.

        Parameters:
        * `name`: name for this worker instance
        * `blocks`: an iterable of Blocks or byte-like objects;
          each item may also be a tuple of (block-or-bytes, length)
          in which case the supplied length will be used for progress reporting
          instead of the 
    '''
    with Pfx("%s: worker", name):
      lock = Lock()
      with srcS:
        pending_blocks = {}   # mapping of Result to Block
        for block in blocks:
          if type(block) is tuple:
            try:
              block1, length = block
            except TypeError as e:
              error("cannot unpack %s into Block and length: %s", type(block), e)
              continue
            else:
              block = block1
          else:
            length = None
          sem.acquire()
          # worker function to add a block conditionally
          @logexc
          def add_block(srcS, dstS, block, length, progress):
            # add block content if not already present in dstS
            try:
              h = block.hashcode
            except AttributeError:
              # presume bytes-like or unStored Block type
              try:
                h = srcS.hash(block)
              except TypeError:
                warning("ignore object of type %s", type(block))
                return
              if h not in dstS:
                dstS[h] = block
            else:
              # get the hashcode, only get the data if required
              h = block.hashcode
              if h not in dstS:
                dstS[h] = block.get_direct_data()
            if progress:
              if length is None:
                length = len(block)
              progress += length
          addR = bg_result(add_block, srcS, dstS, block, length, progress)
          with lock:
            pending_blocks[addR] = block
          # cleanup function
          @logexc
          def after_add_block(addR):
            ''' Forget that `addR` is pending.
                This will be called after `addR` completes.
            '''
            with lock:
              B = pending_blocks.pop(addR)
            sem.release()
          addR.notify(after_add_block)
        with lock:
          outstanding = list(pending_blocks.keys())
        if outstanding:
          info("PUSHQ: %d outstanding, waiting...", len(outstanding))
          for R in outstanding:
            R.join()

class BasicStoreSync(_BasicStoreCommon):
  ''' Subclass of _BasicStoreCommon expecting synchronous operations
      and providing asynchronous hooks, dual of BasicStoreAsync.
  '''

  #####################################
  ## Background versions of operations.
  ##

  def add_bg(self, data, hashclass=None):
    return self._defer(self.add, data, hashclass)

  def get_bg(self, h):
    return self._defer(self.get, h)

  def contains_bg(self, h):
    return self._defer(self.contains, h)

  def flush_bg(self):
    return self._defer(self.flush)

class BasicStoreAsync(_BasicStoreCommon):
  ''' Subclass of _BasicStoreCommon expecting asynchronous operations
      and providing synchronous hooks, dual of BasicStoreSync.
  '''

  #####################################
  ## Background versions of operations.
  ##

  def add(self, data, hashclass=None):
    return self.add_bg(data, hashclass)()

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
    self._str_attrs.update(mapping=type(mapping).__name__)

  def startup(self):
    super().startup()
    mapping = self.mapping
    try:
      openmap = mapping.open
    except AttributeError:
      pass
    else:
      openmap()

  def shutdown(self):
    mapping = self.mapping
    try:
      closemap = mapping.close
    except AttributeError:
      pass
    else:
      closemap()
    super().shutdown()

  def init(self):
    ''' Mapping stores need no static setup.
    '''
    pass

  def add(self, data, hashclass=None):
    with Pfx("add %d bytes", len(data)):
      mapping = self.mapping
      h = self.hash(data, hashclass)
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

  __contains__ = contains

  def flush(self):
    ''' Call the .flush method of the underlying mapping, if any.
    '''
    map_flush = getattr(self.mapping, 'flush', None)
    if map_flush is not None:
      map_flush()

  def __len__(self):
    return len(self.mapping)

  def keys(self, hashclass=None):
    if hashclass is None:
      hashclass = self.hashclass
    keys_func = self.mapping.keys
    try:
      return keys_func(hashclass)
    except TypeError:
      # get all keys and filter by type
      return ( h for h in keys_func() if type(h) is hashclass )

  def __iter__(self):
    ''' Return iterator over the mapping; required for use of HashCodeUtilsMixin.hashcodes_from.
    '''
    return iter(self.mapping.keys())

  def hashcodes_from(self, **kw):
    ''' Use the mapping's .hashcodes_from if present, otherwise use
        HashCodeUtilsMixin.hashcodes_from.
    '''
    try:
      hashcodes_method = self.mapping.hashcodes_from
    except AttributeError:
      return HashCodeUtilsMixin.hashcodes_from(self, **kw)
    return hashcodes_method(**kw)

class ProxyStore(BasicStoreSync):
  ''' A Store managing various subsidiary Stores.

      Three classes of Stores are managed:

      Save stores. All data added to the Proxy is added to these Stores.

      Read Stores. Requested data may be obtained from these Stores.

      Copy Stores. Data retrieved from a `read2` Store is copied to these Stores.

      A example setup utilising a working ProxyStore might look like this:

          ProxyStore(
            save=[local,upstream],
            save2=[spool],
            read=[local],
            read2=[upstream],
            copy2=[local],
          )

      In this example:
      * `local`: is a local low latency store such as a DataDirStore.
      * `upstream`: is a remote high latency Store such as a TCPStore.
      * `spool`: is a local scondary Store, probably a DataDirStore.

      This setup causes all saved data to be saved to `local` and
      `upstream`.
      If a save to `local` or `upstream` fails,
      for example if the upstream if offline,
      the save is repeated to the `spool`,
      intended as a holding location for data needing a resave.

      Reads are attempted first from the `read` Stores, then from
      the `read2` Stores.
      If there are any `copy2` Stores,
      any data obtained from `read2` are copied into the `copy2` Stores;
      in this way remote data become locally saved.

      Archives may be made available with the `archives` parameter.
      This is an iterable of `(glob,Store)`.
      This supports obtaining an Archive by name
      from the first Store whose glob matches the name.

      TODO: replay and purge the spool? probably better as a separate
      pushto operation ("vt -S spool_store pushto upstream_store").
  '''

  def __init__(
      self, name,
      save, read,
      *,
      save2=(), read2=(), copy2=(), archives=(),
      **kw
  ):
    ''' Initialise a ProxyStore.

        Parameters:
        * `name`: ProxyStore name.
        * `save`: iterable of Stores to which to save blocks
        * `read`: iterable of Stores from which to fetch blocks
        * `save2`: fallback Store for saves which fail
        * `read2`: optional fallback iterable of Stores from which
          to fetch blocks if not found via `read`. Typically these
          would be higher latency upstream Stores.
        * `copy2`: optional iterable of Stores to receive copies
          of data obtained via `read2` Stores.
        * `archives`: search path for archive names
    '''
    BasicStoreSync.__init__(self, name, **kw)
    self.save = frozenset(save)
    self.read = frozenset(read)
    self.save2 = frozenset(save2)
    self.read2 = frozenset(read2)
    self.copy2 = frozenset(copy2)
    self.archive_path = tuple(archives)
    for S, _ in self.archive_path:
      if not hasattr(S, 'get_Archive'):
        raise ValueError("%s: no get_Archive method" % (S,))
    self._str_attrs.update(save=save, read=read)
    if save2:
      self._str_attrs.update(save2=save2)
    if read2:
      self._str_attrs.update(read2=read2)
    if copy2:
      self._str_attrs.update(copy2=copy2)
    self.readonly = len(self.save) == 0

  def get_Archive(self, name, missing_ok=False):
    ''' Obtain the named Archive from a Store in the archives list.
    '''
    with Pfx("%s.get_Archive(%r)", self, name):
      for S, fnptn in self.archive_path:
        if fnmatch(name, fnptn):
          info(
              "%s.get_Archive(%r): matched %r, fetching from %r",
              self.name, name, fnptn, S.name)
          return S.get_Archive(name, missing_ok=missing_ok)
      raise KeyError("no such Archive")

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, self.name)

  def init(self):
    ''' Init the subsidiary Stores.
    '''
    for S in self.save | self.read | self.save2 | self.read2 | self.copy2:
      S.init()

  def startup(self):
    super().startup()
    for S in self.save | self.read | self.save2 | self.read2 | self.copy2:
      S.open()
    for S, _ in self.archive_path:
      S.open()

  def shutdown(self):
    for S, _ in self.archive_path:
      S.close()
    for S in self.save | self.read | self.save2 | self.read2 | self.copy2:
      S.close()
    super().shutdown()

  @staticmethod
  def _multicall0(stores, method_name, args):
    ''' Basic multicall of _bg methods yielding (LF, S) pairs in the order submitted.
    '''
    assert method_name.endswith('_bg')
    stores = list(stores)
    for S in stores:
      with Pfx("%s.%s()", S, method_name):
        with S:
          LF = getattr(S, method_name)(*args)
      yield LF, S   # outside Pfx because this is a generator

  def _multicall(self, stores, method_name, args):
    ''' Generator yielding (S, result, exc_info) for each call to
        S.method_name(args) in the order completed.

        The method_name should be one of the *_bg names which return
        LateFunctions.
        Methods are called in parallel and values returned as
        completed, so the (S, LateFUnction) returns may not be in the same
        order as the supplied `stores`.
        `stores`: iterable of Stores on which to call `method_name`
        `method_name`: name of Store method
        `args`: positional arguments for the method call
    '''
    LFmap = dict(self._multicall0(stores, method_name, args))
    for LF in report(LFmap.keys()):
      # locate the corresponding store for context
      S = LFmap[LF]
      yield S, LF.result, LF.exc_info

  def add(self, data, hashclass=None):
    ''' Add a data chunk to the save Stores.
        This queues all the saves in the background and returns the
        hashcode received.
    '''
    ch = Channel()
    self._defer(self._bg_add, data, hashclass, ch)
    hashcode = ch.get()
    if hashcode is None:
      raise RuntimeError("no hashcode returned from .add")
    return hashcode

  def _bg_add(self, data, hashclass, ch):
    ''' Add a data chunk to the save Stores.

        Parameters:
        * `data`: the data to add
        * `hashclass`: the hashclass with which to index the data,
          default: `None`, meaning `self.hashclass`
        * `ch`: a channel for hashcode return
    '''
    try:
      if not self.save:
        # no save - allow add if hashcode already present - dubious
        hashcode = self.hash(data, hashclass)
        if hashcode in self:
          ch.put(hashcode)
          ch = None
          return
        raise RuntimeError("new add but no save Stores")
      ok = True
      fallback = None
      for S, hashcode, exc_info in self._multicall(self.save, 'add_bg', (data, hashclass)):
        if exc_info is None:
          assert hashcode is not None, "None from .add of %s" % (S,)
          if ch:
            ch.put(hashcode)
            ch = None
        else:
          e = exc_info[1]
          if isinstance(e, StoreError):
            exc_info = None
          X("================ exc_info=%r", exc_info)
          error("exception from %s.add: %s", S, e, exc_info=exc_info)
          if ok:
            ok = False
            if self.save2:
              # kick off the fallback saves immediately
              X("_BG_ADD: dispatch fallback %r", self.save2)
              fallback = list(self._multicall0(self.save2, 'add_bg', (data, hashclass)))
            else:
              error("no fallback Stores")
          continue
      if not ok:
        if fallback:
          failures = []
          for LF, S in fallback:
            hashcode, exc_info = LF.join()
            if exc_info:
              e = exc_info[1]
              if isinstance(e, StoreError):
                exc_info = None
              X("==== ==== ==== exc_info=%r", exc_info)
              error("exception saving to %s: %s", S, exc_info[1], exc_info=exc_info)
              failures.append( (S, e) )
            else:
              if ch:
                ch.put(hashcode)
                ch = None
          if failures:
            raise RuntimeError("exceptions saving to save2: %r" % (failures,))
    finally:
      # mark end of queue
      if ch:
        ch.put(None)
        ch = None
        ch.close()

  def get(self, h):
    ''' Fetch a block from the first Store which has it.
    '''
    with Pfx("%s.get", type(self).__name__):
      for stores in self.read, self.read2:
        for S, data, exc_info in self._multicall(stores, 'get_bg', (h,)):
          with Pfx("%s.get_bg(%s)", S, h):
            if exc_info:
              error("exception", exc_info=exc_info)
            elif data is not None:
              ##XP("got %d bytes", len(data))
              if S not in self.read:
                for copyS in self.copy2:
                  ##XP("copy to %s", copyS)
                  copyS.add_bg(data)
              return data
      return None

  def contains(self, h):
    ''' Test whether the hashcode `h` is in any of the read Stores.
    '''
    for stores in self.read, self.read2:
      for S, result, exc_info in self._multicall(stores, 'contains_bg', (h,)):
        if exc_info:
          error("exception fetching from %s: %s", S, exc_info)
        elif result:
          return True
    return False

  def flush(self):
    ''' Flush all the save Stores.
    '''
    for _ in self._multicall(self.save, 'flush_bg', ()):
      pass

  def keys(self, hashclass=None):
    if hashclass is None:
      hashclass = self.hashclass
    seen = set()
    Q = IterableQueue()
    def keys_from(S):
      for h in S.keys(hashclass):
        Q.put(h)
      Q.put(None)
    busy = 0
    for S in self.read:
      bg_thread(partial(keys_from, S))
      busy += 1
    for h in Q:
      if h is None:
        busy -= 1
        if not busy:
          Q.close()
      elif h not in seen:
        yield h
        seen.add(h)

class DataDirStore(MappingStore):
  ''' A MappingStore using a DataDir as its backend.
  '''

  def __init__(
      self,
      name,
      statedirpath,
      *,
      hashclass=None, indexclass=None,
      rollover=None,
      lock=None,
      **kw
  ):
    if lock is None:
      lock = RLock()
    self._lock = lock
    self.statedirpath = statedirpath
    if hashclass is None:
      hashclass = DEFAULT_HASHCLASS
    self.indexclass = indexclass
    self.rollover = rollover
    self._datadir = _PerHashclassMapping(
        lambda hcls: DataDir(
            self.statedirpath,
            hcls,
            indexclass=self.indexclass,
            rollover=self.rollover),
        hashclass,
        self._lock)
    MappingStore.__init__(self, name, self._datadir, hashclass=hashclass, **kw)

  def startup(self, **kw):
    ''' Startup: open the internal DataDir.
    '''
    super().startup(**kw)
    self._datadir.open()

  def shutdown(self):
    ''' Shutdown: close the internal DataDir.
    '''
    self._datadir.close()
    super().shutdown()

  def init(self):
    ''' Init the supporting data dir.
    '''
    init_datadir(self.statedirpath)

  def pathto(self, rpath):
    ''' Compute the full path from a relative path.
    '''
    return self._datadir.pathto(rpath)

  def get_Archive(self, name=None, missing_ok=False):
    ''' DataDirStore Archives are associated with the internal DataDir.
    '''
    return self._datadir.get_Archive(name, missing_ok=missing_ok)

def PlatonicStore(name, statedirpath, *a, meta_store=None, hashclass=None, **kw):
  ''' Factory function for platonic Stores.

      This is needed because if a meta_store is specified then it
      must be included as a block source in addition to the core
      platonic Store.
  '''
  if meta_store is None:
    return _PlatonicStore(name, statedirpath, *a, hashclass=hashclass, **kw)
  PS = _PlatonicStore(name, statedirpath, *a, meta_store=meta_store, hashclass=hashclass, **kw)
  S = ProxyStore(
      name,
      save=(),
      read=(PS, meta_store),
      hashclass=hashclass,
  )
  S.get_Archive = PS.get_Archive
  return S

class _PlatonicStore(MappingStore):
  ''' A MappingStore using a PlatonicDir as its backend.
  '''

  def __init__(
      self, name, statedirpath,
      *,
      hashclass=None,
      indexclass=None, follow_symlinks=False,
      archive=None, meta_store=None,
      flags_prefix=None,
      lock=None,
      **kw
  ):
    if lock is None:
      lock = RLock()
    self.lock = lock
    self.statedirpath = statedirpath
    if hashclass is None:
      hashclass = DEFAULT_HASHCLASS
    self._datadir = _PerHashclassMapping(
        lambda hcls: PlatonicDir(
            self.statedirpath, hcls,
            indexclass=indexclass, follow_symlinks=follow_symlinks,
            archive=archive, meta_store=meta_store,
            flags_prefix=flags_prefix,
            **kw,
        ),
        hashclass, lock)
    MappingStore.__init__(self, name, self._datadir, hashclass=hashclass, **kw)
    self.readonly = True

  def init(self):
    init_datadir(self.statedirpath)

  def startup(self, **kw):
    ''' Startup: open the internal DataDir.
    '''
    super().startup(**kw)
    self._datadir.open()

  def shutdown(self):
    ''' Shutdown: close the internal DataDir.
    '''
    self._datadir.close()
    super().shutdown()

  def get_Archive(self, name=None, missing_ok=False):
    ''' PlatonicStore Archives are associated with the internal DataDir.
    '''
    return self._datadir.get_Archive(name, missing_ok=missing_ok)

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
      raise KeyError("%s: aspect=%r: %s" % (key, aspect, e))
    return value

class ProgressStore(BasicStoreSync):
  ''' A shim for another Store to do progress reporting.
      TODO: planning to redo basic store methods as shims, with
      implementations supplying _foo methods across the board
      instead.
  '''

  def __init__(
      self,
      name, S,
      template='rq  {requests_position}  {requests_throughput}/s',
      **kw
  ):
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
    ''' The number of requests.
    '''
    return self._progress['requests'].position

if __name__ == '__main__':
  from .store_tests import selftest
  selftest(sys.argv)
