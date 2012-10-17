#!/usr/bin/python
#
# Assorted convenience functions for files and filenames/pathnames.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import with_statement, print_function
import errno
from functools import partial
import os
import os.path
import errno
import sys
from collections import namedtuple
from contextlib import contextmanager
import shutil
from tempfile import NamedTemporaryFile
import time
import unittest
from cs.env import envsub

def saferename(oldpath, newpath):
  ''' Rename a path using os.rename(), but raise an exception if the target
      path already exists. Slightly racey.
  '''
  try:
    os.lstat(newpath)
    raise OSError(errno.EEXIST)
  except OSError as e:
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
        raise ValueError("no data in temp file")
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

_FileState = namedtuple('FileState', 'mtime size dev ino')
def FileState(path, do_lstat=False):
  ''' Return a signature object for a file state derived from os.stat
      (or os.lstat if `do_lstat` is true).
      This returns an object with mtime, size, dev and ino properties
      and can be compared for equality with other signatures.
  '''
  s = os.lstat(path) if do_lstat else os.stat(path)
  return _FileState(s.st_mtime, s.st_size, s.st_dev, s.st_ino)

def poll_file(path, old_state, reload_file, missing_ok=False):
  ''' Watch a file for modification by polling its state as obtained by FileState().
      Call reload_file(path) if the state changes.
      Return (new_state, reload_file(path)) if the file was modified and was
      unchanged (stable state) beofre and after the reload_file().
      Otherwise return (None, None).
      This may raise an OSError if the `path` cannot be os.stat()ed
      and of course for any exceptions that occur calling `reload_file`.
      If `missing_ok` is true then a failure to os.stat() which
      raises OSError with ENOENT will just return (None, None).
  '''
  try:
    new_state = FileState(path)
  except OSError as e:
    if e.errno == errno.ENOENT:
      if missing_ok:
        return None, None
    raise
  if old_state is None or old_state != new_state:
    R = reload_file(path)
    try:
      new_new_state = FileState(path)
    except OSError as e:
      if e.errno == errno.ENOENT:
        if missing_ok:
          return None, None
      raise
    # make sure file was unchanged
    if new_new_state == new_state:
      return new_state, R
  return None, None

def watched_file_property(func, attr_name=None, unset_object=None, poll_rate=1):
  ''' A property whose value reloads if a file changes.
      `func` accepts the file path and returns the new value.
      The attribute {attr_name}_lock controls access to the property.
      The attributes {attr_name}_filestate and {attr_name}_path track the
      associated file state.
      The attribute {attr_name}_lastpoll tracks the last poll time.
  '''
  if attr_name is None:
    attr_name = '_' + func.__name__
  lock_name = attr_name + '_lock'
  filestate_name = attr_name + '_filestate'
  path_name = attr_name + '_path'
  lastpoll_name = attr_name + '_lastpoll'
  def getprop(self):
    ''' Try to reload property value from file if the propety value
        is stale and the file has been modified since the last reload.
    '''
    with getattr(self, lock_name):
      now = time.time()
      then = getattr(self, lastpoll_name, None)
      if then is None or then + poll_rate <= now:
        setattr(self, lastpoll_name, now)
        old_filestate = getattr(self, filestate_name, None)
        try:
          new_filestate, new_value = poll_file(getattr(self, path_name),
                                        old_filestate,
                                        partial(func, self),
                                        missing_ok=True)
        except NameError:
          raise
        except AttributeError:
          raise
        except Exception as e:
          new_value = getattr(self, attr_name, unset_object)
          if new_value is unset_object:
            raise
          import cs.logutils
          cs.logutils.exception("exception during poll_file, leaving .%s untouched", attr_name)
        else:
          if new_filestate:
            setattr(self, attr_name, new_value)
            setattr(self, filestate_name, new_filestate)
    return getattr(self, attr_name, unset_object)
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
  start = None
  lockpath = path + ext
  while True:
    try:
      lockfd = os.open(lockpath, os.O_CREAT|os.O_EXCL|os.O_RDWR, 0)
    except OSError as e:
      if e.errno == errno.EEXIST:
        if block:
          if start is None:
            start = time.time()
            complaint_last = start
            complaint_interval = 1.0
          else:
            now = time.time()
            if now - complaint_last >= complaint_interval:
              from cs.logutils import warning
              warning("cs.fileutils.lockfile: pid %d waited %ds for \"%s\"",
                      os.getpid(), now - start, lockpath)
              complaint_last = now
              complaint_interval *= 2
          time.sleep(poll_interval)
          continue
      raise
    else:
      break
  os.close(lockfd)
  yield lockpath
  os.remove(lockpath)

DEFAULT_SHORTEN_PREFIXES = ( ('$HOME/', '~/'), )

def shortpath(path, environ=None, prefixes=None):
  ''' Return `path` with the first matching leading prefix replaced.
      `environ`: environment mapping if not os.environ
      `prefixes`: iterable of (prefix, subst) to consider for replacement;
                  each `prefix` is subject to environment variable
                  substitution before consideration
                  The default considers "$HOME/" for replacement by "~/".
  '''
  if prefixes is None:
    prefixes = DEFAULT_SHORTEN_PREFIXES
  for prefix, subst in prefixes:
    prefix = envsub(prefix, environ)
    if path.startswith(prefix):
      return subst + path[len(prefix):]
  return path

class Pathname(str):
  ''' Subclass of str presenting convenience properties useful for
      fomat strings related to file paths.
  '''

  _default_prefixes = ( ('$HOME/', '~/'), )

  def __format__(self, fmt_spec):
    ''' Calling format(<Pathname>, fmt_spec) treat `fmt_spec` as a new style
        formatting string with a single positional parameter of `self`.
    '''
    if fmt_spec == '':
      return str(self)
    return fmt_spec.format(self)

  @property
  def dirname(self):
    return Pathname(os.path.dirname(self))

  @property
  def basename(self):
    return Pathname(os.path.basename(self))

  @property
  def abs(self):
    return Pathname(os.path.abspath(self))

  @property
  def isabs(self):
    return os.path.isabs(self)

  @property
  def short(self):
    return self.shorten()

  def shorten(self, environ=None, prefixes=None):
    return shortpath(self, environ=environ, prefixes=prefixes)

if __name__ == '__main__':
  import cs.fileutils_tests
  cs.fileutils_tests.selftest(sys.argv)
