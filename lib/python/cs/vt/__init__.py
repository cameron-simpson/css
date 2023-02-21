#!/usr/bin/env python3

''' A content hash based data store with a filesystem layer, using
    variable sized blocks, arbitrarily sized data and utilising some
    domain knowledge to aid efficient block boundary selection.

    *Note*: the "mount" filesystem facility uses FUSE,
    which may need manual OS installation.
    On MacOS this means installing `osxfuse`
    for example from MacPorts.
    You will also need the `llfuse` Python module,
    which is not automatically required by this package.

    The package provides the `vt` command to access
    these facilities from the command line.

    This system has two main components:
    * Stores: storage areas of variable sized data blocks
      indexed by the cryptographic hashcode of their content
    * Dirents: references to filesystem entities
      containing hashcode based references to the content

    These are logically disconnected.
    Dirents are not associated with particular Stores;
    it is sufficient to have access to any Store
    containing the required blocks.

    The other common entity is the Archive,
    which is just a text file containing
    a timestamped log of revisions of a Dirent.
    These can be mounted as a FUSE filesystem,
    and the `vt pack` command simply stores
    a directory tree into the current Store,
    and records the stored reference in an Archive file.

    See also the Plan 9 Venti system:
    (http://library.pantek.com/general/plan9.documents/venti/venti.html,
    http://en.wikipedia.org/wiki/Venti)
    which is also a system based on variable sized blocks.
'''

from abc import ABC, abstractmethod
from contextlib import closing, contextmanager
import os
from threading import Thread
from types import SimpleNamespace as NS
from typing import Mapping, Tuple

from cs.context import stackattrs
from cs.deco import default_params, fmtdoc
from cs.later import Later
from cs.lex import r
from cs.logutils import warning
from cs.progress import Progress, OverProgress, progressbar
from cs.queues import IterableQueue, QueueIterator
from cs.pfx import Pfx, pfx_method
from cs.resources import MultiOpenMixin, RunState, RunStateMixin, uses_runstate
from cs.seq import Seq
from cs.threads import bg as bg_thread, State as ThreadState, HasThreadState

from icontract import require
from typeguard import typechecked

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

# intercept Lock and RLock
if False:
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
  cs.resources._mom_lockclass = RLock
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

_progress = Progress(name="cs.vt.common.progress"),
_over_progress = OverProgress(name="cs.vt.common.over_progress")

# some shared default state, Thread independent
common = NS(
    progress=_progress,
    over_progress=_over_progress,
    S=None,
)

del _progress
del _over_progress

class _Defaults(ThreadState):
  ''' Per-thread default context stack.

      A Store's __enter__/__exit__ methods push/pop that store
      from the `.S` attribute.
  '''

  # Global stack of fallback Store values.
  # These are pushed by things like main or the fuse setup
  # to provide a shared default across Threads.
  _Ss = []

  def __init__(self):
    super().__init__()
    self.progress = common.progress
    self.fs = None
    self.block_cache = None
    self.show_progress = False

  def __getattr__(self, attr):
    if attr == 'S':
      S = common.S
      if S is None:
        S = self.config['default']
      return S
    raise AttributeError(attr)

  @contextmanager
  def common_S(self, S):
    ''' Context manager to push a Store onto `common.S`.
    '''
    with stackattrs(common, S=S):
      yield

NOdefaults = _Defaults()

class Store(Mapping, HasThreadState, MultiOpenMixin, HashCodeUtilsMixin,
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
      self, name, *, capacity=None, hashclass=None, runstate: RunState
  ):
    ''' Initialise the Store.

        Parameters:
        * `name`: a name for this Store;
          if `None`, a sequential name based on the Store class name
          is generated
        * `capacity`: a capacity for the internal `Later` queue, default 4
        * `hashclass`: the hash class to use for this Store,
          default: `DEFAULT_HASHCLASS` (`{DEFAULT_HASHCLASS.__name__}`)
        * `runstate`: a `cs.resources.RunState` for external control;
          if not supplied one is allocated
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
        for example to create an empty DataDir,
        that code goes here.
    '''

  def __str__(self):
    ##return "STORE(%s:%s)" % (type(self), self.name)
    params = []
    for attr, val in sorted(self._str_attrs.items()):
      if isinstance(val, type):
        val_s = '<%s.%s>' % (val.__module__, val.__name__)
      else:
        val_s = str(val)
      params.append(attr + '=' + val_s)
    return "%s:%s(%s)" % (
        self.__class__.__name__, self.hashclass.hashname,
        ','.join([repr(self.name)] + params)
    )

  __repr__ = __str__

  __bool__ = lambda self: True

  # Basic support for putting Stores in sets.
  def __hash__(self):
    return id(self)

  def hash(self, data):
    ''' Return a HashCode instance from data bytes.
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

  def keys(self):
    ''' Return an iterator over the Store's hashcodes.
    '''
    return self.hashcodes_from()

  def __iter__(self):
    ''' Return an iterator over the Store's hashcodes.
    '''
    return self.keys()

  def __getitem__(self, h):
    ''' Return the data bytes associated with the supplied hashcode.
        Raise `MissingHashcodeError` (a subclass of `KeyError`)
        if the hashcode is not present.
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

  ###################################################
  ## Context manager methods via ContextManagerMixin.
  ##
  def __enter_exit__(self):
    with HasThreadState.as_contextmanager(self):
      with MultiOpenMixin.as_contextmanager(self):
        with defaults(S=self):
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
        # obtain this before the Later forgets it
        finished = L.finished_event
      finished.wait()

  #############################
  ## Function dispatch methods.
  ##

  def _defer(self, func, *args, **kwargs):
    ''' Defer a function via the internal `Later` queue.
        Hold an `open()` on `self` to avoid easy shutdown.
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
    ''' The configuration for use with this Store.
        Falls back to `defaults.config`.
    '''
    return self._config or defaults.config

  @config.setter
  def config(self, new_config):
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

  @typechecked
  @require(lambda capacity: capacity >= 1)
  def pushto(self,
             dstS,
             *,
             capacity: int = 64,
             progress=None) -> Tuple[QueueIterator, Thread]:
    ''' Allocate a `QueueIterator` for Blocks to push from this
        Store to another Store `dstS`.
        Return `(Q,T)` where `Q` is the new `QueueIterator` and `T` is the
        `Thread` processing the queue.

        Parameters:
        * `dstS`: the secondary Store to receive Blocks.
        * `capacity`: the Queue capacity, arbitrary default `64`.
        * `progress`: an optional `Progress` counting submitted and completed data bytes.

        Once called, the caller can then `.put` Blocks onto the queue.
        When finished, call `Q.close()` to indicate end of Blocks and
        T.join() to wait for the processing completion.
    '''
    name = "%s.pushto(%s)" % (self.name, dstS.name)
    with Pfx(name):
      Q = IterableQueue(capacity=capacity, name=name)
      srcS = self
      srcS.open()
      dstS.open()
      T = bg_thread(
          lambda: (
              self._push_blocks(name, Q, srcS, dstS, progress),
              srcS.close(),
              dstS.close(),
          )
      )
      return Q, T

  @pfx_method
  def _push_blocks(self, name, blocks, srcS, dstS, progress):
    ''' This is a worker function which pushes Blocks or bytes from
        the supplied iterable `blocks` to the second Store.

        Parameters:
        * `name`: name for this worker instance
        * `blocks`: an iterable of `HashCode`s or Blocks or bytes-like objects
        * `srcS`: the source Store from which to obtain block data
        * `dstS`: the target Store to which to push Blocks
    '''
    with srcS:
      for item in progressbar(blocks, f'{self.name}.pushto',
                              update_frequency=64):
        if isinstance(item, HashCode):
          h = item
          if h in dstS:
            continue
          data = srcS[h]
          dstS.add_bg(data)
        else:
          # a Block?
          try:
            h = item.hashcode
          except AttributeError:
            # just data
            data = item
            dstS.add(data)
          else:
            # Block
            if h in dstS:
              continue
            data = item.data
            dstS.add_bg(data)

  @classmethod
  @fmtdoc
  def promote(cls, obj):
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
      from .config import Config
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
  return default_params(func, S=Store.default())
