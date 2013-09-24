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
from cs.timeutils import TimeoutError

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
            mode='w',
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
  with NamedTemporaryFile(mode=mode) as T:
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
_FileState.samefile = lambda self, other: self.dev == other.dev and self.ino == other.ino

def FileState(path, do_lstat=False):
  ''' Return a signature object for a file state derived from os.stat
      (or os.lstat if `do_lstat` is true).
      `path` may also be an int, in which case os.fstat is used.
      This returns an object with mtime, size, dev and ino attributes
      and can be compared for equality with other signatures.
  '''
  if type(path) is int:
    s = os.fstat(path)
  else:
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
    # first stat or changed stat
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

def file_property(func):
  ''' A property whose value reloads if a file changes.
      This is just the default mode for make_file_property().
      `func` accepts the file path and returns the new value.
      The underlying attribute name is '_' + func.__name__,
      the default from make_file_property().
      The attribute {attr_name}_lock controls access to the property.
      The attributes {attr_name}_filestate and {attr_name}_path track the
      associated file state.
      The attribute {attr_name}_lastpoll tracks the last poll time.

      The decorated function just loads the file content and returns
      the value computed from it. Example where .foo returns the
      length of the file data:

        class C(object):
          def __init__(self):
            self._foo_path = '.foorc'
          @file_property
          def foo(self):
            with open(self._foo_path) as foofp:
              value = len(foofp.read())
            return value

      The load function is called on the first access and on every
      access thereafter where the associated file's FileState() has
      changed and the time since the last successful load exceeds
      the poll_rate (1s). Races are largely circumvented by ignoring
      reloads that raise exceptions or where the FileState() before
      the load differs from the FileState() after the load (indicating
      the file was in flux during the load); the next poll will
      retry.
  '''
  return make_file_property()(func)

def make_file_property(attr_name=None, unset_object=None, poll_rate=1):
  ''' Construct a decorator that watches an associated file.
      `attr_name`: the underlying attribute, default: '_' + func.__name__
      `unset_object`: the sentinel value for "uninitialised", default: None
      `poll_rate`: how often in seconds to poll the file for changes, default: 1
      The attribute {attr_name}_lock controls access to the property.
      The attributes {attr_name}_filestate and {attr_name}_path track the
      associated file state.
      The attribute {attr_name}_lastpoll tracks the last poll time.

      The decorated function just loads the file content and returns
      the value computed from it. Example where .foo returns the
      length of the file data, polling no more often than once
      every 3 seconds:

        class C(object):
          def __init__(self):
            self._foo_path = '.foorc'
          @make_file_property(poll_rate=3)
          def foo(self):
            with open(self._foo_path) as foofp:
              value = len(foofp.read())
            return value

      The load function is called on the first access and on every
      access thereafter where the associated file's FileState() has
      changed and the time since the last successful load exceeds
      the poll_rate (default 1s). Races are largely circumvented
      by ignoring reloads that raise exceptions or where the FileState()
      before the load differs from the FileState() after the load
      (indicating the file was in flux during the load); the next
      poll will retry.
  '''
  def made_file_property(func):
    if attr_name is None:
      attr_value = '_' + func.__name__
    else:
      attr_value = attr_name
    attr_lock = attr_value + '_lock'
    attr_filestate = attr_value + '_filestate'
    attr_path = attr_value + '_path'
    attr_lastpoll = attr_value + '_lastpoll'
    def getprop(self):
      ''' Try to reload the property value from the file if the property value
          is stale and the file has been modified since the last reload.
      '''
      with getattr(self, attr_lock):
        now = time.time()
        then = getattr(self, attr_lastpoll, None)
        if then is None or then + poll_rate <= now:
          setattr(self, attr_lastpoll, now)
          old_filestate = getattr(self, attr_filestate, None)
          try:
            new_filestate, new_value = poll_file(getattr(self, attr_path),
                                          old_filestate,
                                          partial(func, self),
                                          missing_ok=True)
          except NameError:
            raise
          except AttributeError:
            raise
          except Exception as e:
            new_value = getattr(self, attr_value, unset_object)
            if new_value is unset_object:
              raise
            import cs.logutils
            cs.logutils.exception("exception during poll_file, leaving .%s untouched", attr_value)
          else:
            if new_filestate:
              setattr(self, attr_value, new_value)
              setattr(self, attr_filestate, new_filestate)
      return getattr(self, attr_value, unset_object)
    return property(getprop)
  return made_file_property

def files_property(func):
  ''' A property whose value reloads if any of a list of files changes.
      This is just the default mode for make_files_property().
      `func` accepts the file path and returns the new value.
      The underlying attribute name is '_' + func.__name__,
      the default from make_files_property().
      The attribute {attr_name}_lock controls access to the property.
      The attributes {attr_name}_filestates and {attr_name}_paths track the
      associated file states.
      The attribute {attr_name}_lastpoll tracks the last poll time.

      The decorated function is passed the current list of files
      and returns the new list of files and the associated value.
      One example use would be a configuration file with recurive
      include operations; the inner function would parse the first
      file in the list, and the parse would accumulate this filename
      and those of any included files so that they can be monitored,
      triggering a fresh parse if one changes. Example:

        class C(object):
          def __init__(self):
            self._foo_path = '.foorc'
          @files_property
          def foo(self,paths):
            new_paths, result = parse(paths[0])
            return new_paths, result

      The load function is called on the first access and on every
      access thereafter where an associated file's FileState() has
      changed and the time since the last successful load exceeds
      the poll_rate (1s). An attempt at avoiding races is made by
      ignoring reloads that raise exceptions and ignoring reloads
      where files that were stat()ed during the change check have
      changed state after the load.
  '''
  return make_files_property()(func)

def make_files_property(attr_name=None, unset_object=None, poll_rate=1):
  ''' Construct a decorator that watches multiple associated files.
      `attr_name`: the underlying attribute, default: '_' + func.__name__
      `unset_object`: the sentinel value for "uninitialised", default: None
      `poll_rate`: how often in seconds to poll the file for changes, default: 1
      The attribute {attr_name}_lock controls access to the property.
      The attributes {attr_name}_filestates and {attr_name}_paths track the
      associated files' state.
      The attribute {attr_name}_lastpoll tracks the last poll time.

      The decorated function is passed the current list of files
      and returns the new list of files and the associated value.
      One example use would be a configuration file with recurive
      include operations; the inner function would parse the first
      file in the list, and the parse would accumulate this filename
      and those of any included files so that they can be monitored,
      triggering a fresh parse if one changes. Example:

        class C(object):
          def __init__(self):
            self._foo_path = '.foorc'
          @files_property
          def foo(self,paths):
            new_paths, result = parse(paths[0])
            return new_paths, result

      The load function is called on the first access and on every
      access thereafter where an associated file's FileState() has
      changed and the time since the last successful load exceeds
      the poll_rate (default 1s). An attempt at avoiding races is made by
      ignoring reloads that raise exceptions and ignoring reloads
      where files that were stat()ed during the change check have
      changed state after the load.
  '''
  def made_files_property(func):
    if attr_name is None:
      attr_value = '_' + func.__name__
    else:
      attr_value = attr_name
    attr_lock = attr_value + '_lock'
    attr_filestates = attr_value + '_filestates'
    attr_paths = attr_value + '_paths'
    attr_lastpoll = attr_value + '_lastpoll'
    def getprop(self):
      ''' Try to reload the property value from the file if the property value
          is stale and the file has been modified since the last reload.
      '''
      with getattr(self, attr_lock):
        now = time.time()
        then = getattr(self, attr_lastpoll, None)
        if then is None or then + poll_rate <= now:
          setattr(self, attr_lastpoll, now)
          old_paths = getattr(self, attr_paths)
          old_filestates = getattr(self, attr_filestates, None)
          preload_filestate_map = {}
          if old_filestates is None:
            changed = True
          else:
            changed = False
	    # Instead of breaking out of the loop below on the first change
	    # found we actually stat every file path because we want to
            # maximise the coverage of the stability check after the load.
            for path, old_filestate in zip(old_paths, old_filestates):
              try:
                new_filestate = FileState(path)
              except OSError as e:
                changed = True
              else:
                preload_filestate_map[path] = new_filestate
                if old_filestate != new_filestate:
                  changed = True
          if changed:
            try:
              new_paths, new_value = func(self, old_paths)
              new_filestates = [ FileState(new_path) for new_path in new_paths ]
            except NameError:
              raise
            except AttributeError:
              raise
            except Exception as e:
              new_value = getattr(self, attr_value, unset_object)
              if new_value is unset_object:
                raise
              import cs.logutils
              cs.logutils.debug("exception reloading .%s, keeping cached value: %s", attr_value, e)
            else:
              # examine new filestates in case they changed during load
              # _if_ we knew about them from the earlier load
              stable = True
              for path, new_filestate in zip(new_paths, new_filestates):
                if path in preload_filestate_map:
                  if preload_filestate_map[path] != new_filestate:
                    stable = False
                    break
              if stable:
                setattr(self, attr_value, new_value)
                setattr(self, attr_paths, new_paths)
                setattr(self, attr_filestates, new_filestates)
      return getattr(self, attr_value, unset_object)
    return property(getprop)
  return made_files_property

@contextmanager
def lockfile(path, ext='.lock', poll_interval=0.1, timeout=None):
  ''' A context manager which takes and holds a lock file.
      `path`: the base associated with the lock file.
      `ext`: the extension to the base used to construct the lock file name.
             Default: ".lock"
      `timeout`: maximum time to wait before failing,
                 default None (wait forever).
      `poll_interval`: polling frequency when timeout is not 0.
  '''
  if timeout is not None and timeout < 0:
    raise ValueError("timeout should be None or >= 0, not %r" % (timeout,))
  start = None
  lockpath = path + ext
  while True:
    try:
      lockfd = os.open(lockpath, os.O_CREAT|os.O_EXCL|os.O_RDWR, 0)
    except OSError as e:
      if e.errno == errno.EEXIST:
        if timeout is not None and timeout <= 0:
          # immediate failure
          raise TimeoutError("cs.fileutils.lockfile: pid %d timed out on lockfile \"%s\""
                             % (os.getpid(), lockpath),
                             timeout)
        now = time.time()
        # post: timeout is None or timeout > 0
        if start is None:
          # first try - set up counters
          start = now
          complaint_last = start
          complaint_interval = 1.0
        else:
          if now - complaint_last >= complaint_interval:
            from cs.logutils import warning
            warning("cs.fileutils.lockfile: pid %d waited %ds for \"%s\"",
                    os.getpid(), now - start, lockpath)
            complaint_last = now
            complaint_interval *= 2
        # post: start is set
        if timeout is None:
          sleep_for = poll_interval
        else:
          sleep_for = min(poll_interval, start + timeout - now)
        # test for timeout
        if sleep_for <= 0:
          raise TimeoutError("cs.fileutils.lockfile: pid %d timed out on lockfile \"%s\""
                             % (os.getpid(), lockpath),
                             timeout)
        time.sleep(poll_interval)
        continue
      raise
    else:
      os.close(lockfd)
      yield lockpath
      os.remove(lockpath)
      break

def maxFilenameSuffix(dir, pfx):
  from dircache import listdir
  maxn=None
  pfxlen=len(pfx)
  for tail in [ e[pfxlen:] for e in listdir(dir)
                if len(e) > pfxlen and e.startswith(pfx)
              ]:
    if tail.isdigit():
      n=int(tail)
      if maxn is None:
        maxn=n
      elif maxn < n:
        maxn=n
  return maxn

def mkdirn(path):
  opath=path
  if len(path) == 0:
    path='.'+os.sep

  if path.endswith(os.sep):
    dir=path[:-len(os.sep)]
    pfx=''
  else:
    dir=os.path.dirname(path)
    if len(dir) == 0: dir='.'
    pfx=os.path.basename(path)

  if not os.path.isdir(dir):
    return None

  # do a quick scan of the directory to find
  # if any names of the desired form already exist
  # in order to start after them
  maxn=maxFilenameSuffix(dir, pfx)
  if maxn is None:
    newn=0
  else:
    newn=maxn

  while True:
    newn += 1
    newpath=path+str(newn)
    try:
      os.mkdir(newpath)
    except OSError as e:
      if sys.exc_value[0] == errno.EEXIST:
        # taken, try new value
        continue
      error("mkdir(%s): %s", newpath, e)
      return None
    if len(opath) == 0:
      newpath=os.path.basename(newpath)
    return newpath

def tmpdir():
  ''' Return the pathname of the default temporary directory for scratch data,
      $TMPDIR or '/tmp'.
  '''
  tmpdir = os.environ.get('TMPDIR')
  if tmpdir is None:
    tmpdir = '/tmp'
  return tmpdir

def tmpdirn(tmp=None):
  if tmp is None: tmp=tmpdir()
  return mkdirn(os.path.join(tmp, os.path.basename(sys.argv[0])))

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
      format strings related to file paths.
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
