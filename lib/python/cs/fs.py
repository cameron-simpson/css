#!/usr/bin/env python3

''' Assorted filesystem related utility functions,
    some of which have been bloating cs.fileutils for too long.
'''

from collections import namedtuple
import errno
from fnmatch import filter as fnfilter
from functools import partial
import os
from os import PathLike
from os.path import (
    abspath,
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
    splitext,
)
from pathlib import Path
from pwd import getpwuid
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any, Callable, Iterable, Optional, Union

from cs.deco import decorator, fmtdoc, Promotable
from cs.lex import r
from cs.obj import SingletonMixin
from cs.pfx import pfx, pfx_call

__version__ = '20250414'

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
    ],
    'python_requires':
    '>=3.6',
}

pfx_listdir = partial(pfx_call, os.listdir)
pfx_mkdir = partial(pfx_call, os.mkdir)
pfx_makedirs = partial(pfx_call, os.makedirs)
pfx_rename = partial(pfx_call, os.rename)
pfx_rmdir = partial(pfx_call, os.rmdir)

def needdir(dirpath, mode=0o777, *, use_makedirs=False, log=None) -> bool:
  ''' Create the directory `dirpath` if missing.
      Return `True` if the directory was made, `False` otherwise.

      Parameters:
      * `dirpath`: the required directory path
      * `mode`: the permissions mode, default `0o777`
      * `log`: log `makedirs` or `mkdir` call
      * `use_makedirs`: optional creation mode, default `False`;
        if true, use `os.makedirs`, otherwise `os.mkdir`
  '''
  if isdirpath(dirpath):
    return False
  if use_makedirs:
    if log is not None:
      log("makedirs(%r,0o%3o)", dirpath, mode)
    pfx_makedirs(dirpath, mode)
  else:
    if log is not None:
      log("mkdir(%r,0o%3o)", dirpath, mode)
    pfx_mkdir(dirpath, mode)
  return True

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
    assert isinstance(
        dirpath,
        str,
    ), f'dirpath not a str: {r(dirpath)}'
    remove_placeholder = False
    if make_placeholder:
      # prevent other users from using this directory
      pfx_mkdir(dirpath, 0o000)
      remove_placeholder = True
    elif existspath(dirpath):
      raise FileExistsError(dirpath)
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
          raise FileExistsError(dirpath)
        pfx_rename(tmpdirpath, dirpath)
        pfx_mkdir(tmpdirpath, 0o000)
    except:
      if remove_placeholder and isdirpath(dirpath):
        pfx_rmdir(dirpath)
      raise
    return result

  return atomic_directory_wrapper

@pfx
def scandirtree(
    dirpath='.',
    *,
    include_dirs=False,
    name_selector=None,
    only_suffixes=None,
    skip_suffixes=None,
    sort_names=False,
    follow_symlinks=False,
    recurse=True,
):
  ''' Generator to recurse over `dirpath`, yielding `(is_dir,subpath)`
      for all selected subpaths.

      Parameters:
      * `dirpath`: the directory to scan, default `'.'`
      * `include_dirs`: if true yield directories; default `False`
      * `name_selector`: optional callable to select particular names;
        the default is to select names not starting with a dot (`'.'`)
      * `only_suffixes`: if supplied, skip entries whose extension
        is not in `only_suffixes`
      * `skip_suffixes`: if supplied, skip entries whose extension
        is in `skip_suffixes`
      * `sort_names`: option flag, default `False`; yield entires
        in lexical order if true
      * `follow_symlinks`: optional flag, default `False`; passed to `scandir`
      * `recurse`: optional flag, default `True`; if true, recurse
        into subdrectories
  '''
  if name_selector is None:
    name_selector = lambda name: name and not name.startswith('.')
  pending = [dirpath]
  while pending:
    path = pending.pop(0)
    try:
      dirents = pfx_call(os.scandir, path)
    except NotADirectoryError:
      yield False, path
      continue
    if not recurse and include_dirs:
      yield True, path
    if sort_names:
      dirents = sorted(dirents, key=lambda entry: entry.name)
    for entry in dirents:
      name = entry.name
      if not name_selector(name):
        continue
      if only_suffixes or skip_suffixes:
        _, ext = splitext(name)
        if only_suffixes and ext[1:] not in only_suffixes:
          continue
        if skip_suffixes and ext[1:] in skip_suffixes:
          continue
      is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
      if is_dir:
        if recurse:
          pending.append(entry.path)
        if include_dirs:
          yield True, entry.path
      else:
        yield False, entry.path

def scandirpaths(dirpath='.', **scan_kw):
  ''' A shim for `scandirtree` to yield filesystem paths from a directory.

      Parameters:
      * `dirpath`: optional top directory, default `'.'`

      Other keyword arguments are passed to `scandirtree`.
  '''
  for _, fspath in scandirtree(dirpath, **scan_kw):
    yield fspath

def rpaths(dirpath='.', **scan_kw):
  ''' A shim for `scandirtree` to yield relative file paths from a directory.

      Parameters:
      * `dirpath`: optional top directory, default `'.'`

      Other keyword arguments are passed to `scandirtree`.
  '''
  for fspath in scandirpaths(dirpath, **scan_kw):
    yield relpath(fspath, dirpath)

def fnmatchdir(dirpath, fnglob):
  ''' Return a list of the names in `dirpath` matching the glob `fnglob`.
  '''
  return fnfilter(pfx_listdir(dirpath), fnglob)

def update_linkdir(linkdirpath: str, paths: Iterable[str], trim=False):
  ''' Update `linkdirpath` with symlinks to `paths`.
      Remove unused names if `trim`.
      Return a mapping of names in `linkdirpath` to absolute forms of `paths`.
  '''
  # TODO: deal with paths which conflict by basename
  # TODO: hard link mode?
  # TODO: move to cs.fs
  name_map = {basename(path): abspath(path) for path in paths}
  for name, linkpath in sorted(name_map.items()):
    linkpath_short = shortpath(linkpath)
    namepath = joinpath(linkdirpath, name)
    namepath_short = shortpath(namepath)
    try:
      linksto = os.readlink(namepath)
    except FileNotFoundError:
      pass
    except OSError as e:
      if e.errno != errno.EINVAL:
        raise
      # not a symlink
      pfx_call(os.remove, namepath)
    else:
      if linksto == linkpath:
        # symlink good, leave it alone
        continue
      pfx_call(os.remove, namepath)
    pfx_call(os.symlink, linkpath, namepath)

  if trim:
    for name in sorted(os.listdir(linkdirpath)):
      if name.startswith('.'):
        continue
      if name in name_map:
        continue
      namepath = joinpath(linkdirpath, name)
      namepath_short = shortpath(namepath)
      pfx_call(os.remove, namepath)
  return name_map

# pylint: disable=too-few-public-methods
class HasFSPath:
  ''' A mixin for an object with a `.fspath` attribute representing
      a filesystem location.

      The `__init__` method just sets the `.fspath` attribute, and
      need not be called if the main class takes care of that itself.
  '''

  def __init__(self, fspath):
    ''' Save `fspath` as `.fspath`; often done by the parent class.
    '''
    self.fspath = fspath

  def __str__(self):
    return f'{self.__class__.__name__}(fspath={self.shortpath})'

  def __lt__(self, other):
    return self.fspath < other.fspath

  @property
  def shortpath(self):
    ''' The short version of `self.fspath`.
    '''
    try:
      return shortpath(self.fspath)
    except AttributeError:
      return "<no-fspath>"

  def pathto(self, *subpaths):
    ''' The full path to `subpaths`, comprising a relative path
        below `self.fspath`.
        This is a shim for `os.path.join` which requires that all
        the `subpaths` be relative paths.
    '''
    if not subpaths:
      raise ValueError('missing subpaths')
    if any(map(isabspath, subpaths)):
      raise ValueError('all subpaths must be relative paths')
    return joinpath(self.fspath, *subpaths)

  def fnmatch(self, fnglob):
    ''' Return a list of the names in `self.fspath` matching the
        glob `fnglob`.
    '''
    return fnmatchdir(self.fspath, fnglob)

  def listdir(self):
    ''' Return `os.listdir(self.fspath)`. '''
    return os.listdir(self.fspath)

class FSPathBasedSingleton(SingletonMixin, HasFSPath, Promotable):
  ''' The basis for a `SingletonMixin` based on `realpath(self.fspath)`.
  '''

  @classmethod
  def _resolve_fspath(
      cls,
      fspath: Optional[str] = None,
      envvar: Optional[str] = None,
      default_attr: str = 'FSPATH_DEFAULT',
  ):
    ''' Resolve the filesystem path `fspath` using `os.path.realpath`.
        This key is used to identify instances in the singleton registry.

        Parameters:
        * `fspath`: the filesystem path to resolve;
          this may be `None` to use the class defaults
        * `envvar`: the environment variable to consult for a default
          `fspath`; the default for this comes from `cls.FSPATH_ENVVAR`
          if defined
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
      # various sources for the default fspath
      # pylint: disable=no-member
      if envvar is None:
        envvar = getattr(cls, 'FSPATH_ENVVAR', None)
      if envvar is not None:
        fspath = os.environ.get(envvar)
        if fspath is not None:
          return cls.fspath_normalised(fspath)
      default = getattr(cls, default_attr, None)
      if default is not None:
        fspath = default() if callable(default) else expanduser(default)
    if fspath is None:
      raise ValueError('_resolve_fspath: no default fspath')
    return cls.fspath_normalised(fspath)

  @classmethod
  def _singleton_key(cls, fspath=None, **_):
    ''' Each instance is identified by `realpath(fspath)`.
    '''
    return cls._resolve_fspath(fspath)

  # pylint: disable=return-in-init
  ##@typechecked
  def __init__(self, fspath: Optional[str] = None, lock=None):
    ''' Initialise the singleton:

        On the first call:
        - set `.fspath` to `self._resolve_fspath(fspath)`
        - set `._lock` to `lock` (or `cs.threads.NRLock()` if not specified)
    '''
    if '_lock' in self.__dict__:
      return
    fspath = self._resolve_fspath(fspath)
    HasFSPath.__init__(self, fspath)
    if lock is None:
      try:
        from cs.threads import NRLock  # pylint: disable=import-outside-toplevel
      except ImportError:
        lock = Lock()
      else:
        lock = NRLock()
    self._lock = lock

  @classmethod
  def fspath_normalised(cls, fspath: str):
    ''' Return the normalised form of the filesystem path `fspath`,
        used as the key for the singleton registry.

        This default returns `realpath(fspath)`.

        As a contracting example, the `cs.ebooks.kindle.classic.KindleTree`
        class tries to locate the directory containing the book
        database, and returns its realpath, allowing some imprecision.
    '''
    return realpath(fspath)

  @classmethod
  def promote(cls, obj):
    ''' Promote `None` or `str` to a `CalibreTree`.
    '''
    if isinstance(obj, cls):
      return obj
    if obj is None or isinstance(obj, (str, PathLike)):
      return cls(obj)
    raise TypeError(f'{cls.__name__}.promote: cannot promote {r(obj)}')

class RemotePath(
    namedtuple('RemotePath', 'host fspath'),
    HasFSPath,
    Promotable,
):
  ''' A representation of a remote filesystem path (local if `host` is `None`).

      This is useful for things like `rsync` targets.
  '''

  # dummy init since namedtuple does not have one
  def __init__(self, host, fspath):
    pass

  @staticmethod
  def str(host, fspath):
    ''' Return the string form of a remote path.
    '''
    if host is None:
      if ':' in fspath.split('/')[0]:
        fspath = f'./{fspath}'
      return fspath
    return f'{host}:{fspath}'

  def __str__(self):
    ''' Return the string form of this path.
    '''
    return self.str(self.host, self.fspath)

  @classmethod
  def from_str(cls, pathspec: str):
    ''' Produce a RemotePath` from `pathspec`, a path with an
        optional leading `[user@]rhost:` prefix.
    '''
    if ':' in pathspec.split('/')[0]:
      host, fspath = pathspec.split(':', 1)
    else:
      host, fspath = None, pathspec
    return cls(host, fspath)

  def from_tuple(cls, host_fspath: tuple):
    ''' Produce a RemotePath` from `host_fspath`, a `(host,fspath)` 2-tuple.
    '''
    return cls(*host_fspath)

SHORTPATH_PREFIXES_DEFAULT = (('$HOME/', '~/'),)

@fmtdoc
def shortpath(
    fspath,
    prefixes=None,
    *,
    collapseuser=False,
    foldsymlinks=False,
):
  ''' Return `fspath` with the first matching leading prefix replaced.

      Parameters:
      * `prefixes`: optional list of `(prefix,subst)` pairs
      * `collapseuser`: optional flag to enable detection of user
        home directory paths; default `False`
      * `foldsymlinks`: optional flag to enable detection of
        convenience symlinks which point deeper into the path;
        default `False`

      The `prefixes` is an optional iterable of `(prefix,subst)`
      to consider for replacement.  Each `prefix` is subject to
      environment variable substitution before consideration.
      The default `prefixes` is from `SHORTPATH_PREFIXES_DEFAULT`:
      `{SHORTPATH_PREFIXES_DEFAULT!r}`.
  '''
  if prefixes is None:
    prefixes = SHORTPATH_PREFIXES_DEFAULT
  if collapseuser or foldsymlinks:
    # our resolved path
    leaf = Path(fspath).resolve()
    assert leaf.is_absolute()
    # Paths from leaf-parent to root
    parents = list(leaf.parents)
    paths = [leaf, *parents]

    def statkey(S):
      ''' A 2-tuple of `(S.st_dev,Sst_info)`.
      '''
      return S.st_dev, S.st_ino

    def pathkey(P):
      ''' A 2-tuple of `(st_dev,st_info)` from `P.stat()`
          or `None` if the `stat` fails.
      '''
      try:
        S = P.stat()
      except OSError:
        return None
      return statkey(S)

    base_s = None
    if collapseuser:
      # scan for the lowest homedir in the path
      pws = {}
      for i, path in enumerate(paths):
        try:
          st = path.stat()
        except OSError:
          continue
        try:
          pw = pws[st.st_uid]
        except KeyError:
          pw = pws[st.st_uid] = getpwuid(st.st_uid)
        if path.samefile(pw.pw_dir):
          base_s = '~' if pw.pw_uid == os.geteuid() else f'~{pw.pw_name}'
          paths = paths[:i + 1]
          break
    # a list of (Path,display) from base to leaf
    paths_as = [[path, None] for path in reversed(paths)]
    # note the display for the base Path
    paths_as[0][1] = base_s
    if not foldsymlinks:
      keep_as = paths_as
    else:
      # look for symlinks which point deeper into the path
      # map path keys to (i,path)
      pathindex_by_key = {
          sk: i
          for sk, i in
          ((pathkey(path_as[0]), i) for i, path_as in enumerate(paths_as))
          if sk is not None
      }
      # scan from the base towards the leaf, excluding the leaf
      i = 0
      keep_as = []
      while i < len(paths_as) - 1:
        path_as = paths_as[i]
        keep_as.append(path_as)
        path = path_as[0]
        skip_to_i = None
        try:
          for entry in os.scandir(path):
            if not entry.name.isalpha():
              continue
            try:
              if not entry.is_symlink():
                continue
              sympath = os.readlink(entry.path)
            except OSError:
              continue
            # only consider clean subpaths
            if not is_valid_rpath(sympath):
              continue
            # see the the symlink resolves to a path entry
            try:
              pathndx = pathindex_by_key[statkey(entry.stat())]
            except KeyError:
              continue
            if skip_to_i is None or pathndx > skip_to_i:
              # we will advance to skip_to_i
              skip_to_i = pathndx
              # note the symlink name for this component
              paths_as[skip_to_i][1] = entry.name
          i = i + 1 if skip_to_i is None else skip_to_i
        except OSError:
          i += 1
    parts = [
        (path_as[1] or (path_as[0].path if i == 0 else path_as[0].name))
        for i, path_as in enumerate(keep_as)
    ]
    parts.append(paths_as[-1][1] or leaf.name)
    fspath = os.sep.join(parts)
  # replace leading prefix
  for prefix, subst in prefixes:
    prefix = expandvars(prefix)
    if fspath.startswith(prefix):
      return subst + fspath[len(prefix):]
  return fspath

def longpath(path, prefixes=None):
  ''' Return `path` with prefixes and environment variables substituted.
      The converse of `shortpath()`.
  '''
  if prefixes is None:
    prefixes = SHORTPATH_PREFIXES_DEFAULT
  for prefix, subst in prefixes:
    if path.startswith(subst):
      path = prefix + path[len(subst):]
      break
  return expandvars(path)

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

def findup(dirpath: str, criterion: Union[str, Callable[[str], Any]]) -> str:
  ''' Walk up the filesystem tree looking for a directory where
      `criterion(fspath)` is not `None`, where `fspath` starts at `dirpath`.
      Return the result of `criterion(fspath)`.
      Return `None` if no such path is found.

      Parameters:
      * `dirpath`: the starting directory
      * `criterion`: a `str` or a callable accepting a `str`

      If `criterion` is a `str`, look for the existence of
      `os.path.join(fspath,criterion)`.

      Example:

          # find a directory containing a `.envrc` file
          envrc_path = findup('.', '.envrc')

          # find a Tagger rules file for the Downloads directory
          rules_path = findup(expanduser('~/Downloads', '.taggerrc')
  '''
  if isinstance(criterion, str):
    # passing a name looks for that name (usually a basename) with
    # respect to each directory path
    find_name = criterion

    def test_subpath(dirpath):
      testpath = joinpath(dirpath, find_name)
      if pfx_call(existspath, testpath):
        return testpath
      return None

    criterion = test_subpath
  if not isabspath(dirpath):
    dirpath = abspath(dirpath)
  while True:
    found = pfx_call(criterion, dirpath)
    if found is not None:
      return found
    new_dirpath = dirname(dirpath)
    if new_dirpath == dirpath:
      break
    dirpath = new_dirpath
  return None
