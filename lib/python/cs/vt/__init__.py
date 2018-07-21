#!/usr/bin/python

''' A content hash based data store with a filesystem layer, using
    variable sized blocks, arbitrarily sized data and utilising some
    domain knowledge to aid efficient block boundary selection.

    Man page:
      http://www.cskk.ezoshosting.com/cs/css/manuals/vt.1.html

    See also:
        The Plan 9 Venti system:
          http://library.pantek.com/general/plan9.documents/venti/venti.html
          http://en.wikipedia.org/wiki/Venti
'''

from contextlib import contextmanager
import os
from string import ascii_letters, digits
import tempfile
import threading
from cs.excutils import logexc
from cs.lex import texthexify, untexthexify
from cs.logutils import error, warning
from cs.py.func import prop
from cs.seq import isordered
from cs.resources import RunState

# Default OS level file high water mark.
# This is used for rollover levels for DataDir files and cache files.
MAX_FILE_SIZE = 1024 * 1024 * 1024

# path separator, hardwired
PATHSEP = '/'

class _Defaults(threading.local):
  ''' Per-thread default store stack.
      A Store's __enter__/__exit__ methods push/pop that store
      from the default.
  '''
  _Ss = []  # global stack of fallback Store values
  def __init__(self):
    threading.local.__init__(self)
    self.Ss = []
    self.runstate = RunState()
  @prop
  @logexc
  def S(self):
    ''' The topmost per-Thread Store, or the topmost global Store.
    '''
    Ss = self.Ss
    if Ss:
      return Ss[-1]
    warning("no per-Thread Store stack, using the global stack")
    ##raise RuntimeError("BANG")
    Ss = self._Ss
    if Ss:
      return Ss[-1]
    error("%s: no per-Thread defaults.S and no global stack, returning None", self)
    return None
  def pushStore(self, newS):
    ''' Push a new Store onto the per-Thread stack.
    '''
    newS.open()
    self.Ss.append(newS)
  def popStore(self):
    ''' Pop and return the topmost Store from the per-Thread stack.
    '''
    oldS = self.Ss.pop()
    oldS.close()
    return oldS
  def push_Ss(self, newS):
    ''' Push a new Store onto the global stack.
    '''
    self._Ss.append(newS)
  def pop_Ss(self):
    ''' Pop and return the topmost Store from the global stack.
    '''
    return self._Ss.pop()

  @contextmanager
  def push_runstate(self, new_runstate):
    ''' Context manager to push a new RunState instance onto the per-Thread stack.
    '''
    old_runstate = self.runstate
    self.runstate = new_runstate
    yield new_runstate
    self.runstate = old_runstate

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
      from cs.x import X
      X("no len(0) for o=%s:%r", type(o), o)
      ##pass
    else:
      self.assertEqual(olen, length, *a, **kw)

  def assertIsOrdered(self, s, reverse, strict=False):
    ''' Assertion to test that an object's elements are ordered.
    '''
    self.assertTrue(
        isordered(s, reverse, strict),
        "not ordered(reverse=%s,strict=%s): %r" % (reverse, strict, s))
