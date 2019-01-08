#!/usr/bin/python

''' A content hash based data store with a filesystem layer, using
    variable sized blocks, arbitrarily sized data and utilising some
    domain knowledge to aid efficient block boundary selection.

    *NOTE*: pre-Alpha; alpha release following soon once the packaging
    is complete.

    See also the Plan 9 Venti system:
    (http://library.pantek.com/general/plan9.documents/venti/venti.html,
    http://en.wikipedia.org/wiki/Venti).
'''

from collections import namedtuple
import os
from string import ascii_letters, digits
import tempfile
import threading
from threading import (
    Lock as threading_Lock,
    RLock as threading_RLock,
    current_thread,
)
from cs.lex import texthexify, untexthexify
from cs.logutils import error, warning
from cs.mappings import StackableValues
from cs.py.stack import caller, stack_dump
from cs.seq import isordered
import cs.resources
from cs.resources import RunState
from cs.x import X

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        ##"Development Status :: 3 - Alpha",
        "Development Status :: 2 - Pre-Alpha",
        "Environment :: Console",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.buffer',
        'cs.app.flag',
        'cs.binary',
        'cs.cache',
        'cs.debug',
        'cs.deco',
        'cs.env',
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
        'lmdb',
    ],
    'entry_points': {
        'console_scripts': [
            'vt = cs.vt.__main__:main',
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

def fromtext(s):
  ''' Return raw byte array from text/hexadecimal string.
  '''
  return untexthexify(s)

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

LockContext = namedtuple("LockContext", "caller thread")

class DebuggingLock(object):
  ''' A wrapper for a threading Lock or RLock
      to notice contention and report contending uses.
  '''

  def __init__(self, recursive=False):
    self.recursive = recursive
    self.trace_acquire = False
    self._lock = threading_RLock() if recursive else threading_Lock()
    self._held = None

  def __repr__(self):
    return "%s(lock=%r,held=%s)" % (type(self).__name__, self._lock, self._held)

  def acquire(self, timeout=-1, _caller=None):
    ''' Acquire the lock and note the caller who takes it.
    '''
    if _caller is None:
      _caller = caller()
    lock = self._lock
    hold = LockContext(_caller, current_thread())
    if timeout != -1:
      warning(
          "%s:%d: lock %s: timeout=%s",
          hold.caller.filename, hold.caller.lineno,
          lock, timeout)
    contended = False
    if True:
      if lock.acquire(0):
        lock.release()
      else:
        contended = True
        held = self._held
        warning(
            "%s:%d: lock %s: waiting for contended lock, held by %s:%s:%d",
            hold.caller.filename, hold.caller.lineno,
            lock,
            held.thread, held.caller.filename, held.caller.lineno)
    acquired = lock.acquire(timeout=timeout)
    if contended:
      warning(
          "%s:%d: lock %s: %s",
          hold.caller.filename, hold.caller.lineno,
          lock,
          "acquired" if acquired else "timed out")
    self._held = hold
    if acquired and self.trace_acquire:
      X("ACQUIRED %r", self)
      stack_dump()
    return acquired

  def release(self):
    ''' Release the lock and forget who took it.
    '''
    self._held = None
    self._lock.release()

  def __enter__(self):
    ##X("%s.ENTER...", type(self).__name__)
    self.acquire(_caller=caller())
    ##X("%s.ENTER: acquired, returning self", type(self).__name__)
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    self.release()
    return False

  def _is_owned(self):
    lock = self._lock
    return lock._is_owned()
