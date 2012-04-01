#!/usr/bin/python
#
# Assorted convenience functions for files.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import with_statement
import errno
import os
from os.path import isabs, abspath, dirname
import sys
import shutil
from tempfile import NamedTemporaryFile
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
  if not isabs(path):
    if not isabs(from_file):
      from_file = abspath(from_file)
    path = os.path.join(dirname(from_file), path)
  return path

def poll_updated(path, old_mtime, reload, missing_ok=False):
  ''' Poll a file for modification.
      Call reload(path) if the file is newer than `old_mtime`.
      Return (new_mtime, reload(path)) if the file was updated
      and was unchanged during the reload().
      Otherwise return (None, None).
      This may raise an OSError if the `path` cannot be os.stat()ed
      and of course for any exceptions that occur calling `reload`.
      If `missing_ok` is true then a failure to os.stat() that
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
    R = reload(path)
    try:
      s = os.stat(path)
    except OSError, e:
      if e.errno == errno.ENOENT:
        if missing_ok:
          return None, None
      raise
    if new_mtime == s.st_mtime:
      return new_mtime, R
  return None, None

if __name__ == '__main__':
  import cs.fileutils_tests
  cs.fileutils_tests.selftest(sys.argv)
