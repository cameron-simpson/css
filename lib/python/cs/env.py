#!/usr/bin/python
#
# Environment access and substitution.
#   - Cameron Simpson <cs@cskk.id.au>
#

r'''
Some environment related functions.

* LOGDIR, VARRUN, FLAGDIR: lambdas defining standard places used in other modules

* envsub: replace substrings of the form '$var' with the value of 'var' from `environ`.

* getenv: fetch environment value, optionally performing substitution
'''

import os
from cs.lex import get_qstr

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.lex'],
}

# various standard locations used in the cs.* modules
LOGDIR = lambda environ=None: _get_standard_var('LOGDIR', '$HOME/var/log', environ=environ)
VARRUN = lambda environ=None: _get_standard_var('VARRUN', '$HOME/var/run', environ=environ)
FLAGDIR = lambda environ=None: _get_standard_var('FLAGDIR', '$HOME/var/flags', environ=environ)

def _get_standard_var(varname, default, environ=None):
  if environ is None:
    environ = os.environ
  value = environ.get(varname)
  if value is None:
    value = envsub(default, environ)
  return value

def getenv(var, default=None, environ=None, dosub=False):
  ''' Fetch environment value.

      Parameters:
      * `var`: name of variable to fetch.
      * `default`: default value if not present. If not specified or None,
          raise KeyError.
      * `environ`: environment mapping, default `os.environ`.
      * `dosub`: if true, use envsub() to perform environment variable
          substitution on `default` if it used. Default value is `False`.
  '''
  if environ is None:
    environ = os.environ
  value = environ.get(var)
  if value is None:
    if default is None:
      raise KeyError("getenv: $%s: unknown variable" % (var,))
    value = default
    if dosub:
      value = envsub(value, environ=environ)
  return value

def envsub(s, environ=None, default=None):
  ''' Replace substrings of the form '$var' with the value of 'var' from environ.

      Parameters:
      * `environ`: environment mapping, default `os.environ`.
      * `default`: value to substitute for unknown vars;
              if `default` is `None` a `ValueError` is raised.
  '''
  if environ is None:
    environ = os.environ
  return get_qstr(s, 0, q=None, environ=environ, default=default)[0]
