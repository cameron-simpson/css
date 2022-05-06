#!/usr/bin/python
#
# Assorted convenience functions for files and filenames/pathnames.
# - Cameron Simpson <cs@cskk.id.au>

''' My grab bag of convenience functions for files and filenames/pathnames.
'''

# pylint: disable=too-many-lines

from __future__ import with_statement, print_function, absolute_import
from contextlib import contextmanager
import errno
from functools import partial
import gzip
import os
from os import SEEK_CUR, SEEK_END, SEEK_SET, O_RDONLY, read, rename
try:
  from os import pread
except ImportError:
  pread = None
from os.path import (
    abspath,
    basename,
    dirname,
    exists as existspath,
    isabs as isabspath,
    isdir,
    join as joinpath,
    splitext,
)
import shutil
import stat
import sys
from tempfile import TemporaryFile, NamedTemporaryFile, mkstemp
from threading import Lock, RLock
import time
from cs.buffer import CornuCopyBuffer
from cs.deco import cachedmethod, decorator, fmtdoc, strable
from cs.env import envsub
from cs.filestate import FileState
from cs.fs import longpath, shortpath
from cs.gimmicks import TimeoutError
from cs.lex import as_lines, cutsuffix, common_prefix
from cs.logutils import error, warning, debug
from cs.pfx import Pfx, pfx_call
from cs.progress import Progress, progressbar
from cs.py3 import ustr, bytes, pread  # pylint: disable=redefined-builtin
from cs.range import Range
from cs.result import CancellationError
from cs.threads import locked
from cs.units import BINARY_BYTES_SCALE

__version__ = '20220429-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.buffer',
        'cs.deco',
        'cs.env',
        'cs.filestate',
        'cs.fs>=longpath,shortpath',
        'cs.gimmicks>=TimeoutError',
        'cs.lex>=20200914',
        'cs.logutils',
        'cs.pfx>=pfx_call',
        'cs.progress',
        'cs.py3',
        'cs.range',
        'cs.result',
        'cs.threads',
        'cs.units',
    ],
}

DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_READSIZE = 131072
DEFAULT_TAIL_PAUSE = 0.25

def seekable(fp):
  ''' Try to test whether a filelike object is seekable.

      First try the `IOBase.seekable` method, otherwise try getting a file
      descriptor from `fp.fileno` and `os.stat()`ing that,
      otherwise return `False`.
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
  ''' Rename a path using `os.rename()`,
      but raise an exception if the target path already exists.
      Note: slightly racey.
  '''
  try:
    os.lstat(newpath)
    raise OSError(errno.EEXIST)
  except OSError as e:
    if e.errno != errno.ENOENT:
      raise
    os.rename(oldpath, newpath)

def trysaferename(oldpath, newpath):
  ''' A `saferename()` that returns `True` on success,
      `False` on failure.
  '''
  try:
    saferename(oldpath, newpath)
  except OSError:
    return False
  ##except Exception:
  ##  raise
  return True

def compare(f1, f2, mode="rb"):
  ''' Compare the contents of two file-like objects `f1` and `f2` for equality.

      If `f1` or `f2` is a string, open the named file using `mode`
      (default: `"rb"`).
  '''
  if isinstance(f1, str):
    with open(f1, mode) as f1fp:
      return compare(f1fp, f2, mode)
  if isinstance(f2, str):
    with open(f2, mode) as f2fp:
      return compare(f1, f2fp, mode)
  return f1.read() == f2.read()

# pylint: disable=too-many-locals,too-many-branches,too-many-statements
@contextmanager
def NamedTemporaryCopy(f, progress=False, progress_label=None, **kw):
  ''' A context manager yielding a temporary copy of `filename`
      as returned by `NamedTemporaryFile(**kw)`.

      Parameters:
      * `f`: the name of the file to copy, or an open binary file,
        or a `CornuCopyBuffer`
      * `progress`: an optional progress indicator, default `False`;
        if a `bool`, show a progress bar for the copy phase if true;
        if an `int`, show a progress bar for the copy phase
        if the file size equals or exceeds the value;
        otherwise it should be a `cs.progress.Progress` instance
      * `progress_label`: option progress bar label,
        only used if a progress bar is made
      Other keyword parameters are passed to `tempfile.NamedTemporaryFile`.
  '''
  if isinstance(f, str):
    # copy named file
    filename = f
    progress_label = (
        "copy " + repr(filename) if progress_label is None else progress_label
    )
    # should we use shutil.copy() and display no progress?
    if progress is False:
      fast_mode = True
    else:
      with Pfx("stat(%r)", filename):
        S = os.stat(filename)
      fast_mode = stat.S_ISREG(S.st_mode)
    if fast_mode:
      with NamedTemporaryFile(**kw) as T:
        with Pfx("shutil.copy(%r,%r)", filename, T.name):
          shutil.copy(filename, T.name)
        yield T
    else:
      with Pfx("open(%r)", filename):
        with open(filename, 'rb') as f2:
          with NamedTemporaryCopy(f2, progress=progress,
                                  progress_label=progress_label, **kw) as T:
            yield T
    return
  prefix = kw.pop('prefix', None)
  if prefix is None:
    prefix = 'NamedTemporaryCopy'
  # prepare the buffer and try to infer the length
  if isinstance(f, CornuCopyBuffer):
    length = None
    bfr = f
  else:
    if isinstance(f, int):
      fd = f
      bfr = CornuCopyBuffer.from_fd(fd)
    else:
      bfr = CornuCopyBuffer.from_file(f)
      try:
        fd = f.fileno()
      except AttributeError:
        fd = None
    if fd is None:
      length = None
    else:
      S = os.fstat(fd)
      length = S.st_size if stat.S_ISREG(S.st_mode) else None
  # determine whether we need a progress bar
  if isinstance(progress, bool):
    need_bar = progress
    progress = None
  elif isinstance(progress, int):
    need_bar = length is None or length >= progress
    progress = None
  else:
    need_bar = False
    assert isinstance(progress, Progress)
  with NamedTemporaryFile(prefix=prefix, **kw) as T:
    it = (
        bfr if need_bar else progressbar(
            bfr,
            label=progress_label,
            total=length,
            itemlenfunc=len,
            units_scale=BINARY_BYTES_SCALE,
        )
    )
    nbs = 0
    for bs in it:
      while bs:
        nwritten = T.write(bs)
        if progress is not None:
          progress += nwritten
        if nwritten != len(bs):
          warning(
              "NamedTemporaryCopy: %r.write(%d bytes) => %d",
              T.name,
              len(bs),
              nwritten,
          )
          bs = bs[nwritten:]
        else:
          bs = b''
        nbs += nwritten
    bfr.close()
    T.flush()
    if length is not None and nbs != length:
      warning(
          "NamedTemporaryCopy: given length=%s, wrote %d bytes to %r",
          length,
          nbs,
          T.name,
      )
    yield T

# pylint: disable=too-many-arguments
def rewrite(
    filepath,
    srcf,
    mode='w',
    backup_ext=None,
    do_rename=False,
    do_diff=None,
    empty_ok=False,
    overwrite_anyway=False
):
  ''' Rewrite the file `filepath` with data from the file object `srcf`.

      Parameters:
      * `filepath`: the name of the file to rewrite.
      * `srcf`: the source file containing the new content.
      * `mode`: the write-mode for the file, default `'w'` (for text);
        use `'wb'` for binary data.
      * `empty_ok`: if true (default `False`),
        do not raise `ValueError` if the new data are empty.
      * `overwrite_anyway`: if true (default `False`),
        skip the content check and overwrite unconditionally.
      * `backup_ext`: if a nonempty string,
        take a backup of the original at `filepath + backup_ext`.
      * `do_diff`: if not `None`, call `do_diff(filepath,tempfile)`.
      * `do_rename`: if true (default `False`),
        rename the temp file to `filepath`
        after copying the permission bits.
        Otherwise (default), copy the tempfile to `filepath`;
        this preserves the file's inode and permissions etc.
  '''
  with Pfx("rewrite(%r)", filepath):
    with NamedTemporaryFile(dir=dirname(filepath), mode=mode) as T:
      T.write(srcf.read())
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
def rewrite_cmgr(filepath, mode='w', **kw):
  ''' Rewrite a file, presented as a context manager.

      Parameters:
      * `mode`: file write mode, defaulting to "w" for text.

      Other keyword parameters are passed to `rewrite()`.

      Example:

          with rewrite_cmgr(pathname, do_rename=True) as f:
              ... write new content to f ...
  '''
  with NamedTemporaryFile(mode=mode) as T:
    yield T
    T.flush()
    with open(T.name, 'rb') as f:
      rewrite(filepath, mode='wb', srcf=f, **kw)

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
  ''' Watch a file for modification by polling its state as obtained
      by `FileState()`.
      Call `reload_file(path)` if the state changes.
      Return `(new_state,reload_file(path))` if the file was modified
      and was unchanged (stable state) before and after the reload_file().
      Otherwise return `(None,None)`.

      This may raise an `OSError` if the `path` cannot be `os.stat()`ed
      and of course for any exceptions that occur calling `reload_file`.

      If `missing_ok` is true then a failure to `os.stat()` which
      raises `OSError` with `ENOENT` will just return `(None,None)`.
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
def file_based(
    func,
    attr_name=None,
    filename=None,
    poll_delay=None,
    sig_func=None,
    **dkw
):
  ''' A decorator which caches a value obtained from a file.

      In addition to all the keyword arguments for `@cs.deco.cachedmethod`,
      this decorator also accepts the following arguments:
      * `attr_name`: the name for the associated attribute, used as
        the basis for the internal cache value attribute
      * `filename`: the filename to monitor.
        Default from the `._{attr_name}__filename` attribute.
        This value will be passed to the method as the `filename` keyword
        parameter.
      * `poll_delay`: delay between file polls, default `DEFAULT_POLL_INTERVAL`.
      * `sig_func`: signature function used to encapsulate the relevant
        information about the file; default
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
      ''' The default signature function: `FileState(filename,missing_ok=True)`.
      '''
      filename = filename0
      if filename is None:
        filename = getattr(self, filename_attr)
      return FileState(filename, missing_ok=True)

  def wrap0(self, *a, **kw):
    ''' Inner wrapper for `func`.
    '''
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
  return cachedmethod(**dkw)(wrap0)

@decorator
def file_property(func, **dkw):
  ''' A property whose value reloads if a file changes.
  '''
  return property(file_based(func, **dkw))

def files_property(func):
  ''' A property whose value reloads if any of a list of files changes.

      Note: this is just the default mode for `make_files_property`.

      `func` accepts the file path and returns the new value.
      The underlying attribute name is `'_'+func.__name__`,
      the default from `make_files_property()`.
      The attribute *{attr_name}*`_lock` is a mutex controlling access to the property.
      The attributes *{attr_name}*`_filestates` and *{attr_name}*`_paths` track the
      associated file states.
      The attribute *{attr_name}*`_lastpoll` tracks the last poll time.

      The decorated function is passed the current list of files
      and returns the new list of files and the associated value.

      One example use would be a configuration file with recurive
      include operations; the inner function would parse the first
      file in the list, and the parse would accumulate this filename
      and those of any included files so that they can be monitored,
      triggering a fresh parse if one changes.

      Example:

          class C(object):
            def __init__(self):
              self._foo_path = '.foorc'
            @files_property
            def foo(self,paths):
              new_paths, result = parse(paths[0])
              return new_paths, result

      The load function is called on the first access and on every
      access thereafter where an associated file's `FileState` has
      changed and the time since the last successful load exceeds
      the poll_rate (1s). An attempt at avoiding races is made by
      ignoring reloads that raise exceptions and ignoring reloads
      where files that were stat()ed during the change check have
      changed state after the load.
  '''
  return make_files_property()(func)

# pylint: disable=too-many-statements
@fmtdoc
def make_files_property(
    attr_name=None, unset_object=None, poll_rate=DEFAULT_POLL_INTERVAL
):
  ''' Construct a decorator that watches multiple associated files.

      Parameters:
      * `attr_name`: the underlying attribute, default: `'_'+func.__name__`
      * `unset_object`: the sentinel value for "uninitialised", default: `None`
      * `poll_rate`: how often in seconds to poll the file for changes,
        default from `DEFAULT_POLL_INTERVAL`: `{DEFAULT_POLL_INTERVAL}`

      The attribute *attr_name*`_lock` controls access to the property.
      The attributes *attr_name*`_filestates` and *attr_name*`_paths` track the
      associated files' state.
      The attribute *attr_name*`_lastpoll` tracks the last poll time.

      The decorated function is passed the current list of files
      and returns the new list of files and the associated value.

      One example use would be a configuration file with recursive
      include operations; the inner function would parse the first
      file in the list, and the parse would accumulate this filename
      and those of any included files so that they can be monitored,
      triggering a fresh parse if one changes.

      Example:

          class C(object):
            def __init__(self):
              self._foo_path = '.foorc'
            @files_property
            def foo(self,paths):
              new_paths, result = parse(paths[0])
              return new_paths, result

      The load function is called on the first access and on every
      access thereafter where an associated file's `FileState` has
      changed and the time since the last successful load exceeds
      the `poll_rate`.

      An attempt at avoiding races is made by
      ignoring reloads that raise exceptions and ignoring reloads
      where files that were `os.stat()`ed during the change check have
      changed state after the load.
  '''

  # pylint: disable=too-many-statements
  def made_files_property(func):
    if attr_name is None:
      attr_value = '_' + func.__name__
    else:
      attr_value = attr_name
    attr_lock = attr_value + '_lock'
    attr_filestates = attr_value + '_filestates'
    attr_paths = attr_value + '_paths'
    attr_lastpoll = attr_value + '_lastpoll'

    # pylint: disable=too-many-statements,too-many-branches
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
              except OSError:
                changed = True
              else:
                preload_filestate_map[path] = new_filestate
                if old_filestate != new_filestate:
                  changed = True
          if changed:
            try:
              new_paths, new_value = func(self, old_paths)
              new_filestates = [FileState(new_path) for new_path in new_paths]
            except NameError:
              raise
            except AttributeError:
              raise
            except Exception as e:  # pylint: disable=broad-except
              new_value = getattr(self, attr_value, unset_object)
              if new_value is unset_object:
                raise
              debug(
                  "exception reloading .%s, keeping cached value: %s",
                  attr_value, e
              )
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

# pylint: disable=too-many-branches
def makelockfile(
    path, ext=None, poll_interval=None, timeout=None, runstate=None
):
  ''' Create a lockfile and return its path.

      The lockfile can be removed with `os.remove`.
      This is the core functionality supporting the `lockfile()`
      context manager.

      Parameters:
      * `path`: the base associated with the lock file,
        often the filesystem object whose access is being managed.
      * `ext`: the extension to the base used to construct the lockfile name.
        Default: ".lock"
      * `timeout`: maximum time to wait before failing.
        Default: `None` (wait forever).
        Note that zero is an accepted value
        and requires the lock to succeed on the first attempt.
      * `poll_interval`: polling frequency when timeout is not 0.
      * `runstate`: optional `RunState` duck instance supporting cancellation.
        Note that if a cancelled `RunState` is provided
        no attempt will be made to make the lockfile.
  '''
  if poll_interval is None:
    poll_interval = DEFAULT_POLL_INTERVAL
  if ext is None:
    ext = '.lock'
  if timeout is not None and timeout < 0:
    raise ValueError("timeout should be None or >= 0, not %r" % (timeout,))
  start = None
  lockpath = path + ext
  with Pfx("makelockfile: %r", lockpath):
    while True:
      if runstate is not None and runstate.cancelled:
        warning(
            "%s cancelled; pid %d waited %ds", runstate, os.getpid(),
            0 if start is None else time.time() - start
        )
        raise CancellationError("lock acquisition cancelled")
      try:
        lockfd = os.open(lockpath, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0)
      except OSError as e:
        if e.errno != errno.EEXIST:
          raise
        if timeout is not None and timeout <= 0:
          # immediate failure
          # pylint: disable=raise-missing-from
          raise TimeoutError("pid %d timed out" % (os.getpid(),), timeout)
        now = time.time()
        # post: timeout is None or timeout > 0
        if start is None:
          # first try - set up counters
          start = now
          complaint_last = start
          complaint_interval = 2 * max(DEFAULT_POLL_INTERVAL, poll_interval)
        else:
          if now - complaint_last >= complaint_interval:
            warning("pid %d waited %ds", os.getpid(), now - start)
            complaint_last = now
            complaint_interval *= 2
        # post: start is set
        if timeout is None:
          sleep_for = poll_interval
        else:
          sleep_for = min(poll_interval, start + timeout - now)
        # test for timeout
        if sleep_for <= 0:
          # pylint: disable=raise-missing-from
          raise TimeoutError("pid %d timed out" % (os.getpid(),), timeout)
        time.sleep(sleep_for)
        continue
      else:
        break
    os.close(lockfd)
    return lockpath

@contextmanager
def lockfile(path, ext=None, poll_interval=None, timeout=None, runstate=None):
  ''' A context manager which takes and holds a lock file.

      Parameters:
      * `path`: the base associated with the lock file.
      * `ext`: the extension to the base used to construct the lock file name.
        Default: `'.lock'`
      * `timeout`: maximum time to wait before failing.
        Default: `None` (wait forever).
      * `poll_interval`: polling frequency when timeout is not `0`.
      * `runstate`: optional `RunState` duck instance supporting cancellation.
  '''
  lockpath = makelockfile(
      path,
      ext=ext,
      poll_interval=poll_interval,
      timeout=timeout,
      runstate=runstate
  )
  try:
    yield lockpath
  finally:
    with Pfx("remove %r", lockpath):
      os.remove(lockpath)

def crop_name(name, ext=None, name_max=255):
  ''' Crop a file basename so as not to exceed `name_max` in length.
      Return the original `name` if it already short enough.
      Otherwise crop `name` before the file extension
      to make it short enough.

      Parameters:
      * `name`: the file basename to crop
      * `ext`: optional file extension;
        the default is to infer the extension with `os.path.splitext`.
      * `name_max`: optional maximum length, default: `255`
  '''
  if ext is None:
    base, ext = splitext(name)
  else:
    base = cutsuffix(name, ext)
    if base is name:
      base, ext = splitext(name)
  max_base_len = name_max - len(ext)
  if max_base_len < 0:
    raise ValueError(
        "cannot crop name before ext %r to <=%s: name=%r" %
        (ext, name_max, name)
    )
  if len(base) <= max_base_len:
    return name
  return base[:max_base_len] + ext

def max_suffix(dirpath, pfx):
  ''' Compute the highest existing numeric suffix
      for names starting with the prefix `pfx`.

      This is generally used as a starting point for picking
      a new numeric suffix.
  '''
  pfx = ustr(pfx)
  maxn = None
  pfxlen = len(pfx)
  for e in os.listdir(dirpath):
    e = ustr(e)
    if len(e) <= pfxlen or not e.startswith(pfx):
      continue
    tail = e[pfxlen:]
    if tail.isdigit():
      n = int(tail)
      if maxn is None:
        maxn = n
      elif maxn < n:
        maxn = n
  return maxn

# pylint: disable=too-many-branches
def mkdirn(path, sep=''):
  ''' Create a new directory named `path+sep+n`,
      where `n` exceeds any name already present.

      Parameters:
      * `path`: the basic directory path.
      * `sep`: a separator between `path` and `n`.
        Default: `''`
  '''
  with Pfx("mkdirn(path=%r, sep=%r)", path, sep):
    if os.sep in sep:
      raise ValueError("sep contains os.sep (%r)" % (os.sep,))
    opath = path
    if not path:
      path = '.' + os.sep

    if path.endswith(os.sep):
      if sep:
        raise ValueError(
            "mkdirn(path=%r, sep=%r): using non-empty sep"
            " with a trailing %r seems nonsensical" % (path, sep, os.sep)
        )
      dirpath = path[:-len(os.sep)]
      pfx = ''
    else:
      dirpath = dirname(path)
      if not dirpath:
        dirpath = '.'
      pfx = basename(path) + sep

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
      if not opath:
        newpath = basename(newpath)
      return newpath

def tmpdir():
  ''' Return the pathname of the default temporary directory for scratch data,
      the environment variable `$TMPDIR` or `'/tmp'`.
  '''
  return os.environ.get('TMPDIR', '/tmp')

def tmpdirn(tmp=None):
  ''' Make a new temporary directory with a numeric suffix.
  '''
  if tmp is None:
    tmp = tmpdir()
  return mkdirn(joinpath(tmp, basename(sys.argv[0])))

def find(path, select=None, sort_names=True):
  ''' Walk a directory tree `path`
      yielding selected paths.

      Note: not selecting a directory prunes all its descendants.
  '''
  if select is None:
    select = lambda _: True
  for dirpath, dirnames, filenames in os.walk(path):
    if select(dirpath):
      yield dirpath
    else:
      dirnames[:] = []
      continue
    if sort_names:
      dirnames[:] = sorted(dirnames)
      filenames[:] = sorted(filenames)
    for filename in filenames:
      filepath = joinpath(dirpath, filename)
      if select(filepath):
        yield filepath
    dirnames[:] = [
        dirname for dirname in dirnames if select(joinpath(dirpath, dirname))
    ]

def findup(path, test, first=False):
  ''' Test the pathname `abspath(path)` and each of its ancestors
      against the callable `test`,
      yielding paths satisfying the test.

      If `first` is true (default `False`)
      this function always yields exactly one value,
      either the first path satisfying the test or `None`.
      This mode supports a use such as:

          matched_path = next(findup(path, test, first=True))
          # post condition: matched_path will be `None` on no match
          # otherwise the first matching path
  '''
  path = abspath(path)
  while True:
    if test(path):
      yield path
      if first:
        return
    up = dirname(path)
    if up == path:
      break
    path = up
  if first:
    yield None

def common_path_prefix(*paths):
  ''' Return the common path prefix of the `paths`.

      Note that the common prefix of `'/a/b/c1'` and `'/a/b/c2'`
      is `'/a/b/'`, _not_ `'/a/b/c'`.

      Callers may find it useful to preadjust the supplied paths
      with `normpath`, `abspath` or `realpath` from `os.path`;
      see the `os.path` documentation for the various caveats
      which go with those functions.

      Examples:

          >>> # the obvious
          >>> common_path_prefix('', '')
          ''
          >>> common_path_prefix('/', '/')
          '/'
          >>> common_path_prefix('a', 'a')
          'a'
          >>> common_path_prefix('a', 'b')
          ''
          >>> # nonempty directory path prefixes end in os.sep
          >>> common_path_prefix('/', '/a')
          '/'
          >>> # identical paths include the final basename
          >>> common_path_prefix('p/a', 'p/a')
          'p/a'
          >>> # the comparison does not normalise paths
          >>> common_path_prefix('p//a', 'p//a')
          'p//a'
          >>> common_path_prefix('p//a', 'p//b')
          'p//'
          >>> common_path_prefix('p//a', 'p/a')
          'p/'
          >>> common_path_prefix('p/a', 'p/b')
          'p/'
          >>> # the comparison strips complete unequal path components
          >>> common_path_prefix('p/a1', 'p/a2')
          'p/'
          >>> common_path_prefix('p/a/b1', 'p/a/b2')
          'p/a/'
          >>> # contrast with cs.lex.common_prefix
          >>> common_prefix('abc/def', 'abc/def1')
          'abc/def'
          >>> common_path_prefix('abc/def', 'abc/def1')
          'abc/'
          >>> common_prefix('abc/def', 'abc/def1', 'abc/def2')
          'abc/def'
          >>> common_path_prefix('abc/def', 'abc/def1', 'abc/def2')
          'abc/'
  '''
  prefix = common_prefix(*paths)
  if not prefix.endswith(os.sep):
    path0 = paths[0]
    if not all(map(lambda path: path == path0, paths)):
      # strip basename from prefix
      base = basename(prefix)
      prefix = prefix[:-len(base)]
  return prefix

class Pathname(str):
  ''' Subclass of str presenting convenience properties useful for
      format strings related to file paths.
  '''

  _default_prefixes = (('$HOME/', '~/'),)

  def __format__(self, fmt_spec):
    ''' Calling format(<Pathname>, fmt_spec) treat `fmt_spec` as a new style
        formatting string with a single positional parameter of `self`.
    '''
    if fmt_spec == '':
      return str(self)
    return fmt_spec.format(self)

  @property
  def dirname(self):
    ''' The dirname of the Pathname.
    '''
    return Pathname(dirname(self))

  @property
  def basename(self):
    ''' The basename of this Pathname.
    '''
    return Pathname(basename(self))

  @property
  def abs(self):
    ''' The absolute form of this Pathname.
    '''
    return Pathname(abspath(self))

  @property
  def isabs(self):
    ''' Whether this Pathname is an absolute Pathname.
    '''
    return isabspath(self)

  @property
  def short(self):
    ''' The shortened form of this Pathname.
    '''
    return self.shorten()

  def shorten(self, environ=None, prefixes=None):
    ''' Shorten a Pathname using ~ and ~user.
    '''
    return shortpath(self, environ=environ, prefixes=prefixes)

def iter_fd(fd, **kw):
  ''' Iterate over data from the file descriptor `fd`.
  '''
  for bs in CornuCopyBuffer.from_fd(fd, **kw):
    yield bs

def iter_file(f, **kw):
  ''' Iterate over data from the file `f`.
  '''
  for bs in CornuCopyBuffer.from_file(f, **kw):
    yield bs

def byteses_as_fd(bss, **kw):
  ''' Deliver the iterable of bytes `bss` as a readable file descriptor.
      Return the file descriptor.
      Any keyword arguments are passed to `CornuCopyBuffer.as_fd`.

      Example:

           # present a passphrase for use as in input file descrptor
           # for a subprocess
           rfd = byteses_as_fd([(passphrase + '\n').encode()])
  '''
  return CornuCopyBuffer(bss).as_fd(**kw)

def datafrom_fd(fd, offset=None, readsize=None, aligned=True, maxlength=None):
  ''' General purpose reader for file descriptors yielding data from `offset`.
      **Note**: This does not move the file descriptor position
      **if** the file is seekable.

      Parameters:
      * `fd`: the file descriptor from which to read.
      * `offset`: the offset from which to read.
        If omitted, use the current file descriptor position.
      * `readsize`: the read size, default: `DEFAULT_READSIZE`
      * `aligned`: if true (the default), the first read is sized
        to align the new offset with a multiple of `readsize`.
      * `maxlength`: if specified yield no more than this many bytes of data.
  '''
  try:
    cur_offset = os.lseek(fd, 0, SEEK_CUR)
    is_seekable = True
  except OSError:
    cur_offset = 0  # guess
    is_seekable = False
  if offset is None:
    offset = cur_offset
  if readsize is None:
    readsize = DEFAULT_READSIZE
  if aligned:
    # do an initial read to align all subsequent reads
    alignsize = offset % readsize
    if alignsize > 0:
      if maxlength is not None:
        alignsize = min(maxlength, alignsize)
      bs = pread(fd, alignsize, offset) if is_seekable else read(fd, alignsize)
      if not bs:
        return
      yield bs
      bslen = len(bs)
      offset += bslen
      if maxlength is not None:
        maxlength -= bslen
  while maxlength is None or maxlength > 0:
    if maxlength is not None:
      readsize = min(readsize, maxlength)
    bs = pread(fd, readsize, offset) if is_seekable else read(fd, readsize)
    if not bs:
      return
    yield bs
    bslen = len(bs)
    offset += bslen
    if maxlength is not None:
      maxlength -= bslen

@strable(open_func=lambda filename: os.open(filename, flags=O_RDONLY))
def datafrom(f, offset=None, readsize=None, maxlength=None):
  ''' General purpose reader for files yielding data from `offset`.

      *WARNING*: this function might move the file pointer.

      Parameters:
      * `f`: the file from which to read data;
        if a string, the file is opened with mode="rb";
        if an int, treated as an OS file descriptor;
        otherwise presumed to be a file-like object.
        If that object has a `.fileno()` method, treat that as an
        OS file descriptor and use it.
      * `offset`: starting offset for the data
      * `maxlength`: optional maximum amount of data to yield
      * `readsize`: read size, default DEFAULT_READSIZE.

      For file-like objects, the read1 method is used in preference
      to read if available. The file pointer is briefly moved during
      fetches.
  '''
  if readsize is None:
    readsize = DEFAULT_READSIZE
  if isinstance(f, int):
    # operating system file descriptor
    for data in datafrom_fd(f, offset=offset, readsize=readsize,
                            maxlength=maxlength):
      yield data
    return
  # see if the file has a fileno; if so use datafrom_fd
  try:
    get_fileno = f.fileno
  except AttributeError:
    pass
  else:
    fd = get_fileno()
    if stat.S_ISREG(os.fstat(fd).st_mode):
      for data in datafrom_fd(fd, offset=offset, readsize=readsize,
                              maxlength=maxlength):
        yield data
      return
  # presume a file-like object
  try:
    read1 = f.read1
  except AttributeError:
    read1 = f.read
  tell = f.tell
  seek = f.seek
  while maxlength is None or maxlength > 0:
    offset0 = tell()
    seek(offset, SEEK_SET)
    n = readsize
    if maxlength is not None:
      n = min(n, maxlength)
    bs = read1(n)
    seek(offset0)
    if not bs:
      break
    yield bs
    offset += len(bs)
    if maxlength is not None:
      maxlength -= len(bs)
      assert maxlength >= 0

class ReadMixin(object):
  ''' Useful read methods to accomodate modes not necessarily available in a class.

      Note that this mixin presumes that the attribute `self._lock`
      is a threading.RLock like context manager.

      Classes using this mixin should consider overriding the default
      .datafrom method with something more efficient or direct.
  '''

  def datafrom(self, offset, readsize=None):
    ''' Yield data from the specified `offset` onward in some
        approximation of the "natural" chunk size.

        *NOTE*: UNLIKE the global datafrom() function, this method
        MUST NOT move the logical file position. Implementors may need
        to save and restore the file pointer within a lock around
        the I/O if they do not use a direct access method like
        os.pread.

        The aspiration here is to read data with only a single call
        to the underlying storage, and to return the chunks in
        natural sizes instead of some default read size.

        Classes using this mixin must implement this method.
    '''
    raise NotImplementedError(
        "return an iterator which does not change the file offset"
    )

  def bufferfrom(self, offset):
    ''' Return a CornuCopyBuffer from the specified `offset`.
    '''
    return CornuCopyBuffer(self.datafrom(offset), offset=offset)

  # pylint: disable=too-many-branches
  def read(self, size=-1, offset=None, longread=False):
    ''' Read up to `size` bytes, honouring the "single system call"
        spirit unless `longread` is true.

        Parameters:
        * `size`: the number of bytes requested. A size of -1 requests
          all bytes to the end of the file.
        * `offset`: the starting point of the read; if None, use the
          current file position; if not None, seek to this position
          before reading, even if `size` == 0.
        * `longread`: switch from "single system call" to "as many
          as required to obtain `size` bytes"; short data will still
          be returned if the file is too short.
    '''
    bfr = getattr(self, '_reading_bfr', None)
    if offset is None:
      if bfr is None:
        offset = self.tell()
      else:
        offset = bfr.offset
    if size == -1:
      size = len(self) - offset
      if size < 0:
        size = 0
    if size == 0:
      return b''
    if longread:
      bss = []
    while size > 0:
      with self._lock:
        # We need to retest on each iteration because other reads
        # may be interleaved, interfering with the buffer.
        if bfr is None or bfr.offset != offset:
          ##if bfr is not None:
          ##  info(
          ##      "ReadMixin.read: new bfr from offset=%d (old bfr was %s)",
          ##      offset, bfr)
          self._reading_bfr = bfr = self.bufferfrom(offset)
        bfr.extend(1, short_ok=True)
        if not bfr.buf:
          break
        consume = min(size, len(bfr.buf))
        assert consume > 0
        chunk = bfr.take(consume)
        offset += consume
        self.seek(offset)
      assert len(chunk) == consume
      if longread:
        bss.append(chunk)
      else:
        return chunk
      size -= consume
    if not bss:
      return b''
    if len(bss) == 1:
      return bss[0]
    return b''.join(bss)

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
    if nread != len(data):
      raise RuntimeError(
          "  WRONG NUMBER OF BYTES(%d): data=%s" % (nread, data)
      )
    return memoryview(data)[:nread] if nread != n else data

  @locked
  def readinto(self, barray):
    ''' Read data into a bytearray.
    '''
    needed = len(barray)
    boff = 0
    for bs in self.datafrom(self.tell()):
      if not bs:
        break
      if len(bs) > needed:
        bs = memoryview(bs)[:needed]
      bs_len = len(bs)
      boff2 = boff + bs_len
      barray[boff:boff2] = bs
      boff = boff2
      needed -= bs_len
    return boff

class BackedFile(ReadMixin):
  ''' A RawIOBase duck type
      which uses a backing file for initial data
      and writes new data to a front scratch file.
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
      back_len = back_file.seek(0, 2)
      back_file.seek(back_pos, 0)
    return max(self.front_range.end, back_len)

  @locked
  def switch_back_file(self, new_back_file):
    ''' Switch out one back file for another. Return the old back file.
    '''
    old_back_file = self.back_file
    self.back_file = new_back_file
    return old_back_file

  def __enter__(self):
    ''' BackedFile instances offer a context manager that take the lock,
        allowing synchronous use of the file
        without implementing a suite of special methods like pread/pwrite.
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
      endpos = self.back_file.seek(0, SEEK_END)
      if self.front_range is not None:
        endpos = max(endpos, self.front_range.end)
      self._offset = endpos
    else:
      raise ValueError("unsupported whence value %r" % (whence,))

  def datafrom(self, offset):
    ''' Generator yielding natural chunks from the file commencing at offset.
    '''
    global_datafrom = globals()['datafrom']
    front_file = self.front_file
    try:
      front_datafrom = front_file.datafrom
    except AttributeError:
      front_datafrom = partial(global_datafrom, front_file)
    back_file = self.back_file
    try:
      back_datafrom = back_file.datafrom
    except AttributeError:
      back_datafrom = partial(global_datafrom, back_file)
    for in_front, span in self.front_range.slices(offset, len(self)):
      consume = len(span)
      assert consume > 0
      if in_front:
        chunks = front_datafrom(span.start)
      else:
        chunks = back_datafrom(span.start)
      for bs in chunks:
        assert len(bs) > 0
        if len(bs) > consume:
          bs = memoryview(bs)[:consume]
        yield bs
        bs_len = len(bs)
        consume -= bs_len
        if consume <= 0:
          break
        offset += bs_len

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
      warning(
          "front_file.write() returned None, assuming %d bytes written, data=%r",
          len(b), b
      )
      written = len(b)
    self.front_range.add_span(start, start + written)
    return written

# pylint: disable=too-few-public-methods,protected-access
class BackedFile_TestMethods(object):
  ''' Mixin for testing subclasses of BackedFile.
      Tests self.backed_fp.
  '''

  # pylint: disable=no-member
  def _eq(self, a, b, opdesc):
    ''' Convenience wrapper for assertEqual.
    '''
    ##if a == b:
    ##  print("OK: %s: %r == %r" % (opdesc, a, b), file=sys.stderr)
    self.assertEqual(a, b, "%s: got %r, expected %r" % (opdesc, a, b))

  # pylint: disable=no-member
  def test_BackedFile(self):
    ''' Test function for a BackedFile to use in unit test suites.
    '''
    from random import randint  # pylint: disable=import-outside-toplevel
    backing_text = self.backing_text
    bfp = self.backed_fp
    # test reading whole file
    bfp.seek(0)
    bfp_text = bfp.read_n(len(bfp))
    self._eq(backing_text, bfp_text, "backing_text vs bfp_text")
    # test reading first 512 bytes only
    bfp.seek(0)
    bfp_leading_text = bfp.read_n(512)
    self._eq(
        backing_text[:512], bfp_leading_text,
        "leading 512 bytes of backing_text vs bfp_leading_text"
    )
    # test writing some data and reading it back
    random_chunk = bytes(randint(0, 255) for x in range(256))
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
    self.assertEqual(
        len(overlap_chunk), 512, "overlap_chunk not 512 bytes: %d:%s" %
        (len(overlap_chunk), bytes(overlap_chunk))
    )
    self.assertEqual(overlap_chunk, backing_text[256:512] + random_chunk)

class Tee(object):
  ''' An object with .write, .flush and .close methods
      which copies data to multiple output files.
  '''

  def __init__(self, *fps):
    ''' Initialise the Tee; any arguments are taken to be output file objects.
    '''
    self._fps = list(fps)

  def add(self, output):
    ''' Add a new output.
    '''
    self._fps.append(output)

  def write(self, data):
    ''' Write the data to all the outputs.
        Note: does not detect or accodmodate short writes.
    '''
    for fp in self._fps:
      fp.write(data)

  def flush(self):
    ''' Flush all the outputs.
    '''
    for fp in self._fps:
      fp.flush()

  def close(self):
    ''' Close all the outputs and close the Tee.
    '''
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
  try:
    yield
  finally:
    fp.write = old_write
    fp.flush = old_flush

class NullFile(object):
  ''' Writable file that discards its input.

      Note that this is _not_ an open of `os.devnull`;
      it just discards writes and is not the underlying file descriptor.
  '''

  def __init__(self):
    ''' Initialise the file offset to 0.
    '''
    self.offset = 0

  def write(self, data):
    ''' Discard data, advance file offset by length of data.
    '''
    dlen = len(data)
    self.offset += dlen
    return dlen

  def flush(self):
    ''' Flush buffered data to the subsystem.
    '''

def file_data(fp, nbytes=None, rsize=None):
  ''' Read `nbytes` of data from `fp` and yield the chunks as read.

      Parameters:
      * `nbytes`: number of bytes to read; if None read until EOF.
      * `rsize`: read size, default DEFAULT_READSIZE.
  '''
  # try to use the "short read" flavour of read if available
  if rsize is None:
    rsize = DEFAULT_READSIZE
  try:
    read1 = fp.read1
  except AttributeError:
    read1 = fp.read
  ##prefix = "file_data(fp, nbytes=%d)" % (nbytes,)
  copied = 0
  while nbytes is None or nbytes > 0:
    to_read = rsize if nbytes is None else min(nbytes, rsize)
    data = read1(to_read)
    if not data:
      if nbytes is not None:
        if copied > 0:
          # no warning of nothing copied - that is immediate end of file - valid
          warning(
              "early EOF: only %d bytes read, %d still to go", copied, nbytes
          )
      break
    yield data
    copied += len(data)
    if nbytes is not None:
      nbytes -= len(data)

def copy_data(fpin, fpout, nbytes, rsize=None):
  ''' Copy `nbytes` of data from `fpin` to `fpout`,
      return the number of bytes copied.

      Parameters:
      * `nbytes`: number of bytes to copy.
        If `None`, copy until EOF.
      * `rsize`: read size, default `DEFAULT_READSIZE`.
  '''
  copied = 0
  for chunk in file_data(fpin, nbytes, rsize):
    fpout.write(chunk)
    copied += len(chunk)
  return copied

def read_data(fp, nbytes, rsize=None):
  ''' Read `nbytes` of data from `fp`, return the data.

      Parameters:
      * `nbytes`: number of bytes to copy.
        If `None`, copy until EOF.
      * `rsize`: read size, default `DEFAULT_READSIZE`.
  '''
  bss = list(file_data(fp, nbytes, rsize))
  if not bss:
    return b''
  if len(bss) == 1:
    return bss[0]
  return b''.join(bss)

def read_from(fp, rsize=None, tail_mode=False, tail_delay=None):
  ''' Generator to present text or data from an open file until EOF.

      Parameters:
      * `rsize`: read size, default: DEFAULT_READSIZE
      * `tail_mode`: if true, yield an empty chunk at EOF, allowing resumption
        if the file grows.
  '''
  if rsize is None:
    rsize = DEFAULT_READSIZE
  if tail_delay is None:
    tail_delay = DEFAULT_TAIL_PAUSE
  elif not tail_mode:
    raise ValueError(
        "tail_mode=%r but tail_delay=%r" % (tail_mode, tail_delay)
    )
  while True:
    chunk = fp.read(rsize)
    if not chunk:
      if tail_mode:
        # indicate EOF and pause
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

# pylint: disable=redefined-builtin
@contextmanager
def atomic_filename(
    filename,
    exists_ok=False,
    placeholder=False,
    dir=None,
    prefix=None,
    suffix=None,
    **kw
):
  ''' A context manager to create `filename` atomicly on completion.
      This returns a `NamedTemporaryFile` to use to create the file contents.
      On completion the temporary file is renamed to the target name `filename`.

      Parameters:
      * `filename`: the file name to create
      * `exists_ok`: default `False`;
        if true it not an error if `filename` already exists
      * `placeholder`: create a placeholder file at `filename`
        while the real contents are written to the temporary file
      * `dir`: passed to `NamedTemporaryFile`, specifies the directory
        to hold the temporary file; the default is `dirname(filename)`
        to ensure the rename is atomic
      * `prefix`: passed to `NamedTemporaryFile`, specifies a prefix
        for the temporary file; the default is a dot (`'.'`) plus the prefix
        from `splitext(basename(filename))`
      * `suffix`: passed to `NamedTemporaryFile`, specifies a suffix
        for the temporary file; the default is the extension obtained
        from `splitext(basename(filename))`
      Other keyword arguments are passed to the `NamedTemporaryFile` constructor.

      Example:

          >>> import os
          >>> from os.path import exists as existspath
          >>> fn = 'test_atomic_filename'
          >>> with atomic_filename(fn, mode='w') as f:
          ...     assert not existspath(fn)
          ...     print('foo', file=f)
          ...     assert not existspath(fn)
          ...
          >>> assert existspath(fn)
          >>> assert open(fn).read() == 'foo\\n'
          >>> os.remove(fn)
  '''
  if dir is None:
    dir = dirname(filename)
  fprefix, fsuffix = splitext(basename(filename))
  if prefix is None:
    prefix = '.' + fprefix
  if suffix is None:
    suffix = fsuffix
  if existspath(filename) and not exists_ok:
    raise ValueError("already exists: %r" % (filename,))
  with NamedTemporaryFile(dir=dir, prefix=prefix, suffix=suffix, delete=False,
                          **kw) as T:
    if placeholder:
      # create a placeholder file
      with open(filename, 'ab' if exists_ok else 'xb'):
        pass
    yield T
    if placeholder:
      try:
        pfx_call(shutil.copymode, filename, T.name)
      except OSError as e:
        warning(
            "defaut modes not copied from from placeholder %r: %s", filename, e
        )
    pfx_call(rename, T.name, filename)

class RWFileBlockCache(object):
  ''' A scratch file for storing data.
  '''

  def __init__(self, pathname=None, dirpath=None, suffix=None, lock=None):
    ''' Initialise the file.

        Parameters:
        * `pathname`: path of file. If None, create a new file with
          tempfile.mkstemp using dir=`dirpath` and unlink that file once
          opened.
        * `dirpath`: location for the file if made by mkstemp as above.
        * `lock`: an object to use as a mutex, allowing sharing with
          some outer system. A Lock will be allocated if omitted.
    '''
    opathname = pathname
    if pathname is None:
      tmpfd, pathname = mkstemp(dir=dirpath, suffix=suffix)
    self.fd = os.open(pathname, os.O_RDWR | os.O_APPEND)
    if opathname is None:
      os.remove(pathname)
      os.close(tmpfd)
      self.pathname = None
    else:
      self.pathname = pathname
    if lock is None:
      lock = Lock()
    self._lock = lock

  def __str__(self):
    return "%s(pathname=%s)" % (type(self).__name__, self.pathname)

  def close(self):
    ''' Close the file descriptors.
    '''
    with Pfx("%s.close", self):
      fd = self.fd
      if fd is None:
        warning("fd already closed")
      else:
        os.close(fd)
        self.fd = None

  @property
  def closed(self):
    ''' Test whether the file descriptor has been closed.
    '''
    return self.fd is None

  def put(self, data):
    ''' Store `data`, return offset.
    '''
    fd = self.fd
    with self._lock:
      offset = os.lseek(fd, 0, 1)
      if len(data) == 0:
        length = 0
      else:
        length = os.write(fd, data)
    assert length == len(data)
    return offset

  def get(self, offset, length):
    ''' Get data from `offset` of length `length`.
    '''
    assert length > 0
    fd = self.fd
    data = os.pread(fd, length, offset)
    assert len(data) == length
    return data

@contextmanager
def gzifopen(path, mode='r', *a, **kw):
  ''' Context manager to open a file which may be a plain file or a gzipped file.

      If `path` ends with `'.gz'` then the filesystem paths attempted
      are `path` and `path` without the extension, otherwise the
      filesystem paths attempted are `path+'.gz'` and `path`.  In
      this way a path ending in `'.gz'` indicates a preference for
      a gzipped file otherwise an uncompressed file.

      However, if exactly one of the paths exists already then only
      that path will be used.

      Note that the single character modes `'r'`, `'a'`, `'w'` and `'x'`
      are text mode for both uncompressed and gzipped opens,
      like the builtin `open` and *unlike* `gzip.open`.
      This is to ensure equivalent behaviour.
  '''
  compresslevel = kw.pop('compresslevel', 9)
  path0 = path
  path, ext = splitext(path)
  if ext == '.gz':
    # gzip preferred
    gzpath = path0
    path1, path2 = gzpath, path
  else:
    # unzipped has precedence
    gzpath = path0 + '.gz'
    path1, path2 = path0, gzpath
  # if exactly one of the files exists, try only that file
  if existspath(path1) and not existspath(path2):
    paths = path1,
  elif existspath(path2) and not existspath(path1):
    paths = path2,
  else:
    paths = path1, path2
  for openpath in paths:
    try:
      with (gzip.open(openpath,
                      (mode + 't' if mode in ('r', 'a', 'w', 'x') else mode), *
                      a, compresslevel=compresslevel, **kw) if
            openpath.endswith('.gz') else open(openpath, mode, *a, **kw)) as f:
        yield f
    except FileNotFoundError:
      # last path to try
      if openpath == paths[-1]:
        raise
      # not present, try the other file
      continue
    # open succeeded, we're done
    return
  raise RuntimeError("NOTREACHED")

if __name__ == '__main__':
  import cs.fileutils_tests
  cs.fileutils_tests.selftest(sys.argv)
