#!/usr/bin/python

''' A content hash based data store with a filesystem layer, using
    variable sized blocks, arbitrarily sized data and utilising some
    domain knowledge to aid efficient block boundary selection.

    *NOTE*: pre-Alpha; alpha release following soon once the packaging
    is complete.

    The package provides the `vt` command to access
    these facilities from the command line.

    This system has two main components:
    * Stores: storage areas of variable sized data blocks
      indexed by the cryptographic hashcode of their content
    * Dirents: references to filesystem entities
      containing hashcode based references to the content

    These are logically disconnected.
    Dirents are not associated with particular Stores;
    it is it sufficient to have access to any Store
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
    which is also based on variable sized blocks.
'''

import os
from string import ascii_letters, digits
import tempfile
import threading
from cs.lex import texthexify
from cs.logutils import error, warning
from cs.mappings import StackableValues
from cs.py.stack import stack_dump
from cs.seq import isordered
import cs.resources
from cs.resources import RunState

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
        'cs.debug',
        'cs.deco',
        'cs.excutils',
        'cs.fileutils',
        'cs.inttypes',
        'cs.later',
        'cs.lex',
        'cs.logutils',
        'cs.mappings',
        'cs.packetstream',
        'cs.pfx',
        'cs.progress',
        'cs.py.func',
        'cs.py.stack',
        'cs.queues',
        'cs.range',
        'cs.resources',
        'cs.result',
        'cs.seq',
        'cs.serialise',
        'cs.socketutils',
        'cs.threads',
        'cs.tty',
        'cs.units',
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
}

DEFAULT_CONFIG_PATH = '~/.vtrc'

DEFAULT_BASEDIR = '~/.vt_stores'

DEFAULT_CONFIG = {
    'GLOBAL': {
        'basedir': DEFAULT_BASEDIR,
        'blockmapdir': '[default]/blockmaps',
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

class _Defaults(threading.local, StackableValues):
  ''' Per-thread default context stack.

      A Store's __enter__/__exit__ methods push/pop that store
      from the `.S` attribute.
  '''

  _Ss = []  # global stack of fallback Store values

  def __init__(self):
    threading.local.__init__(self)
    StackableValues.__init__(self)
    self.push('runstate', RunState())
    self.push('fs', None)
    self.push('block_cache', None)

  def _fallback(self, key):
    ''' Fallback function for empty stack.
    '''
    if key == 'S':
      warning("no per-Thread Store stack, using the global stack")
      stack_dump()
      Ss = self._Ss
      if Ss:
        return Ss[-1]
      error("%s: no per-Thread defaults.S and no global stack, returning None", self)
      return None
    raise ValueError("no fallback for %r" % (key,))

  def pushStore(self, newS):
    ''' Push a new Store onto the per-Thread stack.
    '''
    self.push('S', newS)

  def popStore(self):
    ''' Pop and return the topmost Store from the per-Thread stack.
    '''
    oldS = self.pop('S')
    return oldS

  def push_Ss(self, newS):
    ''' Push a new Store onto the global stack.
    '''
    self._Ss.append(newS)

  def pop_Ss(self):
    ''' Pop and return the topmost Store from the global stack.
    '''
    return self._Ss.pop()

  def push_runstate(self, new_runstate):
    ''' Context manager to push a new RunState instance onto the per-Thread stack.
    '''
    return self.stack('runstate', new_runstate)

defaults = _Defaults()

# Characters that may appear in text sections of a texthexify result.
# Because we transcribe Dir blocks this way it includes some common
# characters used for metadata, notably including the double quote
# because it is heavily using in JSON.
# It does NOT include '/' because these appear at the start of paths.
_TEXTHEXIFY_WHITE_CHARS = ascii_letters + digits + '_+-.,=:;{"}*'

def totext(data):
  ''' Represent a byte sequence as a hex/text string.
  '''
  return texthexify(data, whitelist=_TEXTHEXIFY_WHITE_CHARS)

class _TestAdditionsMixin:
  ''' Some common methods uses in tests.
  '''

  @classmethod
  def mktmpdir(cls, prefix=None):
    ''' Create a temporary directory.
    '''
    if prefix is None:
      prefix = cls.__qualname__
    return tempfile.TemporaryDirectory(
        prefix="test-" + prefix + "-",
        suffix=".tmpdir",
        dir=os.getcwd()
    )

  def assertLen(self, o, length, *a, **kw):
    ''' Test len(o) unless it raises TypeError.
    '''
    try:
      olen = len(o)
    except TypeError:
      pass
    else:
      self.assertEqual(olen, length, *a, **kw)

  def assertIsOrdered(self, s, reverse, strict=False):
    ''' Assertion to test that an object's elements are ordered.
    '''
    self.assertTrue(
        isordered(s, reverse, strict),
        "not ordered(reverse=%s,strict=%s): %r" % (reverse, strict, s))
