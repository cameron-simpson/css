#!/usr/bin/python
#
# FUSE tests.
# - Cameron Simpson <cs@cskk.id.au> 10jul2014
#

from contextlib import contextmanager
import sys
import time
import unittest
from tempfile import TemporaryDirectory

from cs.debug import thread_dump
from cs.logutils import warning
from cs.resources import stackattrs
from cs.testutils import SetupTeardownMixin
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
        with stackattrs(self, E=E, S=S, testdirpath=testdirpath):
          try:
            yield
          finally:
            umount(testdirpath)
    time.sleep(1)
    with open('/dev/tty', 'a') as tty:
      with stackattrs(sys, stderr=tty):
        thread_dump()

  def test_FS(self):
    X("test_FS...")

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
