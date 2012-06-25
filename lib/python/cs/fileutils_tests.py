#!/usr/bin/python
#
# Unit tests for cs.fileutils.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import os
import os.path
import errno
import unittest
from tempfile import NamedTemporaryFile
from .fileutils import compare, rewrite, lockfile, Pathname

class Test(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def test_compare(self):
    data = "here are some data\n"
    with NamedTemporaryFile() as T1:
      T1.write(data)
      T1.flush()
      with NamedTemporaryFile() as T2:
        T2.write(data)
        T2.flush()
        self.assertEquals( open(T1.name).read(), data, "bad data in %s" % (T1.name,) )
        self.assertEquals( open(T2.name).read(), data, "bad data in %s" % (T2.name,) )
        self.assert_(compare(T1.name, T2.name), "mismatched data in %s and %s" % (T1.name, T2.name))

  def test_rewrite(self):
    from StringIO import StringIO
    olddata = "old data\n"
    newdata = "new data\n"
    with NamedTemporaryFile() as T1:
      T1.write(olddata)
      T1.flush()
      self.assertEquals( open(T1.name).read(), olddata, "bad old data in %s" % (T1.name,) )
      rewrite(T1.name, StringIO(newdata))
      self.assertEquals( open(T1.name).read(), newdata, "bad new data in %s" % (T1.name,) )

  def test_lockfile_00_basic(self):
    lockbase = 'testlock'
    lockext = '.lock'
    lockpath = lockbase + lockext
    self.assert_( not os.path.exists(lockpath), "before lock, lock file already exists: %s" % (lockpath,))
    with lockfile(lockbase) as lock:
      self.assert_( lock == lockpath, "inside lock, expected \"%s\", got \"%s\"" % (lockpath, lock))
      self.assert_( os.path.exists(lockpath), "inside lock, lock file does not exist: %s" % (lockpath,))
    self.assert_( not os.path.exists(lockpath), "after lock: lock file still exists: %s" % (lockpath,))

  def test_lockfile_01_conflict(self):
    lockbase = 'testlock'
    lockext = '.lock'
    lockpath = lockbase + lockext
    self.assert_( not os.path.exists(lockpath), "before lock, lock file already exists: %s" % (lockpath,))
    with lockfile(lockbase) as lock:
      self.assert_( lock == lockpath, "inside lock, expected \"%s\", got \"%s\"" % (lockpath, lock))
      self.assert_( os.path.exists(lockpath), "inside lock, lock file does not exist: %s" % (lockpath,))
      try:
        with lockfile(lockbase):
          self.assert_( False, "lock inside lock, should not happen: %s" % (lockpath,))
      except OSError as e:
        if e.errno == errno.EEXIST:
          pass
        else:
          raise
    self.assert_( not os.path.exists(lockpath), "after lock: lock file still exists: %s" % (lockpath,))

  def test_Pathname_01_attrs(self):
    for path in "a", "a/b", "a/b/c", "/a/b/c":
      P = Pathname(path)
      self.assertEqual(P.dirname, os.path.dirname(path), "bad %r.dirname" % (path,))
      self.assertEqual(P.basename, os.path.basename(path), "bad %r.basename" % (path,))
      self.assertEqual(P.abs, os.path.abspath(path), "bad %r.abs" % (path,))
      self.assertEqual(P.isabs, os.path.isabs(path), "bad %r.isabs" % (path,))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
