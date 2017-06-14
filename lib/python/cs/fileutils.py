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
from os import dup, fdopen, \
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
import os
import shutil
import socket
import stat
from tempfile import TemporaryFile, NamedTemporaryFile, mkstemp
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

class BackedFile(object):
  ''' A RawIOBase duck type that uses a backing file for initial data and writes new data to a front scratch file.
  '''

  def __init__(self, back_file, dirpath=None):
    ''' Initialise the BackedFile using `back_file` for the backing data.
    '''
    self._offset = 0
    self._dirpath = dirpath
    self._lock = RLock()
    self.back_file = back_file
    self.front_file = TemporaryFile(dir=dirpath, buffering=0)
    self.front_range = Range()
    self.read_only = False

  @locked
  def switch_back_file(self, new_back_file):
    ''' Switch out one back file for another. Return the old back file.
    '''
    old_back_file = self.back_file
    self.back_file = new_back_file
    return old_back_file

  def __enter__(self):
    ''' BackedFile instances offer a context manager that take the lock, allowing synchronous use of the file without implementing a suite of special methods like pread/pwrite.
    '''
    self._lock.acquire()

  def __exit__(self, *e):
    self._lock.release()

  def close(self):
    ''' Close the BackedFile.
        Flush contents. Close the front_file if necessary.
    '''
    self.front_file.close()
    self.front_file = None

  def tell(self):
    ''' Report the current file pointer offset.
    '''
    return self._offset

  @locked
  def seek(self, pos, whence=SEEK_SET):
    ''' Adjust the current file pointer offset.
    '''
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
    ''' Read data into a bytearray.
    '''
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
    ''' Write data to the front_file.
    '''
    if self.read_only:
      raise RuntimeError("write to read-only BackedFile")
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
    bfp_text = bfp.read_n(len(bfp))
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

def read_from(fp, rsize=None, tail_mode=False, tail_delay=None):
  ''' Generator to present text or data from an open file until EOF.
      `rsize`: read size, default: DEFAULT_READSIZE
      `tail_mode`: yield an empty chunk at EOF, allowing resumption
        of the file grows
  '''
  if rsize is None:
    rsize = DEFAULT_READSIZE
  if tail_delay is None:
    tail_delay = DEFAULT_TAIL_PAUSE
  elif not tail_mode:
    raise ValueError("tail_mode=%r but tail_delay=%r" % (tail_mode, tail_delay))
  while True:
    chunk = fp.read(rsize)
    if len(chunk) == 0:
      if tail_mode:
        yield chunk
        time.sleep(tail_delay)
      else:
        break
    else:
      yield chunk

def lines_of(fp, partials=None):
  ''' Generator yielding lines from a file until EOF.
      Intended for file-like objects that lack a line iteration API.
  '''
  if partials is None:
    partials = []
  return as_lines(chunks_of(fp), partials)

class RWFileBlockCache(object):
  ''' A scratch file for storing data.
  '''

  def __init__(self, pathname=None, dirpath=None, suffix=None, lock=None):
    ''' Initialise the file.
        `pathname`: path of file. If None, create a new file with
          tempfile.mkstemp using dir=`dirpath` and unlink that file once
          opened.
        `dirpath`: location for the file if made by mkstemp as above.
        `lock`: an object to use as a mutex, allowing sharing with
          some outer system. A Lock will be allocated if omitted.
    '''
    opathname = pathname
    X("dirpath=%r,suffix=%r", dirpath, suffix)
    if pathname is None:
      tmpfd, pathname = mkstemp(dir=dirpath, suffix=None)
    self.rfd = os.open(pathname, os.O_RDONLY)
    self.wfd = os.open(pathname, os.O_WRONLY)
    if opathname is None:
      os.remove(pathname)
      os.close(tmpfd)
      self.pathname = None
    else:
      self.pathname = pathname
    if lock is None:
      lock = Lock()
    self._lock = lock

  def close(self):
    ''' Close the file descriptors.
    '''
    os.close(self.wfd)
    self.wfd = None
    os.close(self.rfd)
    self.rfd = None

  @property
  def closed(self):
    return self.wfd is None

  def put(self, data):
    ''' Store `data`, return offset.
    '''
    assert len(data) > 0
    wfd = self.wfd
    with self._lock:
      offset = os.lseek(wfd, 0, 1)
      length = os.write(wfd, data)
    assert length == len(data)
    return offset

  def get(self, offset, length):
    ''' Get data from `offset` of length `length`.
    '''
    assert length > 0
    rfd = self.rfd
    with self._lock:
      os.lseek(rfd, offset, 0)
      data = os.read(rfd, length)
    assert len(data) == length
    return data

if __name__ == '__main__':
  import cs.fileutils_tests
  cs.fileutils_tests.selftest(sys.argv)
