#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Basic Store classes.
'''

from __future__ import with_statement
from abc import ABC, abstractmethod
from functools import partial
from os.path import expanduser, isabs as isabspath
import sys
from threading import Lock, Semaphore
from cs.later import Later
from cs.logutils import debug, warning, error
from cs.pfx import Pfx, XP
from cs.progress import Progress
from cs.py.func import prop, funccite
from cs.py.stack import caller
from cs.queues import Channel, IterableQueue
from cs.resources import MultiOpenMixin, RunStateMixin
from cs.result import report
from cs.seq import Seq
from cs.threads import bg
from cs.x import X
from . import defaults
from .datadir import DataDir, PlatonicDir
from .hash import DEFAULT_HASHCLASS, HashCodeUtilsMixin, MissingHashcodeError

class StoreError(Exception):
  ''' Raised by Store operation failures.
  '''
  pass

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
    with Pfx("_BasicStoreCommon.__init__(%s,..)", name):
      if not isinstance(name, str):
        raise TypeError(
            "initial `name` argument must be a str, got %s"
            % (type(name),))
      if name is None:
        name = "%s%d" % (self.__class__.__name__, next(_BasicStoreCommon._seq()))
      if capacity is None:
        capacity = 4
      if hashclass is None:
        hashclass = DEFAULT_HASHCLASS
      if runstate is None:
        runstate = defaults.runstate
      MultiOpenMixin.__init__(self, lock=lock)
      RunStateMixin.__init__(self, runstate=runstate)
      self._attrs = {}
      self.name = name
      self.hashclass = hashclass
      self.config = None
      self.logfp = None
      self.mountdir = None
      self.readonly = False
      self.writeonly = False
      self._archives = {}
      self._blockmapdir = None
      self.__funcQ = Later(capacity, name="%s:Later(__funcQ)" % (self.name,)).open()

  def __str__(self):
    ##return "STORE(%s:%s)" % (type(self), self.name)
    params = []
    for attr, val in sorted(self._attrs.items()):
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

  def hash(self, data):
    ''' Return a Hash object from data bytes.
        NB: this does _not_ store the data.
    '''
    return self.hashclass.from_chunk(data)

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

  def __iter__(self):
    return self.hashcodes_from()

  def keys(self):
    ''' Return an iterator over the Store's hashcodes.
    '''
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
    ''' Startup: does nothing.
    '''
    # Later already open
    pass

  def shutdown(self):
    ''' Called by final MultiOpenMixin.close().
    '''
    self.__funcQ.close()
    if not self.__funcQ.closed:
      debug("%s.shutdown: __funcQ not closed yet", self)
    self.__funcQ.wait()

  #############################
  ## Function dispatch methods.
  ##

  def _defer(self, func, *args, **kwargs):
    ''' Defer a function via the internal Later queue.
    '''
    return self.__funcQ.defer(func, *args, **kwargs)

  def bg(self, func, *a, **kw):
    ''' Queue a function without consuming the queue capacity.

        This is intended for "control" functions which themselves
        do all their work through the Store's function queue, such
        as the .pushto method's worker.
    '''
    # keep the Store open
    func2name = "%s (from %s)" % (funccite(func), caller())
    self.open()
    def func2():
      ''' Inner function to call `func` and then close the Store.
      '''
      # use the Store as the context for actions
      with self:
        try:
          value = func(*a, **kw)
        finally:
          # release the Store from earlier
          self.close()
        return value
    func2.__name__ = func2name
    return self.__funcQ.bg(func2)

  ##########################################################################
  # Core Store methods, all abstract.
  @abstractmethod
  def add(self, data):
    ''' Add the `data` to the Store, return its hashcode.
    '''
    raise NotImplementedError()

  @abstractmethod
  def add_bg(self, data):
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
                      dirpath = S.localpathto(rdirpath)
          else:
            # TODO: generic handler for Store subpaths needed
            if not isabspath(dirpath):
              dirpath = expanduser(dirpath)
              if not isabspath(dirpath):
                dirpath = S.localpathto(dirpath)
      return dirpath

  @blockmapdir.setter
  def blockmapdir(self, dirpath):
    ''' Set the Blockmap directory path.
    '''
    self._blockmapdir = dirpath

  def pushto(
      self, S2,
      capacity=1024, block_progress=None, bytes_progress=None
  ):
    ''' Allocate a Queue for Blocks to push from this Store to another Store `S2`.
        Return (Q, T) where `Q` is the new Queue and `T` is the
        Thread processing the Queue.

        Parameters:
        * `S2`: the secondary Store to receive Blocks.
        * `capacity`: the Queue capacity, arbitrary default 1024.
        * `block_progress`: an optional Progress counting submitted and completed Blocks.
        * `bytes_progress`: an optional Progress counting submitted and completed data bytes.

        Once called, the caller can then .put Blocks onto the Queue.
        When finished, call Q.close() to indicate end of Blocks and
        T.join() to wait for the processing completion.
    '''
    if capacity < 1:
      raise ValueError("capacity must be >= 1, got: %r" % (capacity,))
    lock = Lock()
    sem = Semaphore(capacity)
    ##sem = Semaphore(1)
    if block_progress is None:
      added_block = lambda B: None
      did_block = lambda B: None
    else:
      def added_block(B):
        ''' Advance the Block total.
        '''
        with lock:
          block_progress.total += 1
      def did_block(B):
        ''' Advance the Block progress counter.
        '''
        with lock:
          block_progress.position += 1
    if bytes_progress is None:
      added_bytes = lambda B: None
      did_bytes = lambda B: None
    else:
      def added_bytes(B):
        ''' Advance the bytes total.
        '''
        with lock:
          bytes_progress.total += B.span
      def did_bytes(B):
        ''' Advance the bytes progress counter.
        '''
        with lock:
          bytes_progress.position += B.span
    def Xs(s):
      ''' Terse inline string debug output.
      '''
      print(s, file=sys.stderr, end='', flush=True)
    X("NEW PUSHTO %r ==> %r", self.name, S2.name)
    name = "%s.pushto(%s)" % (self.name, S2.name)
    with Pfx(name):
      Q = IterableQueue(capacity=capacity, name=name)
      S1 = self
      S1.open()
      S2.open()
      pending = set()
      def worker(name, Q, S1, S2, sem):
        ''' This is the worker function which pushes Blocks from
            the Queue to the second Store.
        '''
        X("START PUSHTO PROCESSING...")
        with Pfx("%s: worker", name):
          with S1:
            for B in Q:
              Xs("B")
              added_block(B)
              added_bytes(B)
              with Pfx("%s", B):
                try:
                  h = B.hashcode
                except AttributeError:
                  warning("not a hashcode Block, skipping")
                  did_block(B)
                  did_bytes(B)
                  continue
                def addblock(S1, S2, h, B):
                  ''' Add the Block `B` to `S2` if not present.
                  '''
                  Xs("?")
                  if S2.contains(h):
                    return False
                  try:
                    data = S1[h]
                  except KeyError as e:
                    error("missing %s[%s]: %s", S1.name, h, e)
                    return None
                  Xs("+")
                  S2.add(data)
                  return True
                Xs("{")
                sem.acquire()
                addR = S2.bg(addblock, S1, S2, h, B)
                Xs("<")
                with lock:
                  pending.add(addR)
                def after_add(addR):
                  ''' Forget that `addR` is pending.
                      This will be called after `addR` completes.
                  '''
                  Xs(">")
                  with lock:
                    pending.remove(addR)
                  did_block(B)
                  did_bytes(B)
                  Xs("}")
                  sem.release()
                addR.notify(after_add)
            X("PUSHTO: NO MORE BLOCKS")
            with lock:
              outstanding = list(pending)
            X("PUSHTO: %d outstanding, waiting...", len(outstanding))
            for R in outstanding:
              R.join()
          S2.close()
          S1.close()
        X("PUSHTO: PROCESSING THREAD COMPLETES")
      T = bg(partial(worker, name, Q, S1, S2, sem))
      return Q, T

class BasicStoreSync(_BasicStoreCommon):
  ''' Subclass of _BasicStoreCommon expecting synchronous operations
      and providing asynchronous hooks, dual of BasicStoreAsync.
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
  ''' Subclass of _BasicStoreCommon expecting asynchronous operations
      and providing synchronous hooks, dual of BasicStoreSync.
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
    ''' Use the mapping's .hashcodes_from if present, otherwise use
        HashCodeUtilsMixin.hashcodes_from.
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

      A example setup utilising a working ProxyStore might look like this:

        ProxyStore(
          save=[local,upstream],
          save2=[spool],
          read=[local],
          read2=[upstream],
        )

      In this example:
        "local" is a local low latency store such as a DataDirStore.
        "upstream" is a remote high latency Store such as a TCPStore.
        "spool" is a local scondary Store, probably a DataDirStore

      This setup causes all saved data to be saved to "local" and
      "upstream". If a save to "local" or "upstream" fails, for
      example if the upstream if offline, the save is repeated to
      the "spool", intended as a holding location for data needing
      a resave.

      Reads are attempted first from the "read" Stores, then from
      the "read2" Stores".

      TODO: implement save2 saves
      TODO: replay and purge the spool? probably better as a separate
      pushto operation ("vt despool spool_store upstream_store").
  '''

  def __init__(self, name, save, read, *, save2=(), read2=(), **kw):
    ''' Initialise a ProxyStore.
        `name`: ProxyStore name.
        `save`: iterable of Stores to which to save blocks
        `read`: iterable of Stores from which to fetch blocks
        `save2`: fallback Store for saves which fail
        `read2`: optional fallback iterable of Stores from which
          to fetch blocks if not found via `read`. Typically these
          would be higher latency upstream Stores.
    '''
    BasicStoreSync.__init__(self, name, **kw)
    self.save = frozenset(save)
    self.read = frozenset(read)
    self.save2 = frozenset(save2)
    self.read2 = frozenset(read2)
    self._attrs.update(save=save, read=read)
    if save2:
      self._attrs.update(save2=save2)
    if read2:
      self._attrs.update(read2=read2)
    self.readonly = len(self.save) == 0

  def startup(self):
    for S in self.save | self.read | self.save2 | self.read2:
      S.open()

  def shutdown(self):
    for S in self.save | self.read | self.save2 | self.read2:
      S.close()

  @staticmethod
  def _multicall0(stores, method_name, args):
    ''' Basic multicall of _bg methods yielding (LF, S) pairs in the order submitted.
    '''
    assert method_name.endswith('_bg')
    stores = list(stores)
    for S in stores:
      with Pfx("%s.%s()", S, method_name):
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

  def add(self, data):
    ''' Add a data chunk to the save Stores.
        This queues all the saves in the background and returns the
        hashcode received.
    '''
    ch = Channel()
    self.bg(self._bg_add, data, ch)
    hashcode = ch.get()
    if hashcode is None:
      raise RuntimeError("no hashcode returned from .add")
    return hashcode

  def _bg_add(self, data, ch):
    ''' Add a data chunk to the save Stores.

        `data`: the data to add
        `ch`: a channel for hashocde return
    '''
    X("BG QUEUE ADD %d bytes, ch=%s ...", len(data), ch)
    try:
      if not self.save:
        # no save - allow add if hashcode already present - dubious
        hashcode = self.hash(data)
        if hashcode in self:
          ch.put(hashcode)
          return
        raise RuntimeError("new add but no save Stores")
      hashcode1 = None
      ok = True
      fallback = None
      for S, hashcode, exc_info in self._multicall(self.save, 'add_bg', (data,)):
        if exc_info is None:
          assert hashcode is not None, "None from .add of %s" % (S,)
          if hashcode1 is None:
            ch.put(hashcode)
            hashcode1 = hashcode
          elif hashcode1 != hashcode:
            warning(
                "%s: different hashcodes returns from .add: %s vs %s",
                S, hashcode1, hashcode)
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
              fallback = list(self._multicall0(self.save2, 'add_bg', (data,)))
            else:
              error("no fallback Stores")
          continue
      if not ok:
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
            if hashcode1 is None:
              ch.put(hashcode)
              hashcode1 = hashcode
            elif hashcode1 != hashcode:
              warning(
                  "%s: different hashcodes returns from .add: %s vs %s",
                  S, hashcode1, hashcode)
        if failures:
          raise RuntimeError("exceptions saving to save2: %r" % (failures,))
    finally:
      # mark end of queue
      if hashcode1 is None:
        X("BG QUEUE ADD no hashcode1, put None")
        ch.put(None)
      self.close()
      ch.close()

  def get(self, h):
    ''' Fetch a block from the first Store which has it.
    '''
    for stores in self.read, self.read2:
      for S, data, exc_info in self._multicall(stores, 'get_bg', (h,)):
        if exc_info:
          error("exception fetching from %s: %s", S, exc_info)
        elif data is not None:
          X("ProxyStore.GET %s succeeds from %s: %d bytes", h, S.name, len(data))
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

class DataDirStore(MappingStore):
  ''' A MappingStore using a DataDir as its backend.
  '''

  def __init__(
      self,
      name,
      statedirpath, datadirpath=None,
      hashclass=None, indexclass=None,
      rollover=None, runstate=None, **kw
  ):
    datadir = DataDir(
        statedirpath, datadirpath,
        hashclass, indexclass=indexclass,
        rollover=rollover, runstate=runstate)
    MappingStore.__init__(self, name, datadir, runstate=runstate, **kw)
    self._datadir = datadir

  def startup(self, **kw):
    ''' Startup: open the internal DataDir.
    '''
    self._datadir.open()
    super().startup(**kw)

  def shutdown(self):
    ''' Shutdown: close the internal DataDir.
    '''
    super().shutdown()
    self._datadir.close()

  def get_Archive(self, archive_name=None):
    ''' DataDirStore Archives are stored in the internal DataDir.
    '''
    return self._datadir.get_Archive(archive_name)

  def localpathto(self, rpath):
    ''' Compute the full path from a relative path.
    '''
    return self._datadir.localpathto(rpath)

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
      runstate=None,
      **kw
  ):
    datadir = PlatonicDir(
        statedirpath, datadirpath, hashclass, indexclass,
        follow_symlinks=follow_symlinks,
        archive=archive, meta_store=meta_store,
        flag_prefix=flag_prefix,
        runstate=runstate,
    )
    MappingStore.__init__(self, name, datadir, runstate=runstate, **kw)
    self._datadir = datadir
    self.readonly = True

  def startup(self, **kw):
    ''' Startup: open the internal DataDir.
    '''
    self._datadir.open()
    super().startup(**kw)

  def shutdown(self):
    ''' Shutdown: close the internal DataDir.
    '''
    super().shutdown()
    self._datadir.close()

  def get_Archive(self, archive_name=None):
    ''' PlatonicStore Archive are stored in the internal DataDir.
    '''
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
      raise KeyError("%s: aspect=%r: %s" % (key, aspect, e))
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
