#!/usr/bin/python

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

from contextlib import contextmanager
from types import SimpleNamespace as NS

from cs.context import stackattrs
from cs.deco import default_params
from cs.logutils import error, warning
from cs.progress import Progress, OverProgress
from cs.py.stack import stack_dump
import cs.resources
from cs.resources import RunState
from cs.threads import State as ThreadState

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
    runstate=RunState("cs.vt.common.runstate"),
    config=None,
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
    self.runstate = common.runstate
    self.fs = None
    self.block_cache = None
    self.show_progress = False

  @property
  def config(self):
    cfg = common.config
    if not cfg:
      from .config import Config
      cfg = Config()
    return cfg

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

defaults = _Defaults()

def uses_Store(func):
  ''' Decorator to provide the default Store as the parameter `S`.
  '''
  return default_params(func, S=lambda: defaults.S)
