#!/usr/bin/python
#
''' Convenience functions related to modules and importing.
'''

import importlib
from inspect import getmodule
import os.path
import sys

DISTINFO = {
    'description': "module/import related stuff",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
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
      raise ImportError("%s: no entry named %r: %s: %s" % (module_name, name, type(e), e))
  return None

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
    if getmodule(value) is not M:
      continue
    yield attr, value

def module_names(M):
  ''' Return a list of the names of attributes from a module which were
      defined in the module.
  '''
  return [ attr for attr, value in module_attributes(M) ]
