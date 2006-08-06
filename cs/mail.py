import os
import os.path
import email.Parser
import StringIO
from cs.misc import warn

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

class Maildir:
  def __init__(self,path):
    self.__path=path
    self.__parser=email.Parser.Parser()

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
