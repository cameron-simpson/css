#!/usr/bin/python
#

r'''
Gimmicks and hacks to make some of my other modules more robust and
less demanding of others.
'''

import sys

__version__ = '20200418.1'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
}

_logging_functions = {}

# Pull logging functions from cs.logutils is available, otherwise logging.
# This defers the cs.logutils import, breaking circular imports.
def _logging_stub(func_name, *a, **kw):
  try:
    logging_function = _logging_functions[func_name]
  except KeyError:
    try:
      import cs.logutils as logging_module
    except ImportError:
      import logging as logging_module
    _logging_functions['log'] = logging_module.log
    _logging_functions['debug'] = logging_module.debug
    _logging_functions['info'] = logging_module.info
    _logging_functions['warning'] = logging_module.warning
    _logging_functions['error'] = logging_module.error
    _logging_functions['exception'] = logging_module.exception
    logging_function = _logging_functions[func_name]
  if (sys.version_info.major, sys.version_info.minor) >= (3, 8):
    stacklevel = kw.pop('stacklevel', 1)
    kw['stacklevel'] = stacklevel + 1
  return logging_function(*a, **kw)

# Wrapper for `log()` which does a deferred import.
log = lambda *a, **kw: _logging_stub('log', *a, **kw)

# Wrapper for `debug()` which does a deferred import.
debug = lambda *a, **kw: _logging_stub('debug', *a, **kw)

# Wrapper for `info()` which does a deferred import.
info = lambda *a, **kw: _logging_stub('info', *a, **kw)

# Wrapper for `warning()` which does a deferred import.
warning = lambda *a, **kw: _logging_stub('warning', *a, **kw)

# Wrapper for `error()` which does a deferred import.
error = lambda *a, **kw: _logging_stub('error', *a, **kw)

# Wrapper for `exception()` which does a deferred import.
exception = lambda *a, **kw: _logging_stub('exception', *a, **kw)
