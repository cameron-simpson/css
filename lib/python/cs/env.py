import os
import string
import types
from cs.lex import get_identifier

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

def envsub(s, environ=None, bare=None, default=None):
  ''' Replace substring of the form '$var' with the value of 'var' from environ.
      `environ`: environment mapping, default os.environ.
      `bare`: string to replace a '$' with no following identifier;
              a bare '$' raises ValueError if this is not specified.
      `default`: value to substitute for unknown vars;
              if `default` is None a ValueError is raised.
  '''
  if environ is None:
    environ = os.environ
  strs = []
  opos = 0
  while True:
    pos = s.find('$', opos)
    if pos < 0:
      strs.append(s[opos:])
      break
    if pos > opos:
      strs.append(s[opos:pos])
    id, offset = get_identifier(s, pos+1)
    if id:
      value = environ.get(id, default)
      if value is None:
        raise ValueError("unknown envvar name $%s, offset %d: %s"
                         % (id, pos, s))
      strs.append(value)
    else:
      if bare is not None:
        strs.append(bare)
      else:
        raise ValueError("missing envvar name, offset %d: %s" % (pos, s))
    opos = offset
  return ''.join(strs)
