#!/usr/bin/python
#
# Unit tests for cs.fileutils.
#       - Cameron Simpson <cs@cskk.id.au>
#

from __future__ import print_function, absolute_import
import sys
from io import StringIO
import os
import os.path
import errno
from threading import Lock
import time
import unittest
from tempfile import NamedTemporaryFile
from .fileutils import compare, rewrite, rewrite_cmgr, Pathname, \
                        file_property, \
                        make_files_property, \
                        BackedFile, BackedFile_TestMethods
from .logutils import D
from .timeutils import TimeoutError, sleep
from .x import X

class ThingWithFileProperty(object):
  ''' An object with an `@file_property`.
  '''

  def __init__(self):
    self._test1__filename = 'testfileprop1'
    self._test1_lock = Lock()
    self._test2__filename = 'testfileprop2'
    self._test2_lock = Lock()

  def write1(self, data):
    with open(self._test1__filename, "w") as fp:
      fp.write(data)

  def write2(self, data):
    with open(self._test2__filename, "w") as fp:
      fp.write(data)

  @file_property
  def test1(self, filename):
    with open(filename) as fp:
      data = fp.read()
    ##D("test1 loads \"%s\" => %s", path, data)
    return data

class ThingWithFilesProperty(object):
  ''' An object with an `@files_property`.
  '''

  def __init__(self):
    self._test1__filenames = ('testfileprop1',)
    self._test1_lock = Lock()

  def write1(self, data):
    with open(self._test1s[0], "w") as fp:
      fp.write(data)

  def write2(self, data):
    with open(self._test2s[0], "w") as fp:
      fp.write(data)

  ##@files_property
  ##def test1(self, path0):
  ##  with open(path0) as fp:
  ##    data = fp.read()
  ##  return (path0,), data

  @make_files_property(poll_rate=0.3)
  def test2(self, paths):
    with open(paths[0]) as fp:
      data = fp.read()
    return (paths[0],), data

class Test_Misc(unittest.TestCase):
  ''' Tests.
  '''

  def setUp(self):
    self.proppath = 'cs.fileutils_tests_tstprop'
    self.lockbase = 'cs.fileutils_tests_testlock'
    self.lockext = '.lock'
    self.lockpath = self.lockbase + self.lockext
    self.fileprop = None
    self.filesprop = None

  def tearDown(self):
    tidyup = [self.proppath, self.lockpath]
    if self.fileprop:
      tidyup.append(self.fileprop._test1__filename)
      tidyup.append(self.fileprop._test2__filename)
    if self.filesprop:
      tidyup.extend(self.filesprop._test1s)
      tidyup.extend(self.filesprop._test2s)
    for path in tidyup:
      try:
        os.remove(path)
      except OSError as e:
        if e.errno != errno.ENOENT:
          raise

  def test_compare(self):
    data = "here are some data\n"
    with NamedTemporaryFile(mode='w') as T1:
      T1.write(data)
      T1.flush()
      with NamedTemporaryFile(mode='w') as T2:
        T2.write(data)
        T2.flush()
        with open(T1.name) as t1fp:
          t1data = t1fp.read()
        with open(T2.name) as t2fp:
          t2data = t2fp.read()
        self.assertEqual(t1data, data, "bad data in %s" % (T1.name,))
        self.assertEqual(t2data, data, "bad data in %s" % (T2.name,))
        self.assertTrue(
            compare(T1.name, T2.name),
            "mismatched data in %s and %s" % (T1.name, T2.name)
        )

  def test_rewrite(self):
    olddata = "old data\n"
    newdata = "new data\n"
    with NamedTemporaryFile(mode='w') as T1:
      T1.write(olddata)
      T1.flush()
      with open(T1.name) as t1fp:
        t1data = t1fp.read()
      self.assertEqual(t1data, olddata, "bad old data in %s" % (T1.name,))
      rewrite(T1.name, StringIO(newdata), mode='w')
      with open(T1.name) as t1fp:
        t1data = t1fp.read()
      self.assertEqual(t1data, newdata, "bad new data in %s" % (T1.name,))

  def test_rewrite_cmgr(self):
    olddata = "old data\n"
    newdata = "new data\n"
    with NamedTemporaryFile(mode='w') as T1:
      T1.write(olddata)
      T1.flush()
      with open(T1.name) as t1fp:
        t1data = t1fp.read()
      self.assertEqual(t1data, olddata, "bad old data in %s" % (T1.name,))
      with rewrite_cmgr(T1.name) as tfp:
        tfp.write(newdata)
      with open(T1.name) as t1fp:
        t1data = t1fp.read()
      self.assertEqual(t1data, newdata, "bad new data in %s" % (T1.name,))

  def test_file_property_00(self):
    PC = self.fileprop = ThingWithFileProperty()
    self.assertTrue(not os.path.exists(PC._test1__filename))
    data1 = PC.test1
    self.assertTrue(data1 is None)
    PC.write1("data1 value 1")
    self.assertTrue(os.path.exists(PC._test1__filename))
    sleep(1.1)
    data1 = PC.test1
    self.assertEqual(data1, "data1 value 1")
    # NB: data value changes length because the file timestamp might not
    # due to 1s resolution in stat structures
    PC.write1("data1 value 1b")
    self.assertTrue(os.path.exists(PC._test1__filename))
    data1 = PC.test1
    # too soon after last poll
    self.assertEqual(data1, "data1 value 1")
    sleep(1)
    data1 = PC.test1
    self.assertEqual(data1, "data1 value 1b")
    os.remove(PC._test1__filename)
    self.assertTrue(not os.path.exists(PC._test1__filename))
    data1 = PC.test1
    # too soon to poll
    self.assertEqual(data1, "data1 value 1b")
    sleep(1)
    # poll should return None
    data1 = PC.test1
    self.assertEqual(data1, None)
    PC.write1("data1 value 1bc")
    self.assertTrue(os.path.exists(PC._test1__filename))
    sleep(1)
    # poll should succeed and load new value
    data1 = PC.test1
    self.assertEqual(data1, "data1 value 1bc")

  def _eq(self, a, b, opdesc):
    ''' Convenience wrapper for assertEqual.
    '''
    ##if a == b:
    ##  print("OK: %s: %r == %r" % (opdesc, a, b), file=sys.stderr)
    self.assertEqual(a, b, "%s: got %r, expected %r" % (opdesc, a, b))

  def test_Pathname_01_attrs(self):
    home = '/a/b'
    maildir = '/a/b/mail'
    prefixes = (('$MAILDIR/', '+'), ('$HOME/', '~/'))
    environ = {'HOME': home, 'MAILDIR': maildir}
    for path, shortpath in (
        ("a", "a"),
        ("a/b", "a/b"),
        ("a/b/c", "a/b/c"),
        ("/a/b/c", "~/c"),
        ("/a/b/mail", "~/mail"),
        ("/a/b/mail/folder", "+folder"),
    ):
      P = Pathname(path)
      self._eq(P.dirname, os.path.dirname(path), "%r.dirname" % (path,))
      self._eq(P.basename, os.path.basename(path), "%r.basename" % (path,))
      self._eq(P.abs, os.path.abspath(path), "%r.abs" % (path,))
      self._eq(P.isabs, os.path.isabs(path), "%r.isabs" % (path,))
      self._eq(
          P.shorten(environ=environ, prefixes=prefixes), shortpath,
          "%r.shorten(environ=%r, prefixes=%r)" % (path, environ, prefixes)
      )
      for spec, expected in (
          ("{!r}", repr(P)),
          ("{.basename}", os.path.basename(path)),
          ("{.dirname}", os.path.dirname(path)),
          ("{.abs}", os.path.abspath(path)),
      ):
        self._eq(format(P, spec), expected, "format(%r, %r)" % (P, spec))

class Test_BackedFile(unittest.TestCase, BackedFile_TestMethods):
  ''' tests for `BackedFile`.
  '''

  def setUp(self, backing_filename=None):
    if backing_filename is None:
      backing_filename = __file__
    self.backing_filename = backing_filename
    self.backing_fp = open(self.backing_filename, "rb")
    self.backing_text = self.backing_fp.read()
    self.backed_fp = BackedFile(self.backing_fp)

  def tearDown(self):
    self.backed_fp.close()
    self.backing_fp.close()

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
