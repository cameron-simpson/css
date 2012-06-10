import os
import string
import types
from cs.lex import get_identifier

def getLogin(uid=None):
  import pwd
  if uid is None: uid=os.geteuid()
  return pwd.getpwuid(uid)[0]
def getHomeDir(login=None):
  import pwd
  if login is None: login=getLogin()
  return pwd.getpwnam(login)[5]

baseEnv={
  'USER':       getLogin,
  'HOME':       getHomeDir,
  'LOGDIR':     '$HOME/var/log',
  'VARRUN':     '$HOME/var/run',
  'TMPDIR':     '/tmp',
}

def getenv(var, default=None, environ=None, dosub=False):
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

def envsub(s, environ=None):
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
    if not id:
      raise ValueError("missing envvar name, offset %d: %s" % (pos, s))
    strs.append(environ.get(id, ''))
    opos = offset
  return ''.join(strs)

class Env(object):
  def __init__(self,environ=None,base=None):
    if environ is None:
      environ = os.environ
    if base is None:
      base = baseEnv
    self.__environ=environ
    self.__base={}
    for k in base.keys():
      self.__base[k]=base[k]

  def get(self,name,dfltval=None,doenvsub=False):
    if name in self.__environ:
      val=self.__environ[name]
    else:
      if dfltval is not None:
        if doenvsub:
          val=self.envsub(dfltval)
        else:
          val=dfltval
      elif name in self.__base:
        base=self.__base[name]
        t=type(base)
        if t is str:
          val=self.envsub(base)
        elif t is types.FunctionType:
          val=base()
        else:
          assert False, "unsupported baseEnv type %s: %r" % (t, base)
      else:
        raise IndexError

      self.__environ[name]=val

    return val

  def __getitem__(self,name):
    return self.get(name)

  def envsub(self,s):
    next=s.find('$')
    if next < 0:
      return s

    expanded=''
    while next >= 0:
      expanded=expanded+s[:next]
      s=s[next+1:]
      endvar=0
      while ( endvar < len(s)
          and ( s[endvar] == '_'
             or s[endvar] in string.ascii_letters
             or (endvar > 0 and s[endvar] in string.digits)
              )):
        endvar=endvar+1

      if endvar == 0:
        expanded=expanded+'$'
      else:
        expanded=expanded+self[s[:endvar]]

      s=s[endvar:]
      next=s.find('$')

    if len(s) > 0:
      expanded=expanded+s

    return expanded

__dfltEnv=Env()

def dflt(envvar,dfltval=None,doenvsub=False):
  return __dfltEnv.get(envvar,dfltval=dfltval,doenvsub=doenvsub)
