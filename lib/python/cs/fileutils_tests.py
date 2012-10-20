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
import unittest
from tempfile import NamedTemporaryFile
from .fileutils import compare, rewrite, lockfile, Pathname
from .timeutils import TimeoutError

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
        with lockfile(lockbase, timeout=0):
          self.assert_( False, "lock inside lock, should not happen: %s" % (lockpath,))
      except TimeoutError:
        pass
    self.assert_( not os.path.exists(lockpath), "after lock: lock file still exists: %s" % (lockpath,))

  def _eq(self, a, b, opdesc):
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
