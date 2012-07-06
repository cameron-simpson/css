#!/usr/bin/python
#
# Assorted convenience functions for files and filenames/pathnames.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import with_statement
import errno
from functools import partial
import os
import os.path
import errno
import sys
from contextlib import contextmanager
import shutil
from tempfile import NamedTemporaryFile
import time
import unittest

def saferename(oldpath, newpath):
  ''' Rename a path using os.rename(), but raise an exception if the target
      path already exists. Slightly racey.
  '''
  try:
    os.lstat(newpath)
    raise OSError(errno.EEXIST)
  except OSError, e:
    if e.errno != errno.ENOENT:
      raise
    os.rename(oldpath, newpath)

def trysaferename(oldpath, newpath):
  ''' A saferename() that returns True on success, False on failure.
  '''
  try:
    saferename(oldpath, newpath)
  except OSError:
    return False
  except:
    raise
  return True

def compare(f1, f2, mode="rb"):
  ''' Compare the contents of two file-like objects `f1` and `f2` for equality.
      If `f1` or `f2` is a string, open the named file using `mode`
      (default: "rb").
  '''
  if type(f1) is str:
    with open(f1, mode) as f1fp:
      return compare(f1fp, f2, mode)
  if type(f2) is str:
    with open (f2, mode) as f2fp:
      return compare(f1, f2fp, mode)
  return f1.read() == f2.read()

def rewrite(filepath, data,
            backup_ext=None,
            do_rename=False,
            do_diff=None,
            empty_ok=False,
            overwrite_anyway=False):
  ''' Rewrite the file `filepath` with data from the file object `data`.
      If not `empty_ok` (default False), raise ValueError if the new data are
      empty.
      If not `overwrite_anyway` (default False), do not overwrite or backup
      if the new data matches the old data.
      If `backup_ext` is a nonempty string, take a backup of the original at
      filepath + backup_ext.
      If `do_diff` is not None, call `do_diff(filepath, tempfile)`.
      If `do_rename` (default False), rename the temp file to
      `filepath` after copying the permission bits.
      Otherwise (default), copy the tempfile to `filepath`.
  '''
  with NamedTemporaryFile() as T:
    T.write(data.read())
    T.flush()
    if not empty_ok:
      st = os.stat(T.name)
      if st.st_size == 0:
        raise ValueError, "no data in temp file"
    if do_diff or not overwrite_anyway:
      # need to compare data
      if compare(T.name, filepath):
        # data the same, do nothing
        return
      if do_diff:
        # call the supplied differ
        do_diff(filepath, T.name)
    if do_rename:
      # rename new file into old path
      # tries to preserve perms, but does nothing for other metadata
      copymode(filepath, T.name)
      if backup_ext:
        os.link(filepath, filepath + backup_ext)
      os.rename(T.name, filepath)
    else:
      # overwrite old file - preserves perms, ownership, hard links
      if backup_ext:
        shutil.copy2(filepath, filepath + backup_ext)
      shutil.copyfile(T.name, filepath)

def abspath_from_file(path, from_file):
  ''' Return the absolute path if `path` with respect to `from_file`,
      as one might do for an include file.
  '''
  if not os.path.isabs(path):
    if not os.path.isabs(from_file):
      from_file = os.path.abspath(from_file)
    path = os.path.join(os.path.dirname(from_file), path)
  return path

def watch_file(path, old_mtime, reload_file, missing_ok=False):
  ''' Watch a file for modification by polling its mtime.
      Call reload_file(path) if the file is newer than `old_mtime`.
      Return (new_mtime, reload_file(path)) if the file was updated and was
      unchanged (stable mtime and size) during the reload_file().
      Otherwise return (None, None).
      This may raise an OSError if the `path` cannot be os.stat()ed
      and of course for any exceptions that occur calling `reload_file`.
      If `missing_ok` is true then a failure to os.stat() which
      raises OSError with ENOENT will just return (None, None).
  '''
  try:
    s = os.stat(path)
  except OSError, e:
    if e.errno == errno.ENOENT:
      if missing_ok:
        return None, None
    raise
  if old_mtime is None or s.st_mtime > old_mtime:
    new_mtime = s.st_mtime
    # require these four to be unchanged across the reload
    new_stat = (new_mtime, s.st_size, s.st_dev, s.st_ino)
    R = reload_file(path)
    try:
      s = os.stat(path)
    except OSError, e:
      if e.errno == errno.ENOENT:
        if missing_ok:
          return None, None
      raise
    # make sure file was unchanged
    new_new_stat = (s.st_mtime, s.st_size, s.st_dev, s.st_ino)
    if new_stat == new_new_stat:
      return new_mtime, R
  return None, None

def watched_file_property(func, prop_name=None, unset_object=None, poll_rate=1):
  ''' A property whose value reloads if a file changes.
      `func` accepts the file path and returns the new value.
      The property {prop_name}_lock controls access to the property.
      The properties {prop_name}_mtime, {prop_name}_path track the
      associated file state.
      The property {prop_name}_lastpoll track the last poll time.
  '''
  if prop_name is None:
    prop_name = '_' + func.func_name
  lock_name = prop_name + '_lock'
  mtime_name = prop_name + '_mtime'
  path_name = prop_name + '_path'
  lastpoll_name = prop_name + '_lastpoll'
  def getprop(self):
    ''' Attempt lockless fetch of property first.
        Use lock if property is unset.
    '''
    with getattr(self, lock_name):
      now = time.time()
      then = getattr(self, lastpoll_name, None)
      if then is None or then + poll_rate <= now:
        setattr(self, lastpoll_name, now)
        old_mtime = getattr(self, mtime_name, None)
        new_mtime, value = watch_file(getattr(self, path_name),
                                      old_mtime,
                                      partial(func, self),
                                      missing_ok=True)
        if new_mtime:
          setattr(self, prop_name, value)
          setattr(self, mtime_name, new_mtime)
        else:
          value = getattr(self, prop_name)
      else:
        value = getattr(self, prop_name)
    return value
  return property(getprop)

@contextmanager
def lockfile(path, ext='.lock', block=False, poll_interval=0.1):
  ''' A context manager which takes and holds a lock file.
      `path`: the base associated with the lock file.
      `ext`: the extension to the base used to construct the lock file name.
             Default: ".lock"
      `block`: if true and the lock file is already present, block until
               it is free. This operated by polling every `poll_interval`
               seconds, default 0.1s.
      `poll_interval`: polling frequency in blocking mode.
  '''
  lockpath = path + ext
  while True:
    try:
      lockfd = os.open(lockpath, os.O_CREAT|os.O_EXCL|os.O_RDWR, 0)
    except OSError, e:
      if e.errno == errno.EEXIST:
        if block:
          sleep(poll_interval)
          continue
      raise
    else:
      break
  os.close(lockfd)
  yield lockpath
  os.remove(lockpath)

class Pathname(str):
  ''' Subclass of str which presenting convenience properties useful for
      fomat strings related to file paths.
  '''

  def __init__(self, s):
    str.__init__(s)

  def __format__(self, spec):
    ''' Calling format(<Pathname>, spec) treat `spec` as a new style
        formatting string with a single positional parameter of `self`.
    '''
    return spec.format(self)

  @property
  def dirname(self):
    return os.path.dirname(self)

  @property
  def basename(self):
    return os.path.basename(self)

  @property
  def abs(self):
    return os.path.abspath(self)

  @property
  def isabs(self):
    return os.path.isabs(self)

if __name__ == '__main__':
  import cs.fileutils_tests
  cs.fileutils_tests.selftest(sys.argv)
