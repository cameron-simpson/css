#!/usr/bin/python
#
# Assorted convenience functions for files and filenames/pathnames.
#       - Cameron Simpson <cs@cskk.id.au>
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
    'requires': ['cs.result', 'cs.debug', 'cs.deco', 'cs.env', 'cs.logutils', 'cs.queues', 'cs.range', 'cs.threads', 'cs.timeutils', 'cs.py3'],
}

from contextlib import contextmanager
import datetime
import errno
import os
from os import SEEK_CUR, SEEK_END, SEEK_SET
from os.path import basename, dirname, isdir, isabs as isabspath, \
                    abspath, join as joinpath
import shutil
import stat
import sys
from tempfile import TemporaryFile, NamedTemporaryFile, mkstemp
from threading import Lock, RLock
import time
from cs.deco import cached, decorator
from cs.env import envsub
from cs.filestate import FileState
from cs.lex import as_lines
from cs.logutils import error, warning, debug
from cs.pfx import Pfx
from cs.py3 import ustr, bytes
from cs.range import Range
from cs.threads import locked
from cs.timeutils import TimeoutError
from cs.x import X

DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_READSIZE = 131072
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
      shutil.copymode(filepath, T.name)
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

@decorator
def file_based(func, attr_name=None, filename=None, poll_delay=None, sig_func=None, **dkw):
  ''' A decorator which caches a value obtained from a file.
      In addition to all the keyword arguments for @cs.deco.cached,
      this decorator also accepts the following argument:
      `filename`: the filename to monitor. Default from the
        ._{attr_name}__filename attribute. This value will be passed
        to the method as the `filename` keyword parameter.
      If the `poll_delay` is not specified is defaults to `DEFAULT_POLL_INTERVAL`.
      If the `sig_func` is not specified it defaults to
        cs.filestate.FileState({filename}).
      If the decorated function raises OSError with errno == ENOENT,
      this returns None. Other exceptions are reraised.
  '''
  if attr_name is None:
    attr_name = func.__name__
  filename_attr = '_' + attr_name + '__filename'
  filename0 = filename
  if poll_delay is None:
    poll_delay = DEFAULT_POLL_INTERVAL
  sig_func = dkw.pop('sig_func', None)
  if sig_func is None:
    def sig_func(self):
      filename = filename0
      if filename is None:
        filename = getattr(self, filename_attr)
      return FileState(filename, missing_ok=True)
  def wrap0(self, *a, **kw):
    filename = kw.pop('filename', None)
    if filename is None:
      if filename0 is None:
        filename = getattr(self, filename_attr)
      else:
        filename = filename0
    kw['filename'] = filename
    try:
      return func(self, *a, **kw)
    except OSError as e:
      if e.errno == errno.ENOENT:
        return None
      raise
  dkw['attr_name'] = attr_name
  dkw['poll_delay'] = poll_delay
  dkw['sig_func'] = sig_func
  return cached(wrap0, **dkw)

@decorator
def file_property(func, **dkw):
  ''' A property whose value reloads if a file changes.
  '''
  return property(file_based(func, **dkw))

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
    return isabspath(self)

  @property
  def short(self):
    return self.shorten()

  def shorten(self, environ=None, prefixes=None):
    return shortpath(self, environ=environ, prefixes=prefixes)

class ReadMixin(object):
  ''' Useful read methods to accodmodate modes not necessarily available in a class.
  '''

  def read_n(self, n):
    ''' Read `n` bytes of data and return them.
        Unlike traditional file.read(), RawIOBase.read() may return short
        data, thus this workalike, which may only return short data if it
        hits EOF.
    '''
    if n < 1:
      raise ValueError("n two low, expected >=1, got %r" % (n,))
    data = bytearray(n)
    nread = self.readinto(data)
    return memoryview(data)[:nread] if nread != n else data

  def read_natural(self, n, rsize=None):
    ''' A generator that yields data from the file in its natural blocking if possible.
        `n`: the maximum number of bytes to read.
        `rsize`: read size hint if relevant.
        WARNING: this function may move the current file offset.
        This function should be overridden by classes with natural
        direct and efficient data streams. Example override cases
        include a BackedFile which has natural blocks from the front
        and back files and a CornuCopyBuffer which has natural
        blocks from its source iterator.
    '''
    return file_data(self, n, rsize=None)

  def read(self, n):
    ''' Do a single potentially short read.
    '''
    for bs in self.file_data(n):
      return bs

  @locked
  def readinto(self, barray):
    ''' Read data into a bytearray.
        Uses read_natural to obtain data in as efficient a fashion as possible.
    '''
    X("readinto(barray=%s:len=%d)", id(barray), len(barray))
    needed = len(barray)
    boff = 0
    for bs in self.read_natural(needed):
      bs_len = len(bs)
      X("readinfo: got %d bytes from read_natural(needed=%d)", bs_len, needed)
      assert bs_len <= needed
      boff2 = boff + bs_len
      barray[boff:boff2] = bs
      boff = boff2
      needed -= bs_len
    X("readinto: final boff=%d", boff)
    return boff

class BackedFile(ReadMixin):
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

  def __len__(self):
    back_file = self.back_file
    try:
      back_len = len(back_file)
    except TypeError:
      back_pos = back_file.tell()
      X("BackedFile.__len__: tell=%d", back_pos)
      back_len = back_file.seek(0, 2)
      X("BackedFile.__len__: seek(0,2)=%d", back_len)
      back_file.seek(back_pos, 0)
      X("BackedFile.__len__: after seek(%d,0), tell=%d", back_pos, back_file.tell())
    return max(self.front_range.end, back_len)

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
        endpos = max(len(self.back_file), self.front_range.end())
      self._offset = endpos
    else:
      raise ValueError("unsupported whence value %r" % (whence,))

  def file_data(self, n, rsize=None):
    ''' Return "natural" file data.
        This is used to support readinto efficiently.
    '''
    X("file_data: n=%d", n)
    if n < 1:
      return
    start = self._offset
    end = start + n
    back_file = self.back_file
    front_file = self.front_file
    X("front_range=%s", self.front_range)
    SLICES = list(self.front_range.slices(start, end))
    X("front_range slices=%r", SLICES)
    for in_front, span in SLICES:
      X("offset=%d, in_front=%s, span=%s", self._offset, in_front, span)
      assert span.start == self._offset
      assert span.end <= end
      size = span.size
      src_file = front_file if in_front else back_file
      X("seek %d", self._offset)
      src_file.seek(self._offset)
      try:
        rn = src_file.read_natural
      except AttributeError:
        while size > 0:
          X("read %d ...", size)
          bs = src_file.read(size)
          bs_len = len(bs)
          assert bs_len <= size
          if bs:
            X("bs = %r...", bs[:16])
            self._offset += bs_len
            yield bs
          else:
            X("EMPTY READ, leave loop")
            break
          size -= bs_len
          X("offset => %d", self._offset)
          assert self._offset <= end
      else:
        for bs in rn(size, rsize=rsize):
          self._offset += bs_len
          yield bs
          assert self._offset <= end
      if self._offset < span.end:
        # short data, infer EOF, exit loop
        break
      raise RuntimeError("BANG")
    X("EXIT file_data")

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
  return as_lines(read_from(fp), partials)

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
