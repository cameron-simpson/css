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
    'install_requires': ['cs.asynchron', 'cs.debug', 'cs.env', 'cs.lex', 'cs.logutils', 'cs.queues', 'cs.range', 'cs.threads', 'cs.timeutils', 'cs.obj', 'cs.py3'],
}

from io import RawIOBase
from functools import partial
import os
from os import SEEK_CUR, SEEK_END, SEEK_SET
from os.path import basename, dirname, isabs, isdir, \
                    abspath, join as joinpath, exists as existspath
import errno
import sys
from collections import namedtuple
from contextlib import contextmanager
from itertools import takewhile
import shutil
import socket
import stat
from tempfile import TemporaryFile, NamedTemporaryFile
from threading import RLock, Thread
import time
import unittest
from cs.asynchron import Result
from cs.debug import trace
from cs.env import envsub
from cs.lex import as_lines
from cs.logutils import error, warning, debug, Pfx, D, X
from cs.queues import IterableQueue
from cs.range import Range
from cs.threads import locked, locked_property
from cs.timeutils import TimeoutError
from cs.obj import O
from cs.py3 import ustr, bytes

try:
  from os import pread
except ImportError:
  # implement our own pread
  # NB: not thread safe!
  def pread(fd, size, offset):
    offset0 = os.lseek(fd, 0, SEEK_CUR)
    os.lseek(fd, offset, SEEK_SET)
    chunks = []
    while size > 0:
      data = os.read(fd, size)
      if len(data) == 0:
        break
      chunks.append(data)
      size -= len(data)
    os.lseek(fd, offset0, SEEK_SET)
    data = b''.join(chunks)
    return data

def fdreader(fd, readsize=None):
  ''' Generator yielding data chunks from a file descriptor until EOF.
  '''
  if readsize is None:
    readsize = 1024
  while True:
    bs = os.read(fd, readsize)
    if not bs:
      break
    yield bs

def seekable(fp):
  ''' Try to test if a filelike object is seekable.
      First try the .seekable method from IOBase, otherwise try
      getting a file descriptor from fp.fileno and stat()ing that,
      otherwise return False.
  '''
  try:
    test = fp.seekable
  except AttributeError:
    try:
      getfd = fp.fileno
    except AttributeError:
      return False
    test = lambda: stat.S_ISREG(os.fstat(getfd()).st_mode)
  return test()

DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_READSIZE = 8192

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
  if do_rename:
    tmpdir = dirname(filepath)
  else:
    tmpdir = None
  with NamedTemporaryFile(mode=mode, dir=tmpdir) as T:
    if isinstance(data, list):
      I = data
    else:
      I = read_from(data)
    for chunk in I:
      T.write(chunk)
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
  if not isabs(path):
    if not isabs(from_file):
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

def makelockfile(path, ext=None, poll_interval=None, timeout=None):
  ''' Create a lockfile and return its path.
      The file can be removed with os.remove.
      This is the core functionality supporting the lockfile()
      context manager.
      `path`: the base associated with the lock file, often the
              filesystem object whose access is being managed.
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
          from cs.logutils import warning
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
  return lockpath

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
  lockpath = makelockfile(path, ext=ext, poll_interval=poll_interval, timeout=timeout)
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
    return isabs(self)

  @property
  def short(self):
    return self.shorten()

  def shorten(self, environ=None, prefixes=None):
    return shortpath(self, environ=environ, prefixes=prefixes)

class BackedFile(object):
  ''' A RawIOBase duck type that uses a backing file for initial data and writes new data to a front file.
  '''

  def __init__(self, back_file, front_file=None):
    ''' Initialise the BackedFile using `back_file` for the backing data.
    '''
    self._offset = 0
    self._lock = RLock()
    self._reset(back_file, new_front_file=front_file)

  @locked
  def _reset(self, new_back_file, new_front_file):
    ''' Reset the state of this BackedFile.
        Set the front_file and back_file.
        Note that the former front abd back files are not closed.
    '''
    if new_front_file is None:
      new_front_file = TemporaryFile()
      self._need_close_front = True
    else:
      self._need_close_front = False
    self.back_file = new_back_file
    self.front_file = new_front_file
    self.front_range = Range()

  @locked
  def push_file(self, new_front_file=None):
    ''' Push a new front file onto this BackedFile. Return the new back file.
        If `new_front_file` is None, allocate a TemporaryFile.
    '''
    if new_front_file is None:
      new_front_file = TemporaryFile()
    new_back_file = BackedFile(self.back_file, front_file=new_front_file)
    self._reset(new_back_file, new_front_file)
    return self.back_file

  @locked
  def switch_back_file(self, new_back_file):
    ''' Switch out one back file for another. Return the old back file.
    '''
    old_back_file = self.back_file
    self.back_file = new_back_file
    return old_back_file

  def __enter__(self):
    ''' BackedFile instances offer a context manager that takes the lock, allowing synchronous use of the file without implementing a suite of special methods like pread/pwrite.
    '''
    self._lock.acquire()

  def __exit__(self, *e):
    self._lock.release()

  def close(self):
    self.flush()
    if self._need_close_front:
      self.front_file.close()
    self.front_file = None

  def tell(self):
    return self._offset

  @locked
  def flush(self):
    self.front_file.flush()

  @locked
  def __len__(self):
    ''' Length of the file: max(len(backing_file), len(front_file)).
    '''
    return max(self.back_file.seek(0, SEEK_END), self.front_range.end)

  @locked
  def seek(self, pos, whence=SEEK_SET):
    if whence == SEEK_SET:
      self._offset = pos
    elif whence == SEEK_CUR:
      self._offset += pos
    elif whence == SEEK_END:
      self._offset = len(self) + pos
    else:
      raise ValueError("unsupported whence value %r" % (whence,))
    return self._offset

  def spans(self):
    ''' Yield (in_front, span) for all spans from 0 to len(self).
    '''
    return self.front_range.spans(0, len(self))

  def front_spans(self):
    ''' Yield span for all spans from 0 to len(self) in the front_file.
    '''
    for in_front, span in self.spans():
      if in_front:
        yield span

  def back_spans(self):
    ''' Yield span for all spans from 0 to len(self) in the back_file.
    '''
    for in_front, span in self.spans():
      if not in_front:
        yield span

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
    if nread == n:
      # do not copy the buffer if we got all the requested data
      return data
    # return short copy
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
    ffp = bfp.front_file
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

      The use case derives from the shared CSV files used by
      cs.nodedb.csvdb.Backend_CSVFile, where multiple users can
      read from a common CSV file, and coordinate updates with a
      lock file.

      Subclasses need to implement one method:

      transcribe_update(self, fp, item):
        fp    the output file
        item  the output record, to be serialised to the file

      Users of the class or subclass who need to receive foreign
      updates need to supply the `importer` parameter, a callable
      which is handed a foreign record from the file to incorporate
      into their own state. They must be prepared to accept the
      value None, which is a marker for seeing EOF on the backing
      file; it is sent unconditionally on the first scan of the
      file and then whenever a scan of the file receives additional
      data.

      Sunclasses which emit higher order records, such as
      SharedAppendLines below, will need to intercept the `importer`
      paramater and substitute one of their own which constructs
      records from the lower order data received from the superclass.
      For example, here is the for in SharedAppendLines.__init__
      for this purpose:

        importer = kw.get('importer')
        if importer is not None:
          kw['importer'] = lambda chunk: self._linearise(chunk, importer)

      The ._linearise method accepts data chunks from the
      SharedAppendFile superclass and passes completed text lines
      to the supplied `importer`, keeping the state of incomplete
      line data between calls.

      Note that the intercepting importer passes through None values
      immediately; they indicate only that more raw data has been
      gathered from the backing file, not that a complete record
      has been assembled for import. For example,
      SharedAppendLines._linearise starts like this:

        if chunk is None:
          importer(None)
        else:
          ... gather chunk into lines ...

      Therefore real importers and intercepting importers must be
      prepared to accept None at any time; an interceptor passes
      it through and a real (end user) importer discards it, but
      may use the receipt of None to update state such as determining
      that the first scan of the backing file has completed or to
      update some progress/activity indicator.
  '''

  DEFAULT_MAX_QUEUE = 128

  def __init__(self, pathname, no_update=False, importer=None,
                binary=False, max_queue=None,
                poll_interval=None,
                lock_ext=None, lock_timeout=None):
    ''' Initialise this SharedAppendFile.
        `pathname`: the pathname of the file to open.
        `importer`: callable to accept foreign data chunks as their appear
        `no_update`: set to true if we will not write updates.
        `binary`: if the file is to be opened in binary mode, otherwise text mode.
        `max_queue`: maximum input Queue length. Default: SharedAppendFile.DEFAULT_MAX_QUEUE.
        `poll_interval`: sleep time between polls after an idle poll. Default: DEFAULT_POLL_INTERVAL.
        `lock_ext`: lock file extension.
        `lock_timeout`: maxmimum time to wait for obtaining the lock file.
    '''
    with Pfx("SharedAppendFile(%r): __init__", pathname):
      if max_queue is None:
        max_queue = self.DEFAULT_MAX_QUEUE
      if poll_interval is None:
        poll_interval = DEFAULT_POLL_INTERVAL
      self.pathname = pathname
      self.binary = binary
      self.no_update = no_update
      self.importer = importer
      self.poll_interval = poll_interval
      self.max_queue = max_queue
      self.ready = Result(name="readiness(%s)" % (self,))
      self.lock_ext = lock_ext
      self.lock_timeout = lock_timeout
      if not no_update:
        # inbound updates to be saved to the file
        self._inQ = IterableQueue(self.max_queue, name="%s._inQ" % (self,))
      self._open_fp()

  def __str__(self):
    return "SharedAppendFile(%r)" % (self.pathname,)

  def _open_fp(self):
    ''' Open the file for read or read/write, save open file as .fp.
    '''
    mode = "r" if self.no_update else "r+"
    if self.binary:
      mode += "b"
    self.fp = open(self.pathname, mode)
    self.closed = False
    self._monitor_thread = Thread(target=self._monitor, name="%s._monitor" % (self,))
    self._monitor_thread.daemon = True
    self._monitor_thread.start()

  def close(self):
    ''' Close the SharedAppendFile: close input queue, wait for monitor to terminate.
    '''
    if self.closed:
      warning("multiple close of %s", self)
    self.closed = True
    if not self.no_update:
      self._inQ.close()
    self._monitor_thread.join()
    fp = self.fp
    self.fp = None
    fp.close()

  @property
  def filestate(self):
    ''' The current FileState of the backing file.
    '''
    fp = self.fp
    if fp is None:
      return None
    return FileState(fp.fileno())

  def _lockfile(self):
    ''' Obtain an exclusive lock on the CSV file.
    '''
    return lockfile(self.pathname,
                    ext=self.lock_ext,
                    poll_interval=self.poll_interval,
                    timeout=self.lock_timeout)

  @contextmanager
  def open(self, mode=None):
    ''' Context manager to obtain the lockfile, open the file with the specified mode (default: append), return the open file.
    '''
    if mode is None:
      mode = "ab" if self.binary else "a"
    with self._lockfile():
      self.fp.flush()
      self.fp.close()
      try:
        fp = open(self.pathname, mode)
      except OSError:
        raise
      else:
        yield fp
        fp.close()
      finally:
        self._open_fp()

  def put(self, update_item):
    ''' Queue an update record for transcription to the shared file.
    '''
    self._inQ.put(update_item)

  def _read_to_eof(self, force_eof=False):
    ''' Read update data from the file until EOF, hand data chunks to the importer.
        At EOF put None if at least one chunk was sent or force_eof
        is True (set on the first scan so that end users can know
        when the backing file has completed its initial read).
        Return the number of reads with data; 0 ==> no new data.
    '''
    debug("READ_TO_EOF...")
    count = 0
    for chunk in read_from(self.fp):
      if len(chunk) == 0:
        warning("empty chunk received from read_from(%s)", self.fp)
      else:
        self.importer(chunk)
        count += 1
    if force_eof or count > 0:
      debug("READ_TO_EOF COMPLETE: CHUNK COUNT=%d", count)
      self.importer(None)
    return count

  def _monitor(self):
    ''' Monitor the input queue and the data file.
        Read data from the data file as it appears, copy to the output queue.
        Read updates from the input queue, append to the data file.
    '''
    with Pfx("%s._monitor", self):
      first = True
      # run until closed and no pending outbound updates
      while ( not self.closed
           or ( not self.no_update
            and (not self._inQ.closed or not self._inQ.empty())
              )
            ):
        if self.importer:
          # catch up
          count = self._read_to_eof(force_eof=first)
          if first:
            # indicate first to-EOF read complete, output queue primed
            self.ready.put(True)
        else:
          count = 0
        if not self.no_update:
          # check for outgoing updates
          if self._inQ.empty():
            # sleep briefly if nothing to write out
            if count == 0:
              time.sleep(self.poll_interval)
          else:
            # updates due
            # obtain lock
            #   read until EOF
            #   append updates
            # release lock
            with self._lockfile():
              if self.importer:
                self._read_to_eof()
                pos = self.fp.tell()
              self.fp.seek(0, SEEK_END)
              if self.importer and pos != self.fp.tell():
                warning("update: pos after catch up=%r, pos after SEEK_END=%r", pos, self.fp.tell())
              while not self._inQ.empty():
                item = self._inQ._get()
                self.transcribe_update(self.fp, item)
              self.fp.flush()
        # clear flag for next pass
        first = False

class SharedAppendLines(SharedAppendFile):

  def __init__(self, *a, **kw):
    if 'binary' in kw:
      raise ValueError('may not specify binary=')
    importer = kw.get('importer')
    if importer is not None:
      kw['importer'] = lambda chunk: self._linearise(chunk, importer)
    self._line_partials = []
    SharedAppendFile.__init__(self, *a, binary=False, **kw)

  def _linearise(self, chunk, importer):
    ''' Importer for SharedAppendFile: convert to lines from raw data, pass to real importer.
    '''
    if chunk is None:
      importer(None)
    else:
      partials = self._line_partials
      start = 0
      while True:
        nlpos = chunk.find('\n', start)
        if nlpos < 0:
          break
        line = ''.join( partials + [chunk[start:nlpos+1]] )
        partials[:] = []
        importer(line)
        start = nlpos + 1
      if start < len(chunk):
        partials.append(chunk[start:])

  def transcribe_update(self, fp, s):
    ''' Transcribe a string as a line.
    '''
    # sanity check: we should only be writing between foreign updates
    # and foreign updates should always be complete lines
    if len(self._line_partials):
      warning("%s._transcribe_update while non-empty partials[]: %r",
              self, self._line_partials)
    if '\n' in s:
      raise ValueError("invalid string to transcribe, contains newline: %r" % (s,))
    fp.write(s)
    fp.write('\n')

class Tee(object):
  ''' An object with .write, .flush and .close methods which copies data to multiple output files.
  '''

  def __init__(self, *fps):
    ''' Initialise the Tee; any arguments are taken to be output file objects.
    '''
    self._fps = list(fps)

  def add(self, output):
    self._fps.append(output)

  def write(self, data):
    for fp in self._fps:
      fp.write(data)

  def flush(self):
    for fp in self._fps:
      fp.flush()

  def close(self):
    for fp in self._fps:
      fp.close()
    self._fps = None

@contextmanager
def tee(fp, fp2):
  ''' Context manager duplicating .write and .flush from fp to fp2.
  '''
  def _write(*a, **kw):
    fp2.write(*a, **kw)
    return old_write(*a, **kw)
  def _flush(*a, **kw):
    fp2.flush(*a, **kw)
    return old_flush(*a, **kw)
  old_write = getattr(fp, 'write')
  old_flush = getattr(fp, 'flush')
  fp.write = _write
  fp.flush = _flush
  yield
  fp.write = old_write
  fp.flush = old_flush

class NullFile(object):
  ''' Writable file that discards its input.
      Note that this is _not_ an open of os.devnull; is just discards writes and is not the underlying file descriptor.
  '''

  def __init__(self):
    self.offset = 0

  def write(self, data):
    dlen = len(data)
    self.offset += dlen
    return dlen

  def flush(self):
    pass

def file_data(fp, nbytes, rsize=None):
  ''' Read `nbytes` of data from `fp` and yield the chunks as read.
      If `nbytes` is None, copy until EOF.
      `rsize`: read size, default DEFAULT_READSIZE.
  '''
  if rsize is None:
    rsize = DEFAULT_READSIZE
  ##prefix = "file_data(fp, nbytes=%d)" % (nbytes,)
  copied = 0
  while nbytes is None or nbytes > 0:
    to_read = rsize if nbytes is None else min(nbytes, rsize)
    data = fp.read(to_read)
    if not data:
      if nbytes is not None:
        if copied > 0:
          # no warning of nothing copied - that is immediate end of file - valid
          warning("early EOF: only %d bytes read, %d still to go",
                  copied, nbytes)
      break
    yield data
    copied += len(data)
    nbytes -= len(data)

def copy_data(fpin, fpout, nbytes, rsize=None):
  ''' Copy `nbytes` of data from `fpin` to `fpout`, return the number of bytes copied.
      If `nbytes` is None, copy until EOF.
      `rsize`: read size, default DEFAULT_READSIZE.
  '''
  copied = 0
  for chunk in file_data(fpin, nbytes, rsize):
    fpout.write(chunk)
    copied += len(chunk)
  return copied

def read_data(fp, nbytes, rsize=None):
  ''' Read `nbytes` of data from `fp`, return the data.
      If `nbytes` is None, copy until EOF.
      `rsize`: read size, default DEFAULT_READSIZE.
  '''
  bss = list(file_data(fp, nbytes, rsize))
  if len(bss) == 0:
    return b''
  elif len(bss) == 1:
    return bss[0]
  else:
    return b''.join(bss)

def read_from(fp, rsize=None):
  ''' Generator to present text or data from an open file until EOF.
  '''
  if rsize is None:
    rsize = DEFAULT_READSIZE
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
  return as_lines(read_from(fp), partials)

class SavingFile(object):
  ''' A simple file-like object with .write and .close methods used
      to accrue a file, and a .cancel method to be used instead of
      .close to discard the file.
      The originating use case is a cache file for an HTTP response;
      if the response ends up incomplete the cache file is discarded.
  '''

  def __init__(self, path, text=False, dir=None):
    ''' Open the scratch file.
    '''
    self.path = path
    if tmpdir is None:
      # try to make the temporary file in the same directory as the
      # target path
      tmpdir = dirname(path)
      if not isdir(tmpdir):
        # fall back to the default
        tmpdir = None
    self.fd, self.tmppath = mkstemp(prefix='.tmp', dir=dir, text=text)
    self.fp = os.fdopen(fd, ( 'w' if text else 'wb' ) )

  def write(self, data):
    ''' Transcribe data to the temp file.
    '''
    return self.fp.write(data)

  def flush(self):
    ''' Flush buffered data to the temp file.
    '''
    return self.fp.flush()

  def cancel(self):
    ''' Cancel the transcription to the temp file and discard.
    '''
    self.fp.close()
    self.fp = None
    os.remove(self.tmppath)

  def close(self):
    ''' Close the output and move the file into place as the cached response.
    '''
    self.fp.close()
    self.fp = None
    path = self.path
    if exsistspath(path):
      warning("replacing existing %r", path)
    os.rename(self.tmppath, path)

if __name__ == '__main__':
  import cs.fileutils_tests
  cs.fileutils_tests.selftest(sys.argv)
