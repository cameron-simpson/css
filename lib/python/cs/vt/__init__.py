#!/usr/bin/python

''' A data store after the style of the Venti scheme, but not at all binary
    compatible.

    The Plan 9 Venti system is decribed here:
      http://library.pantek.com/general/plan9.documents/venti/venti.html
      http://en.wikipedia.org/wiki/Venti

    cs.vt implements a similar scheme that supports variable
    sized blocks and arbitrary data sizes, with some domain knowledge
    to aid efficient block boundary selection.

    Man page:
      http://www.cskk.ezoshosting.com/cs/css/manuals/vt.1.html
    See also the Plan 9 Venti support manual pages:
      http://swtch.com/plan9port/man/man7/venti.html
    and the Wikipedia entry:
      http://en.wikipedia.org/wiki/Venti
    To do list now at:
      http://csbp.backpackit.com/pub/1356606
'''

import os
from string import ascii_letters, digits
import tempfile
import threading
from cs.lex import texthexify, untexthexify
from cs.seq import isordered

# Default OS level file high water mark.
# This is used for rollover levels for DataDir files and cache files.
MAX_FILE_SIZE = 1024 * 1024 * 1024

# path separator, hardwired
SEP = '/'

class _ventiDefaults(threading.local):
  ''' Per-thread default store stack.
      A Store's __enter__/__exit__ methods push/pop that store
      from the default.
  '''
  def __getattr__(self, attr):
    if attr == 'S':
      return None
    if attr == 'oldS':
      oldS = self.oldS = []
      return oldS
    raise AttributeError("no .%s attribute" % attr)
  def pushStore(self, newS):
    ##X("PUSH STORE %s => %s", defaults.S, newS)
    defaults.oldS.append(defaults.S)
    defaults.S = newS
  def popStore(self):
    oldS = defaults.oldS.pop()
    ##X("POP STORE %s => %s", defaults.S, oldS)
    defaults.S = oldS

defaults = _ventiDefaults()

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

  @staticmethod
  def mktmpdir(prefix="cs.vt"):
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
      pass
    else:
      self.assertEqual(olen, length, *a, **kw)

  def assertIsOrdered(self, s, reverse, strict=False):
    self.assertTrue(
        isordered(s, reverse, strict),
        "not ordered(reverse=%s,strict=%s): %r" % (reverse, strict, s))
