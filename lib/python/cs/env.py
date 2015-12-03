#!/usr/bin/python
#
# Environment access and substitution.
#   - Cameron Simpson <cs@zip.com.au>
#

DISTINFO = {
    'description': "a few environment related functions",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'requires': ['cs.lex'],
}

import os
import string
import types
from cs.lex import get_qstr

def getLogin(uid=None):
  import pwd
  if uid is None:
    uid = os.geteuid()
  return pwd.getpwuid(uid)[0]

def getHomeDir(login=None):
  import pwd
  if login is None:
    login = getLogin()
  return pwd.getpwnam(login)[5]

def getenv(var, default=None, environ=None, dosub=False):
  ''' Fetch environment value.
      `var`: name of variable to fetch.
      `default`: default value if not present. If not specified or None,
          raise KeyError.
      `environ`: environment mapping, default os.environ.
      `dosub`: if true, use envsub() to perform environment variable
          substitution on `default` if it used. Default value is False.
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
      `environ`: environment mapping, default os.environ.
      `default`: value to substitute for unknown vars;
              if `default` is None a ValueError is raised.
  '''
  if environ is None:
    environ = os.environ
  return get_qstr(s, 0, q=None, environ=environ, default=default)[0]

def varlog(environ=None):
  ''' Return the default base for logs for most cs.* modules.
  '''
  if environ is None:
    environ = os.environ
  return envsub('$HOME/var/log')
