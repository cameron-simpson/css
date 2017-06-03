#!/usr/bin/python
#
# Assorted convenience functions for files and filenames/pathnames.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import with_statement, print_function, absolute_import

DISTINFO = {
    'description': "convenience functions and classes for files and filenames/pathnames",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'requires': ['cs.asynchron', 'cs.debug', 'cs.env', 'cs.logutils', 'cs.queues', 'cs.range', 'cs.threads', 'cs.timeutils', 'cs.obj', 'cs.py3'],
}

from io import RawIOBase
from functools import partial
import os
from os import fdopen, \
               SEEK_CUR, SEEK_END, SEEK_SET, \
               O_APPEND, O_RDONLY, O_RDWR, O_WRONLY
from os.path import basename, dirname, isdir, isabs as isabspath, \
                    abspath, join as joinpath
import errno
import sys
from collections import namedtuple
from contextlib import contextmanager
import datetime
from itertools import takewhile
import shutil
import socket
from tempfile import TemporaryFile, NamedTemporaryFile
from threading import Lock, RLock, Thread
import time
import unittest
from cs.asynchron import Result
from cs.debug import trace
from cs.env import envsub
from cs.lex import as_lines
from cs.logutils import exception, error, warning, debug, Pfx, D, X
from cs.queues import IterableQueue
from cs.range import Range
from cs.threads import locked, locked_property
from cs.timeutils import TimeoutError
from cs.obj import O
from cs.py3 import ustr, bytes

DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_READSIZE = 8192
DEFAULT_TAIL_PAUSE = 0.25

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
  except Exception:
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

@contextmanager
def rewrite_cmgr(pathname,
            mode='w',
            backup_ext=None,
            keep_backup=False,
            do_rename=False,
            do_diff=None,
            empty_ok=False,
            overwrite_anyway=False):
  ''' Rewrite a file, presented as a context manager.
      `mode`: file write mode, defaulting to "w" for text.
      `backup_ext`: backup extension. None means no backup.
            An empty string generates an extension based on the current time.
      `keep_backup`: keep the backup file even if everything works.
      `do_rename`: rename the temporary file to the original to update.
      `do_diff`: call do_diff(pathname, tempfile) before commiting.
      `empty_ok`: do not consider empty output an error.
      `overwrite_anyway`: do not update the original if the new data are identical.
  '''
  if backup_ext is None:
    backuppath = None
  else:
    if len(backup_ext) == 0:
      backup_ext = '.bak-%s' % (datetime.datetime.now().isoformat(),)
    backuppath = pathname + backup_ext
  dirpath = dirname(pathname)

  T = NamedTemporaryFile(mode=mode, dir=dirpath, delete=False)
  # hand control to caller
  try:
    yield T
    T.flush()
    if not empty_ok and os.fstat(T.fileno()).st_size == 0:
      raise ValueError("empty file")
  except Exception as e:
    # failure from caller or flush or sanity check, clean up
    try:
      os.unlink(T.name)
    except OSError as e2:
      if e2.errno != errno.ENOENT:
        warning("%s: unlink: %s", T.name, e2)
    raise e

  # success
  if not overwrite_anyway and compare(pathname, T.name):
    # file unchanged, remove temporary
    os.unlink(T.name)
    return

  if do_rename:
    if backuppath is not None:
      os.rename(pathname, backuppath)
    os.rename(T.name, pathname)
  else:
    if backuppath is not None:
      shutil.copy2(pathname, backuppath)
    shutil.copyfile(T.name, pathname)
  if backuppath and not keep_backup:
    os.remove(backuppath)

def abspath_from_file(path, from_file):
  ''' Return the absolute path of `path` with respect to `from_file`,
      as one might do for an include file.
  '''
  if not isabspath(path):
    if not isabspath(from_file):
      from_file = abspath(from_file)
    path = joinpath(dirname(from_file), path)
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

def make_file_property(attr_name=None, unset_object=None, poll_rate=DEFAULT_POLL_INTERVAL):
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
            exception("exception during poll_file, leaving .%s untouched", attr_value)
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

def make_files_property(attr_name=None, unset_object=None, poll_rate=DEFAULT_POLL_INTERVAL):
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
              debug("exception reloading .%s, keeping cached value: %s", attr_value, e)
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
def lockfile(path, ext=None, poll_interval=None, timeout=None):
  ''' A context manager which takes and holds a lock file.
      `path`: the base associated with the lock file.
      `ext`: the extension to the base used to construct the lock file name.
             Default: ".lock"
      `timeout`: maximum time to wait before failing,
                 default None (wait forever).
      `poll_interval`: polling frequency when timeout is not 0.
  '''
  if poll_interval is None:
    poll_interval = DEFAULT_POLL_INTERVAL
  if ext is None:
    ext = '.lock'
  if timeout is not None and timeout < 0:
    raise ValueError("timeout should be None or >= 0, not %r" % (timeout,))
  start = None
  lockpath = path + ext
  while True:
    try:
      lockfd = os.open(lockpath, os.O_CREAT|os.O_EXCL|os.O_RDWR, 0)
    except OSError as e:
      if e.errno != errno.EEXIST:
        raise
      if timeout is not None and timeout <= 0:
        # immediate failure
        raise TimeoutError("cs.fileutils.lockfile: pid %d timed out on lockfile %r"
                           % (os.getpid(), lockpath),
                           timeout)
      now = time.time()
      # post: timeout is None or timeout > 0
      if start is None:
        # first try - set up counters
        start = now
        complaint_last = start
        complaint_interval = 2 * max(DEFAULT_POLL_INTERVAL, poll_interval)
      else:
        if now - complaint_last >= complaint_interval:
          warning("cs.fileutils.lockfile: pid %d waited %ds for %r",
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
        raise TimeoutError("cs.fileutils.lockfile: pid %d timed out on lockfile %r"
                           % (os.getpid(), lockpath),
                           timeout)
      time.sleep(sleep_for)
      continue
    else:
      break
  os.close(lockfd)
  yield lockpath
  os.remove(lockpath)

def max_suffix(dirpath, pfx):
  ''' Compute the highest existing numeric suffix for names starting with the prefix `pfx`.
      This is generally used as a starting point for picking a new numeric suffix.
  '''
  pfx=ustr(pfx)
  maxn=None
  pfxlen=len(pfx)
  for e in os.listdir(dirpath):
    e = ustr(e)
    if len(e) <= pfxlen or not e.startswith(pfx):
      continue
    tail = e[pfxlen:]
    if tail.isdigit():
      n=int(tail)
      if maxn is None:
        maxn=n
      elif maxn < n:
        maxn=n
  return maxn

def mkdirn(path, sep=''):
  ''' Create a new directory named path+sep+n, where `n` exceeds any name already present.
      `path`: the basic directory path.
      `sep`: a separator between `path` and n. Default: ""
  '''
  with Pfx("mkdirn(path=%r, sep=%r)", path, sep):
    if os.sep in sep:
      raise ValueError(
              "sep contains os.sep (%r)"
              % (os.sep,))
    opath = path
    if len(path) == 0:
      path = '.' + os.sep

    if path.endswith(os.sep):
      if sep:
        raise ValueError(
                "mkdirn(path=%r, sep=%r): using non-empty sep with a trailing %r seems nonsensical"
                % (path, sep, os.sep))
      dirpath = path[:-len(os.sep)]
      pfx = ''
    else:
      dirpath = dirname(path)
      if len(dirpath) == 0:
        dirpath='.'
      pfx = basename(path)+sep

    if not isdir(dirpath):
      error("parent not a directory: %r", dirpath)
      return None

    # do a quick scan of the directory to find
    # if any names of the desired form already exist
    # in order to start after them
    maxn = max_suffix(dirpath, pfx)
    if maxn is None:
      newn = 0
    else:
      newn = maxn

    while True:
      newn += 1
      newpath = path + sep + str(newn)
      try:
        os.mkdir(newpath)
      except OSError as e:
        if e.errno == errno.EEXIST:
          # taken, try new value
          continue
        error("mkdir(%s): %s", newpath, e)
        return None
      if len(opath) == 0:
        newpath = basename(newpath)
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
  ''' Make a new temporary directory with a numeric suffix.
  '''
  if tmp is None: tmp=tmpdir()
  return mkdirn(joinpath(tmp, basename(sys.argv[0])))

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

def longpath(path, environ=None, prefixes=None):
  ''' Return `path` with prefixes and environment variables substituted.
      The converse of shortpath().
  '''
  if prefixes is None:
    prefixes = DEFAULT_SHORTEN_PREFIXES
  for prefix, subst in prefixes:
    if path.startswith(subst):
      path = prefix + path[len(subst):]
      break
  path = envsub(path, environ)
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
    return Pathname(dirname(self))

  @property
  def basename(self):
    return Pathname(basename(self))

  @property
  def abs(self):
    return Pathname(abspath(self))

  @property
  def isabs(self):
    return isabspath(self)

  @property
  def short(self):
    return self.shorten()

  def shorten(self, environ=None, prefixes=None):
    return shortpath(self, environ=environ, prefixes=prefixes)

class BackedFile(RawIOBase):
  ''' A RawIOBase implementation that uses a backing file for initial data and writes new data to a front file.
  '''

  def __init__(self, back_file, front_file=None):
    ''' Initialise the BackedFile using `back_file` for the backing data and `front_file` to the update data.
    '''
    self._offset = 0
    self._lock = RLock()
    self._reset(back_file, front_file)

  @locked
  def _reset(self, back_file, front_file=None, front_range=None):
    ''' Reset the internal state of the BackedFile.
    '''
    if front_range is None:
      front_range = Range()
    self.back_file = back_file
    self._front_file = front_file
    self.front_range = front_range

  def __enter__(self):
    ''' BackedFile instances offer a context manager that take the lock, allowing synchronous use of the file without implementing a suite of special methods like pread/pwrite.
    '''
    self._lock.acquire()

  def __exit__(self, *e):
    self._lock.release()

  @locked
  def _discard_front_file(self):
    ''' Reset the BackedFile, keeping only the backing file.
    '''
    self._reset(self.back_file)

  @locked_property
  def front_file(self):
    return TemporaryFile()

  def tell(self):
    return self._offset

  @locked
  def seek(self, pos, whence=SEEK_SET):
    if whence == SEEK_SET:
      self._offset = pos
    elif whence == SEEK_CUR:
      self._offset += pos
    elif whence == SEEK_END:
      endpos = self._back_file.seek(0, SEEK_END)
      if self.front_range is not None:
        endpos = max(back_end, self.front_range.end())
      self._offset = endpos
    else:
      raise ValueError("unsupported whence value %r" % (whence,))

  def read_n(self, n):
    ''' Read `n` bytes of data and return them.
        Unlike file.read(), RawIOBase.read() may return short data,
        thus this workalike, which may only return short data if
        it hits EOF.
    '''
    if n < 1:
      raise ValueError("n two low, expected >=1, got %r" % (n,))
    data = bytearray(n)
    nread = self.readinto(data)
    return data[:nread]

  @locked
  def readinto(self, b):
    start = self._offset
    end = start + len(b)
    back_file = self.back_file
    front_file = self.front_file
    boff = 0
    bspace = len(b)
    for in_front, span in self.front_range.slices(start, end):
      offset = span.start
      size = span.size
      if size > bspace:
        size = bspace
      data = bytearray(size)
      if in_front:
        front_file.seek(offset)
        nread = front_file.readinto(data)
      else:
        back_file.seek(offset)
        nread = back_file.readinto(data)
      assert nread <= size
      b[boff:boff+nread] = data[:nread]
      boff += nread
      self._offset = offset + nread
      if nread < size:
        # short read
        break
    return boff

  @locked
  def write(self, b):
    front_file = self.front_file
    start = self._offset
    front_file.seek(start)
    written = front_file.write(b)
    if written is None:
      warning("front_file.write() returned None, assuming %d bytes written, data=%r", len(b), b)
      written = len(b)
    self.front_range.add_span(start, start+written)
    return written

class BackedFile_TestMethods(object):
  ''' Mixin for testing subclasses of BackedFile.
  '''

  def _eq(self, a, b, opdesc):
    ''' Convenience wrapper for assertEqual.
    '''
    ##if a == b:
    ##  print("OK: %s: %r == %r" % (opdesc, a, b), file=sys.stderr)
    self.assertEqual(a, b, "%s: got %r, expected %r" % (opdesc, a, b))

  def test_BackedFile(self):
    from random import randint
    backing_text = self.backing_text
    bfp = self.backed_fp
    # test reading whole file
    bfp.seek(0)
    bfp_text = bfp.read()
    self._eq(backing_text, bfp_text, "backing_text vs bfp_text")
    # test reading first 512 bytes only
    bfp.seek(0)
    bfp_leading_text = bfp.read_n(512)
    self._eq(backing_text[:512], bfp_leading_text, "leading 512 bytes of backing_text vs bfp_leading_text")
    # test writing some data and reading it back
    random_chunk = bytes( randint(0,255) for x in range(256) )
    bfp.seek(512)
    bfp.write(random_chunk)
    # check that the front file has a single span of the right dimensions
    ffp = bfp._front_file
    fr = bfp.front_range
    self.assertIsNotNone(ffp)
    self.assertIsNotNone(fr)
    self.assertEqual(len(fr._spans), 1, "fr._spans = %r" % (fr._spans,))
    self.assertEqual(fr._spans[0].start, 512)
    self.assertEqual(fr._spans[0].end, 768)
    # read the random data back from the front file
    ffp.seek(512)
    front_chunk = ffp.read(256)
    self.assertEqual(random_chunk, front_chunk)
    # read the random data back from the BackedFile
    bfp.seek(512)
    bfp_chunk = bfp.read_n(256)
    self.assertEqual(bfp_chunk, random_chunk)
    # read a chunk that overlaps the old data and the new data
    bfp.seek(256)
    overlap_chunk = bfp.read_n(512)
    self.assertEqual(len(overlap_chunk), 512, "overlap_chunk not 512 bytes: %r" % (overlap_chunk,))
    self.assertEqual(overlap_chunk, backing_text[256:512] + random_chunk)

class SharedAppendFile(object):
  ''' A base class to share a modifiable file between multiple users.

      The use case was driven from the shared CSV files used by
      cs.nodedb.csvdb.Backend_CSVFile, where multiple users can
      read from a common CSV file, and coordinate updates with a
      lock file.

      This presents the following interfaces:

        __iter__: yields data chunks from the underlying file up
          to EOF; it block no more than reading from the file does.
          Note that multiple iterators share the same read pointer.

        open: a context manager returning a writable file for writing
          updates to the file; it blocks reads from this instance
          (though not, of course, by other users of the file) and
          arranges that users of __iter__ do not receive their own
          written data, thus arranging that __iter__ returns only
          foreign file updates.

      Subclasses would normally override __iter__ to parse the
      received data into their natural records.
  '''

  def __init__(self, pathname,
               read_only=False, write_only=False, binary=False,
               lock_ext=None, lock_timeout=None, poll_interval=None):
    ''' Initialise this SharedAppendFile.
        `pathname`: the pathname of the file to open.
        `read_only`: set to true if we will not write updates.
        `write_only`: set to true if we will not read updates.
        `binary`: if the file is to be opened in binary mode, otherwise text mode.
        `lock_ext`: lock file extension.
        `lock_timeout`: maxmimum time to wait for obtaining the lock file.
        `poll_interval`: poll time when taking a lock file,
            default DEFAULT_POLL_INTERVAL
    '''
    with Pfx("SharedAppendFile(%r): __init__", pathname):
      if poll_interval is None:
        poll_interval = DEFAULT_POLL_INTERVAL
      self.pathname = abspath(pathname)
      self.binary = binary
      self.read_only = read_only
      self.write_only = write_only
      self.lock_ext = lock_ext
      self.lock_timeout = lock_timeout
      self.poll_interval = poll_interval
      if self.read_only:
        if self.write_only:
          raise ValueError("only one of read_only and write_only may be true")
        o_flags = O_RDONLY
      elif self.write_only:
        o_flags = O_WRONLY | O_APPEND
      else:
        o_flags = O_RDWR | O_APPEND
      self._fd = os.open(self.pathname, o_flags)
      self._read_offset = 0
      self._read_skip = Range()
      self._readlock = RLock()
      if not self.write_only:
        self._readopen()
      self.closed = False

  def __str__(self):
    return "SharedAppendFile(%r)" % (self.pathname,)

  def close(self):
    ''' Close the SharedAppendFile: close input queue, wait for monitor to terminate.
    '''
    if self.closed:
      warning("multiple close of %s", self)
    self.closed = True

  def _readopen(self, skip_to_end=False):
    ''' Open the file for read.
    '''
    assert not self.write_only
    mode = 'rb' if self.binary else 'r'
    fd = dup(self._fd)
    self._rfp = fdopen(fd, mode, buffering=0)
    self.open_state = self.filestate

  def _readclose(self):
    ''' Close the reader.
    '''
    assert not self.write_only
    rfp = self._rfp
    self._rfp = None
    rfp.close()
    self.closed = True

  def __iter__(self):
    ''' Iterate over the file, yielding data chunks until EOF.
        This skips data written to the file by this instance so that
        the data chunks returned are always foreign updates.
        Note that all iterators share the same file offset pointer.

        Usage:
          for chunk in f:
            ... process chunk ...
    '''
    assert not self.write_only
    while True:
      with self._readlock:
        # advance over any skip areas
        offset = self._read_offset
        skip = self._read_skip
        while skip and skip.start <= offset:
          start0, end0 = skip.span0
          if offset < end0:
            offset = end0
          skip.discard(start0, end0)
        read_size = DEFAULT_READSIZE
        if skip:
          read_size = min(read_size, skip.span0.start - offset)
          assert read_size > 0
        # gather data
        self._rfp.seek(offset)
        bs = self._rfp.read(read_size)
        self._read_offset = self._rfp.tell()
      if not bs:
        break
      yield bs

  def _lockfile(self):
    ''' Obtain an exclusive write lock on the CSV file.
        This arranges that multiple instances can coordinate writes.

        Usage:
          with self._lockfile():
            ... write data ...
    '''
    return lockfile(self.pathname,
                    ext=self.lock_ext,
                    poll_interval=self.poll_interval,
                    timeout=self.lock_timeout)

  @contextmanager
  def open(self):
    ''' Open the file for append write, returing a writable file.
        Iterators are blocked for the duration of the context manager.
    '''
    if self.read_only:
      raise RuntimeError("attempt to write to read only SharedAppendFile")
    with self._lockfile():
      with self._readlock:
        mode = 'ab' if self.binary else 'a'
        fd = dup(self._fd)
        with fdopen(fd, mode) as wfp:
          wfp.seek(0, SEEK_END)
          start = wfp.tell()
          yield wfp
          end = wfp.tell()
        if end > start:
          sel._read_skip.add(start, end)
        if not self.write_only:
          self._readopen()

  def tail(self):
    ''' A generator returning data chunks from the file indefinitely.
        This supports writing monitors for file updates.
        Note that this, like other iterators, shares the same file offset pointer.
        Also note that it calls the class' iterator, so that if s
        aubsclass returns higher level records from its iterator,
        those records will also be returned from tail.

        Usage:
          for chunk in f:
            ... process chunk ...
    '''
    while True:
      for item in self:
        yield item
      if self.closed:
        return
      sleep(DEFAULT_TAIL_PAUSE)

  @property
  def filestate(self):
    ''' The current FileState of the backing file.
    '''
    fd = self._fd
    if fd is None:
      return None
    return FileState(_fd)

  # TODO: need to notice filestate changes in other areas
  # TODO: support in place rewrite?
  @contextmanager
  def rewrite(self):
    ''' Context manager for rewriting the file.
        This writes data to a new file which is then renamed onto the original.
        After the switch, the read pointer is set to the end of the new file.
        Usage:
          with f.rewrite() as wfp:
            ... write data to wfp ...
    '''
    X("%s.rewrite...", self)
    with self._readlock:
      with self.open() as wfp:
        tmpfp = mkstemp(dir=dirname(self.pathname), text=self.binary)
        yield tmpfp
        if not self.write_only:
          self._read_offset = tmpfp.tell()
        tmpfp.close()
        os.rename(tmpfp, self.pathname)
    X("%s.rewrite DONE", self)

class SharedAppendLines(SharedAppendFile):
  ''' A line oriented subclass of SharedAppendFile.
  '''

  def __init__(self, *a, **kw):
    if 'binary' in kw:
      raise ValueError('may not specify binary=')
    else:
      kw['binary'] = False
    super().__init__(*a, **kw)

  def __iter__(self):
    for line in as_lines(super().__iter__()):
      yield line

def chunks_of(fp, rsize=16384):
  ''' Generator to present text or data from an open file until EOF.
  '''
  while True:
    chunk = fp.read(rsize)
    if len(chunk) == 0:
      break
    yield chunk

def lines_of(fp, partials=None):
  ''' Generator yielding lines from a file until EOF.
      Intended for file-like objects that lack a line iteration API.
  '''
  if partials is None:
    partials = []
  return as_lines(chunks_of(fp), partials)

if __name__ == '__main__':
  import cs.fileutils_tests
  cs.fileutils_tests.selftest(sys.argv)
