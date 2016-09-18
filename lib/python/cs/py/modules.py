#!/usr/bin/python
#
# Convenience functions related to modules and importing.
#   - Cameron Simpson <cs@zip.com.au> 21dec2014
#

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

import sys
import os.path

def import_module_name(module_name, name, path=None, lock=None):
  ''' Import `module_name` and return the value of `name` within it.
      `module_name`: the module name to import.
      `name`: the name within the module whose value is returned;
              if `name` is None, return the module itself.
      `path`: an array of paths to use as sys.path during the import.
      `lock`: a lock to hold during the import (recommended).
  '''
  import importlib
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
  ''' Generator yielding .py pathnames involved in a module.
  '''
  from cs.logutils import X
  X("M = %r", dir(M))
  initpath = M.__file__
  moddir = os.path.dirname(initpath)
  for dirpath, dirnames, filenames in os.walk(moddir):
    for filename in filenames:
      if filename.endswith('.py'):
        yield os.path.join(dirpath, filename)
