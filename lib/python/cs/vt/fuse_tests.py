#!/usr/bin/env python3
#
# FUSE tests.
# - Cameron Simpson <cs@cskk.id.au> 10jul2014
#

from contextlib import contextmanager
import sys
import time
import unittest
from tempfile import TemporaryDirectory

from cs.logutils import warning
from cs.resources import stackattrs
from cs.testutils import SetupTeardownMixin, assertSingleThread
from cs.x import X

from .dir import Dir
try:
  from .fuse import mount, umount
except ImportError as e:
  warning("import fails, no mount function: %s", e)
  mount = None
from .store import MappingStore

TESTDIR = 'vtfuse_testdir'

@unittest.skipIf(mount is None, "no FUSE mount function")
class Test_VTFuse(SetupTeardownMixin, unittest.TestCase):
  ''' Tests for `cs.vt.fuse`.
  '''

  @contextmanager
  def setupTeardown(self):
    testname = type(self).__name__
    self.store_dict = {}
    S = MappingStore(testname, self.store_dict)
    with TemporaryDirectory(prefix=testname + '-', dir='.') as testdirpath:
      E = Dir(testdirpath)
      with S:  # TODO: try without this line? mount should do it
        mount(testdirpath, E, S=S)
        try:
          with stackattrs(self, E=E, S=S, testdirpath=testdirpath):
            yield
        finally:
          umount(testdirpath)
    time.sleep(1)
    assertSingleThread()

  def test_FS(self):
    ''' Dummy mount/umount test.
    '''

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
