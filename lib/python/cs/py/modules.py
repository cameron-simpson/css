#!/usr/bin/python
#

''' Convenience functions related to modules, packages and importing.
'''

from collections import namedtuple
import importlib
from importlib.machinery import SourceFileLoader
from importlib.util import spec_from_loader, module_from_spec
from inspect import getmodule
import os
import os.path
import sys
import threading

from cs.context import contextif, stackattrs
from cs.deco import attr
from cs.gimmicks import warning
from cs.lex import is_dotted_identifier
from cs.obj import Refreshable
from cs.pfx import Pfx, pfx

__version__ = '20260531-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires':
    ['cs.context', 'cs.deco', 'cs.gimmicks', 'cs.lex', 'cs.obj', 'cs.pfx'],
}

@pfx
def import_module_name(module_name, name=None, sys_path=None, lock=None):
  ''' Import `module_name` and return the value of `name` within it.

      Parameters:
      * `module_name`: the module name to import.
      * `name`: optional name within the module whose value is returned;
        if `name` is `None`, return the module itself.
      * `sys_path`optional list of paths to use as `sys.path` during the import.
      * `lock`: an optional lock to hold during the import (recommended).

      *Warning*: `sys.path` is modified for the duration of this function,
      which may affect concurrent applications.
  '''
  with contextif(lock):
    if sys_path is None:
      sys_path = sys.path
    with stackattrs(sys, path=sys_path):
      try:
        M = importlib.import_module(module_name)
      except ImportError as e:
        # pylint: disable=raise-missing-from
        raise ImportError(
            f'cannot import {module_name!r}: {e.__class__.__name__}: {e}'
        ) from e
    if M is not None:
      if name is None:
        return M
      try:
        return getattr(M, name)
      except AttributeError as e:
        # pylint: disable=raise-missing-from
        raise ImportError(
            f'module {module_name!r}: no entry named {name!r} ({e.__class__.__name__}:{e})'
        ) from e
    return None

def import_module_from_file(module_name, source_file, sys_path=None):
  ''' Import a specific file as a module instance,
      return the module instance.

      Parameters:
      * `module_name`: the name to assign to the module
      * `source_file`: the source file to load
      * `sys_path`: optional list of paths to set as `sys.path`
        for the duration of this import;
        the default is the current value of `sys.path`

      Note that this is a "bare" import;
      the module instance is not inserted into `sys.modules`.

      *Warning*: `sys.path` is modified for the duration of this function,
      which may affect concurrent applications.
  '''
  if sys_path is None:
    sys_path = sys.path
  with stackattrs(sys, path=sys_path):
    loader = SourceFileLoader(module_name, source_file)
    spec = spec_from_loader(loader.name, loader)
    M = module_from_spec(spec)
    loader.exec_module(M)
  return M

def module_files(M):
  ''' Generator yielding `.py` pathnames involved in a module.
  '''
  initpath = M.__file__
  moddir = os.path.dirname(initpath)
  for dirpath, _, filenames in os.walk(moddir):
    for filename in filenames:
      if filename.endswith('.py'):
        yield os.path.join(dirpath, filename)

def module_attributes(M):
  ''' Generator yielding the names and values of attributes from a module
      which were defined in the module.
  '''
  for attr in dir(M):
    value = getattr(M, attr, None)
    valueM = getmodule(value)
    if valueM is not None and valueM is not M:
      continue
    yield attr, value

def module_names(M):
  ''' Return a list of the names of attributes from a module which were
      defined in the module.
  '''
  return [attr for attr, value in module_attributes(M)]

# TODO: use the AST module to do a real parse?
# pylint: disable=too-many-branches
@pfx
def direct_imports(src_filename, module_name, *, include_indented=False):
  ''' Crudely parse `src_filename` for `import` statements.
      Return the set of directly imported module names.

      Resolve relative imports against `module_name`.

      This is a very simple minded source parse which understands
      nearly nothing about Python.
  '''
  subnames = set()
  with open(src_filename, encoding='utf-8') as codefp:
    for lineno, line in enumerate(codefp, 1):
      if include_indented:
        line = line.strip()
      else:
        line = line.rstrip()
      with Pfx("%d: %s", lineno, line):
        if line.startswith('import ') or line.startswith('from '):
          # quick hack to strip trailing "; second-statement"
          try:
            line, _ = line.split(';', 1)
          except ValueError:
            pass
          words = line.split()
          if not words:
            continue
          word0 = words[0]
          if word0 not in ('from', 'import'):
            continue
          if len(words) < 2:
            continue
          if word0 == 'from' and (len(words) < 4 or words[2] != 'import'):
            continue
          subimport = words[1]
          if subimport.startswith('.'):
            # relative import, resolve against module_name
            subimport0 = subimport
            module_parts = module_name.split('.')
            while subimport.startswith('.'):
              module_parts = module_parts[:-1]
              subimport = subimport[1:]
            if not module_parts:
              # walked off the top of the path
              warning(
                  "import %r walked out of the module %r", subimport0,
                  module_name
              )
              continue
            module_parts.append(subimport or '__init__')
            subimport = '.'.join(module_parts)
          if subimport == module_name:
            # HACK: simplistic parse finding ourself in a docstring
            continue
          if not is_dotted_identifier(subimport):
            warning("ignoring %r, not a dotted identifier", subimport)
            continue
          subnames.add(subimport)
  return subnames

def import_extra(extra_package_name, distinfo):
  ''' Try to import the package named `extra_package_name`
      using `importlib.import_module`. Return the imported package.

      If an `ImportError` is raised,
      riffle through the extras mapping in `distinfo['extras_requires']`
      for the package name, and emit an informative warning
      about the extras which require this package
      and whose use a `pip install` time would bring the package in.
      The original `ImportError` is then reraised.

      If no extra is found this is presumed to be an error by the caller
      and a `RuntimeError` is raised.
      This function is for internal use as:

          pkg = import_extra('some_package', DISTINFO)

      which passes in the source module's `DISTINFO` mapping,
      which I use as the basis for my package distributions.

      A fuller example from my `cs.timeseries` module's
      `plot` command line mode:

          def cmd_plot(self, argv):
            """ Usage: {cmd} datadir days fields...
            """
            try:
              import_extra('plotly', DISTINFO)
            except ImportError as e:
              raise GetoptError(
                  "the plotly package is not installed: %s" % (e,)
              ) from e

      which produces this output:

          timeseries.py: plot: import_extra('plotly'): package not available; the following extras pull it in: ['plotting']
          timeseries.py: the plotly package is not installed: timeseries.py: plot: import_extra('plotly'): No module named 'plotly'
  '''
  with Pfx("import_extra(%r)", extra_package_name):
    try:
      return importlib.import_module(extra_package_name)
    except ImportError:
      from_extras = [
          extra_name
          for extra_name, extra_packages in distinfo['extras_requires'].items()
          if extra_package_name in extra_packages
      ]
      if from_extras:
        warning(
            "package not available; the following extras pull it in: %r",
            sorted(from_extras)
        )
        raise
      # pylint: disable=raise-missing-from
      raise RuntimeError(
          "import_extra called with a package not listed in DISTINFO[extras_requires]=%r"
          % (DISTINFO['extras_requires'],)
      )

class ModuleReloader(Refreshable):
  ''' A reference to an imported module whose `.refresh()` method
      will reload the module if is has been updated.
  '''

  REFRESH_LIFESPAN = 5
  REFRESH_RATELIMIT = 1

  def __init__(
      self,
      module,
      *,
      auto_import=False,
      refresh_lifespan=None,
      refresh_ratelimit=None,
  ):
    ''' Initialie the `ModuleReloader`.

        Parameters:
        * `module`: a `str` module name or a module object with a
          `ModuleSpec` compatible `.__spec__` attribute
        * `auto_import`: optional flag; if true (default `False`
          and `moudle` is a `str`, import the named module now
        * `refresh_lifespan`: optional overide for `ModuleReloader.REFRESH_LIFESPAN`
        * `refresh_ratelimit`: optional overide for `ModuleReloader.REFRESH_RATELIMIT`
    '''
    if isinstance(module, str):
      module_name = module
      try:
        module = sys.modules[module_name]
      except KeyError:
        if auto_import:
          module = importlib.import_module(module_name)
        else:
          module = None
    else:
      module_name = module.__spec__.name
    if refresh_lifespan is not None:
      self.refresh_lifespan = refresh_lifespan
    if refresh_ratelimit is not None:
      self.refresh_ratelimit = refresh_ratelimit
    self.module = module
    self.module_name = module_name
    self.last_mtime = self.mtime
    self.refresh_lock = threading.Lock()

  def __str__(self):
    return f'{self.__class__.__name__}:{self.fspath!r}'

  def __repr__(self):
    return f'{self.__class__.__name__}(module={self.fspath!r}):last_mtime={self.last_mtime}'

  @property
  def fspath(self):
    ''' The filesystem path of the module source file.
    '''
    return self.module.__spec__.origin

  @property
  def mtime(self):
    ''' The last modification time if the module source file, or
        `None` if the module is not yet loaded or it does not exist.
    '''
    if self.module is None:
      return None
    print(f'stat {self.fspath=}')
    try:
      st = os.stat(self.fspath)
    except FileNotFoundError:
      print('  not found, return None')
      return None
    print(f'  return {st.st_mtime}')
    return st.st_mtime

  def _refresh(self, data=None) -> bool:
    ''' The method supporting `Refreshable.refresh()`.
        This returns `False` if:
        - `self.module` is `None` and `self.module_name` is not yet imported
        - `self.mtime` is `None`, since the source file does not exist
        - the source file is not newer than `self.last_mtime`
        - an `importlib.reload()` is attempted and one of `ImportError`,
          `NameError`, `SyntaxError` is raised
        Otherwise the module has been reloaded, `self.last_mtime`
        gets updated and this returns `True`.
    '''
    print("_refresh")
    assert data is None
    if self.module is None:
      try:
        module = sys.modules[self.module_name]
      except KeyError:
        print(f'  {self.module_name=} not yet imported -> False')
        return False
      self.module = module
    mtime = self.mtime
    if mtime is None:
      print("  no self.mtime -> False")
      return False
    if self.last_mtime is not None and self.last_mtime >= mtime:
      print(f'  {self.last_mtime=} <= {mtime=} -> False')
      return False
    print(f'  importlib.reload {self.module.__name__} from {self.fspath}')
    try:
      importlib.reload(self.module)
    except (ImportError, NameError, SyntaxError) as e:
      warning(f'reload {self.fspath=}: {e}')
      return False
    print(f'  reloa ok, update self.last_mtime <= {mtime=}')
    self.last_mtime = mtime
    print('  -> True')
    return True

@attr(module_state={}, lock=threading.Lock())
def reloadif(module_name: str, *, auto_import=False) -> bool:
  ''' Reload a module if it has been modified the last call to
      `reloadif(module_name)`.
      If `auto_reload` is true (default `False`) then import the
      module if it is not yet imported.
      Return the value from the `ModuleReloader.refresh()` call
      (true if the module was reloaded, false otherwise).

      This is built around a registry of `ModuleReloader` instances.

      Reloads are serialised by a `threading.Lock`.
  '''
  lock = reloadif.lock
  module_state = reloadif.module_state
  with lock:
    try:
      MR = module_state[module_name]
    except KeyError:
      MR = module_state[module_name] = ModuleReloader(
          module_name, auto_import=auto_import
      )
    else:
      if auto_import and MR.module is None:
        importlib.import_module(module_name)
    return MR.refresh()

if __name__ == '__main__':
  import sys, time
  cmd, module_name = sys.argv
  while True:
    print(f'reloadif({module_name=})')
    reloadif(module_name, auto_import=True)
    print("SLEEP 2")
    time.sleep(2)
  ##print(M)
  ##breakpoint()
