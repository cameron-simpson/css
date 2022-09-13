#!/usr/bin/env python3

''' Assorted filesystem related utility functions,
    some of which have been bloating cs.fileutils for too long.
'''

from fnmatch import fnmatch
from functools import partial
import os
from os.path import (
    basename,
    dirname,
    exists as existspath,
    expanduser,
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
from typeguard import typechecked

from cs.deco import decorator
from cs.env import envsub
from cs.obj import SingletonMixin
from cs.pfx import pfx_call

__version__ = '20220805-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.env',
        'cs.obj',
        'cs.pfx',
        'icontract',
        'typeguard',
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
    else:
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
  return [
      filename for filename in pfx_listdir(dirpath)
      if fnmatch(filename, fnglob)
  ]

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
    return shortpath(self.fspath)

  @require(lambda subpath: not isabspath(subpath))
  def pathto(self, subpath):
    ''' The full path to `subpath`, a relative path below `self.fspath`.
    '''
    return joinpath(self.fspath, subpath)

  def fnmatch(self, fnglob):
    ''' Return a list of the names in `self.fspath` matching the glob `fnglob`.
    '''
    return fnmatchdir(self.fspath, fnglob)

class FSPathBasedSingleton(SingletonMixin, HasFSPath):
  ''' The basis for a `SingletonMixin` based on `realpath(self.fspath)`.
  '''

  @classmethod
  def _resolve_fspath(cls, fspath, envvar=None, default_attr=None):
    ''' Resolve the filesystem path `fspath` using `os.path.realpath`.

        Parameters:
        * `fspath`: the filesystem path to resolve;
          this may be `None` to use the class defaults
        * `envvar`: the environment variable to consult for a default `fspath`;
          the default for this comes from `cls.FSPATH_ENVVAR` if defined
        * `default_attr`: the class attribute containing the default `fspath`
          if defined and there is no environment variable for `envvar`

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
      if default_attr is None:
        default_attr = 'FSPATH_DEFAULT'
      defaultpath = getattr(cls, default_attr, None)
      if defaultpath is not None:
        return realpath(expanduser(defaultpath))
      raise ValueError(
          "_resolve_fspath: fspath=None and no %s no %s.%s" % (
              (
                  cls.__name__ + '.FSPATH_ENVVAR' if envvar is None else '$' +
                  envvar
              ),
              cls.name,
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

def shortpath(path, environ=None, prefixes=None):
  ''' Return `path` with the first matching leading prefix replaced.

      Parameters:
      * `environ`: environment mapping if not os.environ
      * `prefixes`: iterable of `(prefix,subst)` to consider for replacement;
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
      The converse of `shortpath()`.
  '''
  if prefixes is None:
    prefixes = DEFAULT_SHORTEN_PREFIXES
  for prefix, subst in prefixes:
    if path.startswith(subst):
      path = prefix + path[len(subst):]
      break
  path = envsub(path, environ)
  return path

def is_clean_subpath(subpath: str):
  ''' Test that `subpath` is clean:
      - not empty or '.' or '..'
      - not an absolute path
      - normalised
      - does not walk up out of its parent directory

      Examples:

          >>> is_clean_subpath('')
          False
          >>> is_clean_subpath('.')
  '''
  if subpath in ('', '.', '..'):
    return False
  if isabspath(subpath):
    return False
  normalised = normpath(subpath)
  return subpath == normalised and not normalised.startswith('../')
