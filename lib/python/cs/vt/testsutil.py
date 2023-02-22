#!/usr/bin/env python3

''' A mixin for the test suites.
'''

import os
import tempfile

from cs.seq import isordered

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
        prefix="test-" + prefix + "-", suffix=".tmpdir", dir=os.getcwd()
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

  def assertIsOrdered(self, s, strict=False):
    ''' Assertion to test that an object's elements are ordered.
    '''
    self.assertTrue(
        isordered(s, strict=strict),
        "not ordered(strict=%s): %r" % (strict, s)
    )
