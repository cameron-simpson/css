#!/usr/bin/python

''' A data store after the style of the Venti scheme, but not at all binary
    compatible.

    The Plan 9 Venti system is decribed here:
      http://library.pantek.com/general/plan9.documents/venti/venti.html
      http://en.wikipedia.org/wiki/Venti

    cs.venti implements a similar scheme that supports variable
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

import re
from os.path import abspath
from string import ascii_letters, digits
import tempfile
import threading
from cs.lex import texthexify, untexthexify
from cs.logutils import X
from cs.seq import isordered

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
# characters used for metadata.
_TEXTHEXIFY_WHITE_CHARS = ascii_letters + digits + '_+-.,=:;{}*/'

def totext(data):
  ''' Represent a byte sequence as a hex/text string.
  '''
  return texthexify(data, whitelist=_TEXTHEXIFY_WHITE_CHARS)

class _TestAdditionsMixin:
  ''' Some common methods uses in tests.
  '''

  @staticmethod
  def mktmpdir():
    return abspath(tempfile.mkdtemp(prefix="test-cs.venti", suffix=".tmpdir", dir='.'))

  def assertLen(self, o, length, *a, **kw):
    ''' Test len(o) unless it raises NotImplementedError.
    '''
    try:
      olen = len(o)
    except TypeError:
      import cs.logutils
      cs.logutils.debug("skip assertLen(o, %d): no len(%s)", length, type(o))
      pass
    else:
      self.assertEqual(olen, length, *a, **kw)

  def assertIsOrdered(self, s, reverse, strict=False):
    return isordered(s, reverse, strict)
