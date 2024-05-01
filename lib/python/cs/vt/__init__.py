#!/usr/bin/env python3

''' A content hash based data store with a filesystem layer, using
    variable sized blocks, arbitrarily sized data and utilising some
    domain knowledge to aid efficient block boundary selection.

    The package provides the `vt` command to access
    these facilities from the command line.

    This system has two main components:
    * `Store`s: storage areas of variable sized data blocks
      indexed by the cryptographic hashcode of their content
    * `Dirent`s: references to filesystem entities
      containing hashcode based references to the content

    These are logically disconnected.
    Dirents are not associated with particular `Store`s;
    it is sufficient to have access to any `Store`
    containing the required blocks.

    The other common entity is the `Archive`,
    which is just a text file containing
    a timestamped log of revisions of a `Dirent`.
    These can be mounted as a FUSE filesystem,
    and the `vt pack` command simply stores
    a directory tree into the current `Store`,
    and records the stored reference in an `Archive` file.

    See also the Plan 9 Venti system:
    (http://library.pantek.com/general/plan9.documents/venti/venti.html,
    http://en.wikipedia.org/wiki/Venti)
    which is also a system based on variable sized blocks.

    *Note*: the "mount" filesystem facility uses FUSE,
    which may need manual OS installation.
    On MacOS this means installing `osxfuse`,
    for example from MacPorts.
    You will also need the `llfuse` Python module,
    which is not automatically required by this package.
'''

from abc import ABC, abstractmethod
from collections.abc import MutableMapping
from contextlib import closing, contextmanager
import os
from threading import Thread
from types import SimpleNamespace as NS
from typing import Iterable, Tuple, Union

from icontract import require
from typeguard import typechecked

from cs.buffer import CornuCopyBuffer
from cs.context import stackattrs
from cs.deco import default_params, fmtdoc, promote
from cs.later import Later
from cs.lex import r
from cs.logutils import warning
from cs.progress import Progress
from cs.queues import IterableQueue, QueueIterator
from cs.pfx import Pfx, pfx_method
from cs.resources import MultiOpenMixin, RunState, RunStateMixin, uses_runstate
from cs.seq import Seq
from cs.threads import bg as bg_thread, ThreadState, HasThreadState
from cs.upd import Upd, uses_upd

from .hash import (
    DEFAULT_HASHCLASS,
    HASHCLASS_BY_NAME,
    HashCode,
    HashCodeUtilsMixin,
    MissingHashcodeError,
)

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 3 - Alpha",
        "Environment :: Console",
        "Programming Language :: Python :: 3",
        "Topic :: System :: Filesystems",
    ],
    'install_requires': [
        'cs.buffer',
        'cs.app.flag',
        'cs.binary',
        'cs.cache',
        'cs.cmdutils',
        'cs.context',
        'cs.debug',
        'cs.deco',
        'cs.excutils',
        'cs.fileutils',
        'cs.inttypes',
        'cs.later',
        'cs.lex',
        'cs.logutils',
        'cs.packetstream',
        'cs.pfx',
        'cs.progress',
        'cs.py.func',
        'cs.py.modules',
        'cs.py.stack',
        'cs.queues',
        'cs.range',
        'cs.resources',
        'cs.result',
        'cs.seq',
        'cs.socketutils',
        'cs.threads',
        'cs.testutils',
        'cs.tty',
        'cs.units',
        'cs.upd',
        'cs.x',
        'icontract',
        'lmdb',
    ],
    'entry_points': {
        'console_scripts': [
            'vt = cs.vt.__main__:main',
            'mount.vtfs = cs.vt.__main__:mount_vtfs',
        ],
    },
    'extras_requires': {
        'FUSE': ['llfuse'],
        'plotting': ['cs.mplutils'],
    },
}

DEFAULT_BASEDIR = '~/.local/share/vt'

DEFAULT_CONFIG_ENVVAR = 'VT_CONFIG'
DEFAULT_CONFIG_PATH = '~/.vtrc'
VT_STORE_ENVVAR = 'VT_STORE'
VT_STORE_DEFAULT = '[default]'
VT_CACHE_STORE_ENVVAR = 'VT_CACHE_STORE'
VT_CACHE_STORE_DEFAULT = '[cache]'
DEFAULT_HASHCLASS_ENVVAR = 'VT_HASHCLASS'

DEFAULT_CONFIG_MAP = {
    'GLOBAL': {
        'basedir': DEFAULT_BASEDIR,
    },
    'default': {
        'type': 'datadir',
        'path': 'trove',
    },
    'cache': {
        'type': 'memory',
        'max_data': '16 GiB',
    },
    'server': {
        'type': 'datadir',
        'path': 'trove',
        'address': '~/.vt.sock',
    },
}

if False:
  # intercept Lock and RLock
  from cs.threads import NRLock
  from .debug import DebuggingLock

  def RLock():
    ''' Obtain a recursive DebuggingLock.
    '''
    return DebuggingLock(recursive=True)

  def Lock():
    ''' Obtain a nonrecursive DebuggingLock.
    '''
    return DebuggingLock()

  # monkey patch MultiOpenMixin
  import cs.resources
  cs.resources.MultiOpenMixin._mom_state_lock = NRLock()
else:
  from threading import (
      Lock as threading_Lock,
      RLock as threading_RLock,
  )
  Lock = threading_Lock
  RLock = threading_RLock

# Default OS level file high water mark.
# This is used for rollover levels for DataDir files and cache files.
MAX_FILE_SIZE = 1024 * 1024 * 1024

# path separator, hardwired
PATHSEP = '/'

run_modes = NS(show_progress=True,)

class Store(MutableMapping, HasThreadState, MultiOpenMixin, HashCodeUtilsMixin,
            RunStateMixin, ABC):
  ''' Core functions provided by all Stores.

      Subclasses should not subclass this class but StoreSyncBase
      or StoreAsyncBase; these provide the *_bg or non-*_bg sibling
      methods of those described below so that a subclass need only
      implement the synchronous or asynchronous forms. Most local
      Stores will derive from StoreSyncBase and remote Stores
      derive from StoreAsyncBase.

      A subclass should provide thread-safe implementations of the following
      methods:

        .add(chunk) -> hashcode
        .get(hashcode, [default=None]) -> chunk (or default)
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

  perthread_state = ThreadState()

  @uses_runstate
  @pfx_method
  @fmtdoc
  def __init__(
      self,
      name,
      *,
      capacity=None,
      conv_cache=None,
      hashclass=None,
      runstate: RunState,
  ):
    ''' Initialise the Store.

        Parameters:
        * `name`: a name for this Store;
          if `None`, a sequential name based on the Store class name
          is generated
        * `capacity`: a capacity for the internal `Later` queue, default 4
        * `convcache`: optional `cs.cache.ConvCache` for persistent
          storage of certain cached values
        * `hashclass`: the hash class to use for this Store,
          default: `DEFAULT_HASHCLASS` (`{DEFAULT_HASHCLASS.__name__}`)
        * `runstate`: a `cs.resources.RunState` for external control;
          if not supplied one is allocated

        `conv_cache`: most `Store`s do not have one of these, but
        a `DatqDirStore` does, a `ProxyStore` returns the first
        conv cache from its read Stores and a `FileCacheStore`
        presents the conv cache of its backend.
    '''
    if not isinstance(name, str):
      raise TypeError(
          "initial `name` argument must be a str, got %s" % (type(name),)
      )
    if name is None:
      name = "%s%d" % (type(self).__name__, next(self._seq()))
    if hashclass is None:
      hashclass = DEFAULT_HASHCLASS
    elif isinstance(hashclass, str):
      hashclass = HASHCLASS_BY_NAME[hashclass]
    assert issubclass(hashclass, HashCode)
    if capacity is None:
      capacity = 4
    RunStateMixin.__init__(self, runstate=runstate)
    self._str_attrs = {}
    self.name = name
    self._capacity = capacity
    self.conv_cache = conv_cache
    self.later = None
    self.hashclass = hashclass
    self._config = None
    self.logfp = None
    self.mountdir = None
    self.readonly = False
    self.writeonly = False
    self._archives = {}
    self._blockmapdir = None
    self.block_cache = None

  def init(self):
    ''' Method provided to support "vt init".
        For stores requiring some physical setup,
        for example to create an empty `DataDir`,
        that code goes here.
    '''

  def __str__(self):
    ##return "STORE(%s:%s)" % (type(self), self.name)
    params = []
    for attr, val in sorted(getattr(self, '_str_attrs', {}).items()):
      if isinstance(val, type):
        val_s = '<%s.%s>' % (val.__module__, val.__name__)
      else:
        val_s = str(val)
      params.append(attr + '=' + val_s)
    return "%s:%s(%s)" % (
        self.__class__.__name__,
        getattr(getattr(self, 'hashclass', None), 'hashname', "no-hashclass"),
        ','.join([repr(getattr(self, 'name', 'no-name'))] + params),
    )

  __repr__ = __str__

  @staticmethod
  @fmtdoc
  def get_default_spec():
    ''' The default `Store` specification from `${VT_STORE_ENVVAR}`,
        default `{VT_STORE_DEFAULT!r}`.
    '''
    return os.environ.get(VT_STORE_ENVVAR, VT_STORE_DEFAULT)

  @staticmethod
  @fmtdoc
  def get_default_cache_spec():
    ''' The default cache `Store` specification from `${VT_CACHE_STORE_ENVVAR}`,
        default `{VT_CACHE_STORE_DEFAULT!r}`.
    '''
    return os.environ.get(VT_CACHE_STORE_ENVVAR, VT_CACHE_STORE_DEFAULT)

  @classmethod
  def default(cls, config_spec=None, store_spec=None, cache_spec=None):
    ''' Get the prevailing `Store` instance.
        This calls `HasThreadState.default()` first,
        but falls back to constrcting the default `Store` instance
        from `Store.get_default_spec` and `Store.get_default_cache_spec`.
        As such, the returns `Store` is not necessarily "open"
        and users should open it for use. Example:

            S = Store.default()
            with S:
                ... do stuff ...
    '''
    S = super().default()
    if S is None:
      # no prevailing Store
      # construct the default Store
      from .config import Config
      config = Config(config_spec)
      if store_spec is None:
        store_spec = cls.get_default_spec()
      if cache_spec is None:
        cache_spec = cls.get_default_cache_spec()
      S = cls.promote(store_spec, config)
      if cache_spec is not None and cache_spec not in ("", "NONE"):
        cacheS = cls.promote(cache_spec, config)
        S = ProxyStore(
            "%s:%s" % (cacheS.name, S.name),
            read=(cacheS,),
            read2=(S,),
            copy2=(cacheS,),
            save=(cacheS, S),
            archives=((S, '*'),),
        )
    return S

  __bool__ = lambda self: True

  # Basic support for putting Stores in sets.
  def __hash__(self):
    return id(self)

  def hash(self, data):
    ''' Return a `HashCode` instance from data bytes.
        NB: this does _not_ store the data.
    '''
    return self.hashclass.from_data(data)

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

  def keys(self):
    ''' Return an iterator over the Store's hashcodes.
    '''
    return self.hashcodes_from()

  def __iter__(self):
    ''' Return an iterator over the Store's hashcodes.
    '''
    return iter(self.keys())

  def __getitem__(self, h):
    ''' Return the data bytes associated with the supplied hashcode.
        Raise `MissingHashcodeError` (a subclass of `KeyError`)
        if the hashcode is not present.
    '''
    data = self.get(h)
    if data is None:
      raise MissingHashcodeError(h)
    return data

  def __setitem__(self, h, data):
    ''' Save `data` against hash key `h`.

        Actually saves the data against the Store's hash function
        and raises `ValueError` if that does not match the supplied
        `h`.
    '''
    if not isinstance(h, self.hashclass):
      raise TypeError(f'h should be a {self.hashclass}, got {r(h)}')
    if h != self.hashclass.from_bytes(data):
      raise ValueError(f'{h=} != {self.hashclass.hashname}({data=})')
    h2 = self.add(data)
    assert h == h2

  def __delitem__(self, h):
    raise NotImplementedError(f'{self.__class__.__name__}.__delitem__')

  ###################################################
  ## Context manager methods via ContextManagerMixin.
  ##
  def __enter_exit__(self):
    with MultiOpenMixin.as_contextmanager(self):
      with HasThreadState.as_contextmanager(self):
        yield

  ##########################
  ## MultiOpenMixin methods.
  ##

  @contextmanager
  def startup_shutdown(self):
    ''' `MultiOpenMixin.startup_shutdown` hook.
    '''
    with super().startup_shutdown():
      runstate = self.runstate
      L = Later(self._capacity, name=self.name)
      with L:
        with stackattrs(self, later=L):
          with runstate:
            try:
              yield
            finally:
              self.runstate.cancel()
      L.wait()

  #############################
  ## Function dispatch methods.
  ##

  def _defer(self, func, *args, **kwargs):
    ''' Defer a function via the internal `Later` queue.
        Hold an `open()` on `self` to avoid premature shutdown.
    '''
    self.open()

    def closing_self():
      with closing(self):
        return func(*args, **kwargs)

    return self.later.defer(func.__qualname__, closing_self)

  ##########################################################################
  # Core Store methods, all abstract.
  @abstractmethod
  def add(self, data):
    ''' Add the `data` to the Store, return its hashcode.
    '''
    raise NotImplementedError

  @abstractmethod
  def add_bg(self, data):
    ''' Dispatch the add request in the background, return a `Result`.
    '''
    raise NotImplementedError

  @abstractmethod
  # pylint: disable=unused-argument
  def get(self, h, default=None):
    ''' Fetch the data for hashcode `h` from the Store, or `None`.
    '''
    raise NotImplementedError

  @abstractmethod
  def get_bg(self, h):
    ''' Dispatch the get request in the background, return a `Result`.
    '''
    raise NotImplementedError

  @abstractmethod
  def contains(self, h):
    ''' Test whether the hashcode `h` is present in the Store.
    '''
    raise NotImplementedError

  @abstractmethod
  def contains_bg(self, h):
    ''' Dispatch the contains request in the background, return a `Result`.
    '''
    raise NotImplementedError

  @abstractmethod
  def flush(self):
    ''' Flush outstanding tasks to the next lowest abstraction.
    '''
    raise NotImplementedError

  @abstractmethod
  def flush_bg(self):
    ''' Dispatch the flush request in the background, return a `Result`.
    '''
    raise NotImplementedError

  ##########################################################################
  # Archive support.
  # pylint: disable=unused-argument
  def get_Archive(self, archive_name, missing_ok=False):
    ''' Fetch the named Archive or `None`.
    '''
    warning("no get_Archive for %s", type(self).__name__)
    return None  # pylint: disable=useless-return

  @property
  def config(self):
    ''' The configuration for use with this `Store`.
        Falls back to `Config.default`.
    '''
    from .config import Config  # pylint:disable=import-outside-toplevel
    return self._config or Config.default(factory=True)

  @config.setter
  def config(self, new_config: "Config"):
    ''' Set the configuration for use with this Store.
    '''
    self._config = new_config

  ##########################################################################
  # Blockmaps.
  @property
  def blockmapdir(self):
    ''' The path to this Store's blockmap directory, if specified.
        Falls back too the Config.blockmapdir.
    '''
    return self._blockmapdir or self.config.blockmapdir

  @blockmapdir.setter
  def blockmapdir(self, dirpath):
    ''' Set the Blockmap directory path.
    '''
    self._blockmapdir = dirpath

  @uses_upd
  @uses_runstate
  @require(lambda capacity: capacity >= 1)
  @typechecked
  def pushto(
      self,
      dstS,
      *,
      capacity: int = 64,
      runstate: RunState,
      upd: Upd,
      progress=None,
  ) -> Tuple[QueueIterator, Thread]:
    ''' Allocate a `QueueIterator` for Blocks to push from this
        Store to another Store `dstS`.
        Return `(Q,T)` where `Q` is a queue to receive blocks `T` is the
        `Thread` processing the queue.

        Parameters:
        * `dstS`: the secondary Store to receive `Block`s.
        * `capacity`: the queue capacity, arbitrary default `64`.
        * `progress`: an optional `Progress` counting submitted and completed data bytes.

        Once called, the caller can then `.put` Blocks onto the queue.
        When finished, call `Q.close()` to indicate end of Blocks and
        T.join() to wait for the processing completion.
    '''
    name = "%s.pushto(%s)" % (self.name, dstS.name)
    with Pfx(name):
      Q = IterableQueue(capacity=capacity, name=name)
      self.open()
      dstS.open()
      T = bg_thread(
          self._push_worker,
          args=(name, Q, dstS),
          kwargs=dict(progress=progress, runstate=runstate, upd=upd),
      )
      return Q, T

  @pfx_method
  def _push_worker(
      self,
      name: str,
      blocks: Iterable,
      dstS: "Store",
      *,
      progress: Union[Progress, None],
      runstate: RunState,
      upd: Upd,
  ):
    ''' This is a worker function which receives `HashCode`s or `Block`s
        or `bytes` from the supplied iterable `blocks` and pushes
        them to `dstS` (the second `Store`).
        This closes `self` and `dstS` on return
        and thus expects them to be opened in `self.pushto()`.

        Parameters:
        * `name`: name for this worker instance
        * `blocks`: an iterable of `HashCode`s or `Block`s or `bytes`-like objects
        * `dstS`: the destination `Store` to which to push `Block`s
    '''
    if progress is None and not upd.disabled:
      P = Progress(name, total=0)
      with P.bar(report_print=True):
        return self._push_worker(
            name, blocks, dstS, progress=P, runstate=runstate, upd=upd
        )
    try:
      if progress is None:
        add_bg = dstS.add_bg
      else:

        def add_bg(data):
          ''' Add the data and advance the progress on completion.
          '''
          R = dstS.add_bg(data)
          R.notify(lambda _: progress.advance(len(data)))
          return R

      for item in blocks:
        if isinstance(item, HashCode):
          h = item
          if h in dstS:
            # known, skip
            continue
          data = self[h]
          dlen = len(data)
          if progress is not None:
            progress.total += dlen
        else:
          # a Block?
          try:
            h = item.hashcode
          except AttributeError:
            # just data
            data = item
            dlen = len(data)
            if progress is not None:
              progress.total += dlen
          else:
            dlen = len(item)
            if progress is not None:
              progress.total += dlen
            h = item.hashcode
            if h in dstS:
              progress += dlen
              continue
        add_bg(data)
    finally:
      self.close()
      dstS.close()

  def is_complete_indirect(self, ih):
    ''' Check whether `ih`, the hashcode of an indirect Block,
        has its data and all its implied data present in this Store.
    '''
    entry = self.get_index_entry(ih)
    if entry is not None and (entry.flags & entry.INDIRECT_COMPLETE):
      # marked as complete, no need to examine the contents
      return True
    subblocks_data = self.get(ih)
    if subblocks_data is None:
      # missing hash, incomplete
      return False
    from .block import IndirectBlock  # pylint:disable=import-outside-toplevel
    IB = IndirectBlock.from_subblocks_data(subblocks_data)
    for subblock in IB.subblocks:
      h = subblock.hashcode
      if h not in self:
        # missing hash, incomplete
        return False
      if not subblock.indirect:
        # direct block, is complete
        continue
      # TODO how/when to set the flag in the index?
      if not self.is_complete_indirect(h):
        # subblock incomplete
        return False
    # ensure the index entry gets marked as complete
    if entry is not None:
      with self.modify_index_entry(ih) as entry2:
        entry2.flags |= entry2.INDIRECT_COMPLETE
    return True

  def get_index_entry(self, hashcode):
    ''' Return the index entry for `hashcode`, or `None` if there
        is no index or the index has no entry for `hashcode`.
    '''
    return None

  @contextmanager
  def modify_index_entry(self, hashcode):
    ''' Context manager to obtain and yield the index record entry for `hashcode`
        and resave it on return.

        *Important Note*:
        on Stores with no persistent index-with-flags
        this yields `None` for the entry and updates nothing on return;
        callers must recognise this.
        That is the behaviour of this default implementation.

        Stores with a persistent index such as `DataDirStore` have
        functioning versions of this method which yield a non`None`
        result.

        Example:

            with index.modify_entry(hashcode) as entry:
                if entry is not None:
                    entry.flags |= entry.INDIRECT_COMPLETE
    '''
    yield None

  @promote
  def _block_for(self, bfr: CornuCopyBuffer) -> "Block":
    ''' Store an object into this `Store`, return the `Block`.
        The object may be any object acceptable to `CornuCopyBuffer.promote`.
    '''
    from .blockify import block_for
    with self:
      return block_for(bfr)

  def block_for(self, src) -> "Block":
    # use the cache if given a filesystem path and we have a .conv_cache
    if isinstance(src, str):
      conv_cache = self.conv_cache
      if conv_cache is not None:
        from .uri import VTURI

        def cache_file_uri(fspath, cachepath):
          ''' Generate the URI for `fspath` and store in `cachepath`.
          '''
          uri = self._block_for(src).uri
          with open(cachepath, 'w') as cachef:
            print(uri, file=cachef)

        uri_cachepath = conv_cache.convof(
            src, f'filepath.vturi.{self.hashclass.hashname}', cache_file_uri
        )
        with open(uri_cachepath) as cachef:
          uri = VTURI.from_uri(cachef.readline().strip())
        return uri.block
    return self._block_for(src)

  @classmethod
  @fmtdoc
  def promote(cls, obj, config=None):
    ''' Promote `obj` to a `Store` instance.
        Existing instances are returned unchanged.
        A `str` is promoted via `Config.Store_from_spec`
        using the default `Config`.
        If `obj` is `None` it is obtained from the environment
        variable ${VT_STORE_ENVVAR} or {VT_STORE_DEFAULT!r}
        and then promoted from `str`.
    '''
    if isinstance(obj, cls):
      return obj
    if obj is None:
      obj = os.environ.get(VT_STORE_ENVVAR, VT_STORE_DEFAULT)
    if isinstance(obj, str):
      if config is None:
        from .config import Config  # pylint:disable=import-outside-toplevel
        config = Config.default(factory=True)
      return config.Store_from_spec(obj)
    raise TypeError("%s.promote: cannot promote %s" % (cls.__name__, r(obj)))

class StoreSyncBase(Store):
  ''' Subclass of Store expecting synchronous operations
      and providing asynchronous hooks, dual of StoreAsyncBase.
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

class StoreAsyncBase(Store):
  ''' Subclass of Store expecting asynchronous operations
      and providing synchronous hooks, dual of StoreSyncBase.
  '''

  #####################################
  ## Background versions of operations.
  ##

  def add(self, data):
    return self.add_bg(data)()

  def get(self, h, default=None):
    return self.get_bg(h, default=default)()

  def contains(self, h):
    return self.contains_bg(h)()

  def flush(self):
    return self.flush_bg()()

def uses_Store(func):
  ''' Decorator to provide the default Store as the parameter `S`.
  '''
  return default_params(func, S=Store.default)
