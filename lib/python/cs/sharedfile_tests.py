#!/usr/bin/python
#
# Unit tests for cs.sharedfile.
#       - Cameron Simpson <cs@cskk.id.au>
#

from __future__ import print_function, absolute_import
import errno
import os
import sys
import time
import unittest
from .sharedfile import lockfile

class Test_Misc(unittest.TestCase):
  ''' Tests for `cs.sharedfile`.
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
      tidyup.append(self.fileprop._test1_path)
      tidyup.append(self.fileprop._test2_path)
    if self.filesprop:
      tidyup.extend(self.filesprop._test1_paths)
      tidyup.extend(self.filesprop._test2_paths)
    for path in tidyup:
      try:
        os.remove(path)
      except OSError as e:
        if e.errno != errno.ENOENT:
          raise

  def test_lockfile_00_basic(self):
    lockbase = self.lockbase
    lockpath = self.lockpath
    self.assertTrue(
        not os.path.exists(lockpath),
        "before lock, lock file already exists: %s" % (lockpath,)
    )
    with lockfile(lockbase) as lock:
      self.assertTrue(
          lock == lockpath,
          "inside lock, expected \"%s\", got \"%s\"" % (lockpath, lock)
      )
      self.assertTrue(
          os.path.exists(lockpath),
          "inside lock, lock file does not exist: %s" % (lockpath,)
      )
    self.assertTrue(
        not os.path.exists(lockpath),
        "after lock: lock file still exists: %s" % (lockpath,)
    )

  def test_lockfile_01_conflict(self):
    lockbase = self.lockbase
    lockpath = self.lockpath
    self.assertTrue(
        not os.path.exists(lockpath),
        "before lock, lock file already exists: %s" % (lockpath,)
    )
    with lockfile(lockbase) as lock:
      self.assertTrue(
          lock == lockpath,
          "inside lock, expected \"%s\", got \"%s\"" % (lockpath, lock)
      )
      self.assertTrue(
          os.path.exists(lockpath),
          "inside lock, lock file does not exist: %s" % (lockpath,)
      )
      try:
        with lockfile(lockbase, timeout=0):
          self.assertTrue(
              False, "lock inside lock, should not happen: %s" % (lockpath,)
          )
      except TimeoutError:
        pass
    self.assertTrue(
        not os.path.exists(lockpath),
        "after lock: lock file still exists: %s" % (lockpath,)
    )

  def test_lockfile_02_timeout(self):
    lockbase = self.lockbase
    lockpath = self.lockpath
    self.assertTrue(
        not os.path.exists(lockpath),
        "before lock, lock file already exists: %s" % (lockpath,)
    )
    with lockfile(lockbase) as lock:
      self.assertTrue(
          lock == lockpath,
          "inside lock, expected \"%s\", got \"%s\"" % (lockpath, lock)
      )
      self.assertTrue(
          os.path.exists(lockpath),
          "inside lock before nested lock attempt, lock file does not exist: %s"
          % (lockpath,)
      )
      start = time.time()
      try:
        with lockfile(lockbase, timeout=0.5):
          self.assertTrue(
              False, "lock inside lock, should not happen: %s" % (lockpath,)
          )
      except TimeoutError:
        end = time.time()
        self.assertTrue(
            end - start >= 0.5, "nested lock timeout took less than 0.5s"
        )
        self.assertTrue(
            end - start <= 0.6, "nested lock timeout took more than 0.6s"
        )
      self.assertTrue(
          os.path.exists(lockpath),
          "inside lock after nested lock attempt, lock file does not exist: %s"
          % (lockpath,)
      )
    self.assertTrue(
        not os.path.exists(lockpath),
        "after lock: lock file still exists: %s" % (lockpath,)
    )

def selftest(argv):
  unittest.main(__name__, None, argv, failfast=True)

if __name__ == '__main__':
  selftest(sys.argv)
