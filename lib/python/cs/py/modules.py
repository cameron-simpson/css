#!/usr/bin/python
#

''' Convenience functions related to modules and importing.
'''

import importlib
from importlib.machinery import SourceFileLoader
from importlib.util import spec_from_loader, module_from_spec
from inspect import getmodule
import os.path
import sys
from cs.context import stackattrs
from cs.gimmicks import warning
from cs.pfx import Pfx

__version__ = '20220606-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.context', 'cs.gimmicks', 'cs.pfx'],
}

def import_module_name(module_name, name, path=None, lock=None):
  ''' Import `module_name` and return the value of `name` within it.

      Parameters:
      * `module_name`: the module name to import.
      * `name`: the name within the module whose value is returned;
        if `name` is `None`, return the module itself.
      * `path`: an array of paths to use as sys.path during the import.
      * `lock`: a lock to hold during the import (recommended).
  '''
  if lock:
    with lock:
      return import_module_name(module_name, name, path)
  osyspath = sys.path
  if path:
    sys.path = path
  try:
    M = importlib.import_module(module_name)
  except ImportError as e:
    # pylint: disable=raise-missing-from
    raise ImportError("no module named %r: %s: %s" % (module_name, type(e), e))
  finally:
    if path:
      sys.path = osyspath
  if M is not None:
    if name is None:
      return M
    try:
      return getattr(M, name)
    except AttributeError as e:
      # pylint: disable=raise-missing-from
      raise ImportError(
          "%s: no entry named %r: %s: %s" % (module_name, name, type(e), e)
      )
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
      which may affect multithreaded applications.
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

# pylint: disable=too-many-branches
def direct_imports(src_filename, module_name=None):
  ''' Crudely parse `src_filename` for `import` statements.
      Return the set of directly imported module names.

      If `module_name` is not `None`,
      resolve relative imports against it.
      Otherwise, relative import names are returned unresolved.

      This is a very simple minded source parse.
  '''
  subnames = set()
  with Pfx(src_filename):
    with open(src_filename, encoding='utf-8') as codefp:
      for lineno, line in enumerate(codefp, 1):
        with Pfx(lineno):
          if line.startswith('import ') or line.startswith('from '):
            line = line.strip()
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
            if module_name and subimport.startswith('.'):
              if subimport == '.':
                subimport = module_name
              else:
                # resolve relative import name
                preparts = module_name.split('.')
                subimport = subimport[1:]
                while subimport.startswith('.'):
                  preparts.pop(-1)
                  subimport = subimport[1:]
                if preparts:
                  if subimport:
                    preparts.append(subimport)
                subimport = '.'.join(preparts)
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
            "package not available; the following extras pull it in: %r" %
            (sorted(from_extras),)
        )
        raise
      # pylint: disable=raise-missing-from
      raise RuntimeError(
          "import_extra called with a package not listed in DISTINFO[extras_requires]=%r"
          % (DISTINFO['extras_requires'],)
      )
