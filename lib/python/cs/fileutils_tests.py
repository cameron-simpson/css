#!/usr/bin/python
#
# Unit tests for cs.fileutils.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import print_function
import sys
import os
import os.path
import errno
from threading import Lock
import time
import unittest
from tempfile import NamedTemporaryFile
from .fileutils import compare, rewrite, lockfile, Pathname, file_property, make_file_property
from .timeutils import TimeoutError

class TestFileProperty(object):
  def __init__(self):
    self._test1_path = 'testfileprop1'
    self._test1_lock = Lock()
    self._test2_path = 'testfileprop2'
    self._test2_lock = Lock()
  def write1(self, data):
    with open(self._test1_path, "w") as fp:
      fp.write(data)
  def write2(self, data):
    with open(self._test2_path, "w") as fp:
      fp.write(data)
  @file_property
  def test1(self, path):
    with open(path) as fp:
      data = fp.read()
    return data
  @make_file_property(poll_rate=2.0)
  def test2(self, path):
    with open(path) as fp:
      data = fp.read()
    return data

class Test(unittest.TestCase):

  def setUp(self):
    self.proppath = 'cs.fileutils_tests_tstprop'
    self.lockbase = 'cs.fileutils_tests_testlock'
    self.lockext = '.lock'
    self.lockpath = self.lockbase + self.lockext
    self.fileprop = None

  def tearDown(self):
    tidyup = [ self.proppath, self.lockpath ]
    if self.fileprop:
      tidyup.append(self.fileprop._test1_path)
      tidyup.append(self.fileprop._test2_path)
    for path in tidyup:
      try:
        os.remove(path)
      except OSError as e:
        if e.errno != errno.ENOENT:
          raise

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
    lockbase = self.lockbase
    lockpath = self.lockpath
    self.assert_( not os.path.exists(lockpath), "before lock, lock file already exists: %s" % (lockpath,))
    with lockfile(lockbase) as lock:
      self.assert_( lock == lockpath, "inside lock, expected \"%s\", got \"%s\"" % (lockpath, lock))
      self.assert_( os.path.exists(lockpath), "inside lock, lock file does not exist: %s" % (lockpath,))
    self.assert_( not os.path.exists(lockpath), "after lock: lock file still exists: %s" % (lockpath,))

  def test_lockfile_01_conflict(self):
    lockbase = self.lockbase
    lockpath = self.lockpath
    self.assert_( not os.path.exists(lockpath), "before lock, lock file already exists: %s" % (lockpath,))
    with lockfile(lockbase) as lock:
      self.assert_( lock == lockpath, "inside lock, expected \"%s\", got \"%s\"" % (lockpath, lock))
      self.assert_( os.path.exists(lockpath), "inside lock, lock file does not exist: %s" % (lockpath,))
      try:
        with lockfile(lockbase, timeout=0):
          self.assert_( False, "lock inside lock, should not happen: %s" % (lockpath,))
      except TimeoutError:
        pass
    self.assert_( not os.path.exists(lockpath), "after lock: lock file still exists: %s" % (lockpath,))

  def test_lockfile_02_timeout(self):
    lockbase = self.lockbase
    lockpath = self.lockpath
    self.assert_( not os.path.exists(lockpath), "before lock, lock file already exists: %s" % (lockpath,))
    with lockfile(lockbase) as lock:
      self.assert_( lock == lockpath, "inside lock, expected \"%s\", got \"%s\"" % (lockpath, lock))
      self.assert_( os.path.exists(lockpath), "inside lock before nested lock attempt, lock file does not exist: %s" % (lockpath,))
      start = time.time()
      try:
        with lockfile(lockbase, timeout=0.5):
          self.assert_( False, "lock inside lock, should not happen: %s" % (lockpath,))
      except TimeoutError:
        end = time.time()
        self.assert_( end - start >= 0.5, "nested lock timeout took less than 0.5s" )
        self.assert_( end - start <= 0.6, "nested lock timeout took more than 0.6s" )
      self.assert_( os.path.exists(lockpath), "inside lock after nested lock attempt, lock file does not exist: %s" % (lockpath,))
    self.assert_( not os.path.exists(lockpath), "after lock: lock file still exists: %s" % (lockpath,))

  def test_file_property_00(self):
    PC = self.fileprop = TestFileProperty()
    self.assert_(not os.path.exists(PC._test1_path))
    data1 = PC.test1
    self.assert_(data1 is None)
    PC.write1("data1 value 1")
    self.assert_(os.path.exists(PC._test1_path))
    data1 = PC.test1
    # too soon after last poll
    self.assert_(data1 is None)
    time.sleep(1)
    data1 = PC.test1
    self.assertEquals(data1, "data1 value 1")
    PC.write1("data1 value 2")
    self.assert_(os.path.exists(PC._test1_path))
    data1 = PC.test1
    # too soon after last poll
    self.assertEquals(data1, "data1 value 1")
    time.sleep(1)
    data1 = PC.test1
    self.assertEquals(data1, "data1 value 2")
    os.remove(PC._test1_path)
    self.assert_(not os.path.exists(PC._test1_path))
    data1 = PC.test1
    # too soon to poll
    self.assertEquals(data1, "data1 value 2")
    time.sleep(1)
    # poll should fail and keep cached value
    data1 = PC.test1
    self.assertEquals(data1, "data1 value 2")
    PC.write1("data1 value 3")
    self.assert_(os.path.exists(PC._test1_path))
    data1 = PC.test1
    # too soon to poll
    self.assertEquals(data1, "data1 value 2")
    time.sleep(1)
    # poll should succeed and load new value
    data1 = PC.test1
    self.assertEquals(data1, "data1 value 3")

  def test_make_file_property_01(self):
    PC = self.fileprop = TestFileProperty()
    self.assert_(not os.path.exists(PC._test2_path))
    data2 = PC.test2
    self.assert_(data2 is None)
    PC.write2("data2 value 1")
    self.assert_(os.path.exists(PC._test2_path))
    data2 = PC.test2
    # too soon after last poll
    self.assert_(data2 is None)
    time.sleep(1)
    data2 = PC.test2
    # still soon after last poll
    self.assert_(data2 is None)
    time.sleep(2)
    data2 = PC.test2
    self.assertEquals(data2, "data2 value 1")
    PC.write2("data2 value 2")
    self.assert_(os.path.exists(PC._test2_path))
    data2 = PC.test2
    # too soon after last poll
    self.assertEquals(data2, "data2 value 1")
    time.sleep(1)
    data2 = PC.test2
    # still too soon after last poll
    self.assertEquals(data2, "data2 value 1")
    time.sleep(2)
    data2 = PC.test2
    self.assertEquals(data2, "data2 value 2")
    os.remove(PC._test2_path)
    self.assert_(not os.path.exists(PC._test2_path))
    data2 = PC.test2
    # too soon to poll
    self.assertEquals(data2, "data2 value 2")
    time.sleep(3)
    # poll should fail and keep cached value
    data2 = PC.test2
    self.assertEquals(data2, "data2 value 2")
    PC.write2("data2 value 3")
    self.assert_(os.path.exists(PC._test2_path))
    data2 = PC.test2
    # too soon to poll
    self.assertEquals(data2, "data2 value 2")
    time.sleep(3)
    # poll should succeed and load new value
    data2 = PC.test2
    self.assertEquals(data2, "data2 value 3")

  def _eq(self, a, b, opdesc):
    ''' Convenience wrapper for assertEqual.
    '''
    ##if a == b:
    ##  print("OK: %s: %r == %r" % (opdesc, a, b), file=sys.stderr)
    self.assertEqual(a, b, "%s: got %r, expected %r" % (opdesc, a, b))

  def test_Pathname_01_attrs(self):
    home = '/a/b'
    maildir = '/a/b/mail'
    prefixes = ( ( '$MAILDIR/', '+' ), ( '$HOME/', '~/') )
    environ = { 'HOME': home, 'MAILDIR': maildir }
    for path, shortpath in (
          ( "a",                "a" ),
          ( "a/b",              "a/b" ),
          ( "a/b/c",            "a/b/c" ),
          ( "/a/b/c",           "~/c" ),
          ( "/a/b/mail",        "~/mail" ),
          ( "/a/b/mail/folder", "+folder" ),
        ):
      P = Pathname(path)
      self._eq(P.dirname, os.path.dirname(path), "%r.dirname" % (path,))
      self._eq(P.basename, os.path.basename(path), "%r.basename" % (path,))
      self._eq(P.abs, os.path.abspath(path), "%r.abs" % (path,))
      self._eq(P.isabs, os.path.isabs(path), "%r.isabs" % (path,))
      self._eq(P.shorten(environ=environ, prefixes=prefixes),
                       shortpath,
                       "%r.shorten(environ=%r, prefixes=%r)"
                         % (path, environ, prefixes))
      for spec, expected in (
                            ("{!r}", repr(P)),
                            ("{.basename}", os.path.basename(path)),
                            ("{.dirname}", os.path.dirname(path)),
                            ("{.abs}", os.path.abspath(path)),
                          ):
        self._eq(format(P, spec), expected, "format(%r, %r)" % (P, spec))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
