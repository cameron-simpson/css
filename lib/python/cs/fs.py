#!/usr/bin/env python3

''' Assorted filesystem related utility functions,
    some of which have been bloating cs.fileutils for too long.
'''

from fnmatch import filter as fnfilter
from functools import partial
import os
from os.path import (
    basename,
    dirname,
    exists as existspath,
    expanduser,
    expandvars,
    isabs as isabspath,
    isdir as isdirpath,
    join as joinpath,
    normpath,
    realpath,
    relpath,
)
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Optional

from icontract import require

from cs.deco import decorator
from cs.obj import SingletonMixin
from cs.pfx import pfx, pfx_call

__version__ = '20240201'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.obj',
        'cs.pfx',
        'icontract',
    ],
}

pfx_listdir = partial(pfx_call, os.listdir)
pfx_mkdir = partial(pfx_call, os.mkdir)
pfx_makedirs = partial(pfx_call, os.makedirs)
pfx_rename = partial(pfx_call, os.rename)
pfx_rmdir = partial(pfx_call, os.rmdir)

def needdir(dirpath, mode=0o777, *, use_makedirs=False, log=None):
  ''' Create the directory `dirpath` if missing.

      Parameters:
      * `dirpath`: the required directory path
      * `mode`: the permissions mode, default `0o777`
      * `log`: log `makedirs` or `mkdir` call
      * `use_makedirs`: optional creation mode, default `False`;
        if true, use `os.makedirs`, otherwise `os.mkdir`
  '''
  if not isdirpath(dirpath):
    if use_makedirs:
      if log is not None:
        log("makedirs(%r,0o%3o)", dirpath, mode)
      pfx_makedirs(dirpath, mode)
    else:
      if log is not None:
        log("mkdir(%r,0o%3o)", dirpath, mode)
      pfx_mkdir(dirpath, mode)

@decorator
def atomic_directory(infill_func, make_placeholder=False):
  ''' Decorator for a function which fills in a directory
      which calls the function against a temporary directory
      then renames the temporary to the target name on completion.

      Parameters:
      * `infill_func`: the function to fill in the target directory
      * `make_placeholder`: optional flag, default `False`:
        if true an empty directory will be make at the target name
        and after completion it will be removed and the completed
        directory renamed to the target name
  '''

  def atomic_directory_wrapper(dirpath, *a, **kw):
    assert isinstance(dirpath, str
                      ), "dirpath not a str: %s:%r" % (type(dirpath), dirpath)
    remove_placeholder = False
    if make_placeholder:
      # prevent other users from using this directory
      pfx_mkdir(dirpath, 0o000)
      remove_placeholder = True
    else:
      if existspath(dirpath):
        raise ValueError("directory already exists: %r" % (dirpath,))
    work_dirpath = dirname(dirpath)
    try:
      with TemporaryDirectory(
          dir=work_dirpath,
          prefix='.tmp--atomic_directory--',
          suffix='--' + basename(dirpath),
      ) as tmpdirpath:
        result = infill_func(tmpdirpath, *a, **kw)
        if remove_placeholder:
          pfx_rmdir(dirpath)
          remove_placeholder = False
        elif existspath(dirpath):
          raise ValueError("directory already exists: %r" % (dirpath,))
        pfx_rename(tmpdirpath, dirpath)
        pfx_mkdir(tmpdirpath, 0o000)
    except:
      if remove_placeholder and isdirpath(dirpath):
        pfx_rmdir(dirpath)
      raise
    return result

  return atomic_directory_wrapper

def rpaths(
    dirpath='.', *, only_suffixes=None, skip_suffixes=None, sort_paths=False
):
  ''' Yield relative file paths from a directory.

      Parameters:
      * `dirpath`: optional top directory, default `'.'`
      * `only_suffixes`: optional iterable of suffixes of interest;
        if provided only files ending in these suffixes will be yielded
      * `skip_suffixes`: optional iterable if suffixes to ignore;
        if provided files ending in these suffixes will not be yielded
      * `sort_paths`: optional flag specifying that filenames should be sorted,
        default `False`
  '''
  if only_suffixes is not None:
    only_suffixes = tuple(only_suffixes)
  if skip_suffixes is not None:
    skip_suffixes = tuple(skip_suffixes)
  for subpath, subdirnames, filenames in os.walk(dirpath):
    if sort_paths:
      subdirnames[:] = sorted(subdirnames)
      filenames = sorted(filenames)
    for filename in filenames:
      if skip_suffixes is not None and filename.endswith(skip_suffixes):
        continue
      if only_suffixes is not None and not filename.endswith(only_suffixes):
        continue
      yield relpath(joinpath(subpath, filename), dirpath)

def fnmatchdir(dirpath, fnglob):
  ''' Return a list of the names in `dirpath` matching the glob `fnglob`.
  '''
  return fnfilter(pfx_listdir(dirpath), fnglob)

# pylint: disable=too-few-public-methods
class HasFSPath:
  ''' An object with a `.fspath` attribute representing a filesystem location.
  '''

  def __init__(self, fspath):
    self.fspath = fspath

  def __str__(self):
    return f'{self.__class__.__name__}(fspath={self.shortpath})'

  @property
  def shortpath(self):
    ''' The short version of `self.fspath`.
    '''
    try:
      return shortpath(self.fspath)
    except AttributeError:
      return "<no-fspath>"

  @require(lambda subpaths: len(subpaths) > 0)
  @require(lambda subpaths: not any(map(isabspath, subpaths)))
  def pathto(self, *subpaths):
    ''' The full path to `subpaths`, comprising a relative path
        below `self.fspath`.
        This is a shim for `os.path.join` which requires that all
        the `subpaths` be relative paths.
    '''
    return joinpath(self.fspath, *subpaths)

  def fnmatch(self, fnglob):
    ''' Return a list of the names in `self.fspath` matching the glob `fnglob`.
    '''
    return fnmatchdir(self.fspath, fnglob)

  def listdir(self):
    ''' Return `os.listdir(self.fspath)`. '''
    return os.listdir(self.fspath)

class FSPathBasedSingleton(SingletonMixin, HasFSPath):
  ''' The basis for a `SingletonMixin` based on `realpath(self.fspath)`.
  '''

  @classmethod
  def _resolve_fspath(
      cls,
      fspath: Optional[str] = None,
      envvar: Optional[str] = None,
      default_attr: str = 'FSPATH_DEFAULT'
  ):
    ''' Resolve the filesystem path `fspath` using `os.path.realpath`.

        Parameters:
        * `fspath`: the filesystem path to resolve;
          this may be `None` to use the class defaults
        * `envvar`: the environment variable to consult for a default `fspath`;
          the default for this comes from `cls.FSPATH_ENVVAR` if defined
        * `default_attr`: the class attribute containing the default `fspath`
          if defined and there is no environment variable for `envvar`

        The `default_attr` value may be either a `str`, in which
        case `os.path.expanduser` is called on it`, or a callable
        returning a filesystem path.

        The common mode is where each instance might have an arbitrary path,
        such as a `TagFile`.

        The "class default" mode is intended for things like `CalibreTree`
        which has the notion of a default location for your Calibre library.
    '''
    if fspath is None:
      # pylint: disable=no-member
      if envvar is None:
        envvar = getattr(cls, 'FSPATH_ENVVAR', None)
      if envvar is not None:
        fspath = os.environ.get(envvar)
        if fspath is not None:
          return realpath(fspath)
      default = getattr(cls, default_attr, None)
      if default is not None:
        if callable(default):
          fspath = default()
        else:
          fspath = expanduser(default)
        if fspath is not None:
          return realpath(fspath)
      raise ValueError(
          "_resolve_fspath: fspath=None and no %s and no %s.%s" % (
              (
                  cls.__name__ + '.FSPATH_ENVVAR' if envvar is None else '$' +
                  envvar
              ),
              cls.__name__,
              default_attr,
          )
      )
    return realpath(fspath)

  @classmethod
  def _singleton_key(cls, fspath=None, **_):
    ''' Each instance is identified by `realpath(fspath)`.
    '''
    return cls._resolve_fspath(fspath)

  ##@typechecked
  def __init__(self, fspath: Optional[str] = None, lock=None):
    ''' Initialise the singleton:

        On the first call:
        - set `.fspath` to `self._resolve_fspath(fspath)`
        - set `._lock` to `lock` (or `threading.Lock()` if not specified)
        - return `True`
        On subsequent calls return `False`.

    '''
    if '_lock' in self.__dict__:
      return False
    fspath = self._resolve_fspath(fspath)
    HasFSPath.__init__(self, fspath)
    if lock is None:
      lock = Lock()
    self._lock = lock
    return True

DEFAULT_SHORTEN_PREFIXES = (('$HOME/', '~/'),)

def shortpath(path, prefixes=None):
  ''' Return `path` with the first matching leading prefix replaced.

      Parameters:
      * `environ`: environment mapping if not os.environ
      * `prefixes`: optional iterable of `(prefix,subst)` to consider for replacement;
        each `prefix` is subject to environment variable
        substitution before consideration
        The default considers "$HOME/" for replacement by "~/".
  '''
  if prefixes is None:
    prefixes = DEFAULT_SHORTEN_PREFIXES
  for prefix, subst in prefixes:
    prefix = expandvars(prefix)
    if path.startswith(prefix):
      return subst + path[len(prefix):]
  return path

def longpath(path, prefixes=None):
  ''' Return `path` with prefixes and environment variables substituted.
      The converse of `shortpath()`.
  '''
  if prefixes is None:
    prefixes = DEFAULT_SHORTEN_PREFIXES
  for prefix, subst in prefixes:
    if path.startswith(subst):
      path = prefix + path[len(subst):]
      break
  path = expandvars(path)
  return path

@pfx
def validate_rpath(rpath: str):
  ''' Test that `rpath` is a clean relative path with no funny business;
      raise `ValueError` if the test fails.

      Tests:
      - not empty or '.' or '..'
      - not an absolute path
      - normalised
      - does not walk up out of its parent directory

      Examples:

          >>> validate_rpath('')
          False
          >>> validate_rpath('.')
  '''
  if not rpath:
    raise ValueError('empty path')
  if rpath in ('.', '..'):
    raise ValueError('may not be . or ..')
  if isabspath(rpath):
    raise ValueError('absolute path')
  if rpath != normpath(rpath):
    raise ValueError('!= normpath(rpath)')
  if rpath.startswith('../'):
    raise ValueError('goes up')

def is_valid_rpath(rpath, log=None) -> bool:
  ''' Test that `rpath` is a clean relative path with no funny business.

      This is a Boolean wrapper for `validate_rpath()`.
  '''
  try:
    validate_rpath(rpath)
  except ValueError as e:
    if log is not None:
      log("invalid: %s", e)
    return False
  return True
