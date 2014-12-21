#!/usr/bin/python
#
# Convenience functions related to modules and importing.
#   - Cameron Simpson <cs@zip.com.au> 21dec2014
#

from cs.logutils import error, exception

def import_module_name(module_name, name, path=None, lock=None):
  ''' Import `module_name` and return the value of `name` within it.
      `module_name`: the module name to import
      `name`: the name within the moudle whose value is returned
      `path`: an array of paths to use as sys.path during the import
      `lock`: a lock to hold during the import (recommended)
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
    exception("%s", e)
    M = None
  if path:
    sys.path = osyspath
  if M is not None:
    try:
      return getattr(M, name)
    except AttributeError as e:
      error("%s: no entry named %r: %s", module_name, func_name, e)
  return None
