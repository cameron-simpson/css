#!/usr/bin/env python3

''' Assorted filesystem related utility functions,
    some of which have been bloating cs.fileutils for too long.
'''

import os
from os.path import (
    basename,
    dirname,
    exists as existspath,
    expanduser,
    isabs as isabspath,
    isdir as isdirpath,
    join as joinpath,
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

__version__ = '20220327-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.deco', 'cs.pfx'],
}

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
      pfx_call(os.mkdir, dirpath, 0o000)
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
          pfx_call(os.rmdir, dirpath)
          remove_placeholder = False
        elif existspath(dirpath):
          raise ValueError("directory already exists: %r" % (dirpath,))
        pfx_call(os.rename, tmpdirpath, dirpath)
        pfx_call(os.mkdir, tmpdirpath, 0o000)
    except:
      if remove_placeholder and isdirpath(dirpath):
        pfx_call(os.rmdir, dirpath)
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

# pylint: disable=too-few-public-methods
class HasFSPath:
  ''' An object with a `.fspath` attribute representing a filesystem location.
  '''

  @require(lambda fspath: isabspath(fspath))  # pylint: disable=unnecessary-lambda
  def __init__(self, fspath):
    self.fspath = fspath

  @require(lambda subpath: not isabspath(subpath))
  def pathto(self, subpath):
    ''' The full path to `subpath`, a relative path below `self.fspath`.
    '''
    return joinpath(self.fspath, subpath)

class FSPathBasedSingleton(SingletonMixin, HasFSPath):
  ''' The basis for a `SingletonMixin` based on `realpath(self.fspath)`.
  '''

  @classmethod
  def _get_default_fspath(cls):
    ''' Obtain the default filesystem path.
    '''
    # pylint: disable=no-member
    fspath = os.environ.get(cls.FSPATH_ENVVAR)
    if fspath is None:
      # pylint: disable=no-member
      fspath = expanduser(cls.FSPATH_DEFAULT)
    return fspath

  @classmethod
  def _singleton_key(cls, fspath=None, **_):
    ''' Each instance is identified by `realpath(fspath)`.
    '''
    if fspath is None:
      fspath = cls._get_default_fspath()
    return realpath(fspath)

  @typechecked
  def __init__(self, fspath: Optional[str] = None):
    if hasattr(self, '_lock'):
      return
    if fspath is None:
      fspath = self._get_default_fspath()
    HasFSPath.__init__(self, fspath)
    self._lock = Lock()

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
