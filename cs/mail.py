import os
import os.path
import time
import socket
import email.Parser
import string
import StringIO
import re
from cs.misc import warn, progress, verbose, seq, saferename

def ismhdir(path):
  return os.path.isfile(os.path.join(path,'.mh_sequences'))

def ismaildir(path):
  for subdir in ('new','cur','tmp'):
    if not os.path.isdir(os.path.join(path,subdir)):
      return False
  return True

def maildirify(path):
  for subdir in ('new','cur','tmp'):
    dpath=os.path.join(path,subdir)
    if not os.path.isdir(dpath):
      os.makedirs(dpath)

_delivered=0
def nextDelivered():
  global _delivered
  _delivered+=1
  return _delivered

_MaildirInfo_RE = re.compile(r':(\d+,[^/]*)$')

class Maildir:
  def __init__(self,path):
    self.__path=path
    self.__parser=email.Parser.Parser()

  def mkname(self,info=None):
    now=time.time()
    secs=int(now)
    subsecs=now-secs

    left=str(secs)
    right=socket.gethostname().replace('/','\057').replace(':','\072')
    middle='#'+str(seq())+'M'+str(subsecs*1e6)+'P'+str(os.getpid())+'Q'+str(nextDelivered())

    name=string.join((left,middle,right),'.')
    if info is None:
      return os.path.join('new',name)

    return os.path.join('cur',name+":"+info)

  def keys(self):
    return self.subpaths()

  def subpaths(self):
    for subdir in ('new','cur'):
      subpath=os.path.join(self.__path,subdir)
      for name in os.listdir(subpath):
        if len(name) > 0 and name[0] != '.':
	  yield os.path.join(subdir,name)

  def fullpath(self,subpath):
    return os.path.join(self.__path,subpath)

  def paths(self):
    for subpath in self.subpaths():
      yield self.fullpath(subpath)

  def __iter__(self):
    P=email.Parser.Parser()
    for subpath in self.subpaths():
      yield self[subpath]

  def __getitem__(self,subpath):
    return self.__parser.parse(file(self.fullpath(subpath)))

  def headers(self,subpath):
    fp=file(self.fullpath(subpath))
    headertext=''
    for line in fp:
      headertext+=line
      if len(line) == 0 or line == "\n":
        break

    fp=StringIO.StringIO(headertext)
    return self.__parser.parse(fp, headersonly=True)

  def importPath(self,path):
    info=None
    m=_MaildirInfo_RE.search(path)
    if m:
      info=m.group(1)
      warn("info=["+info+"]")
    progress(path, '=>', self.fullpath(self.mkname(info)))
    saferename(path,self.fullpath(self.mkname(info)))

_maildirs={}
def openMaildir(path):
  if path not in _maildirs:
    verbose("open new Maildir", path)
    _maildirs[path]=Maildir(path)
  return _maildirs[path]
