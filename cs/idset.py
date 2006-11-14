import os
import os.path
import dircache
import re
from cs.misc import warn, chomp, mkdirn

numeric_re = re.compile(r'^(0|[0-9][0-9]*)$')

class IdSet:
  def __init__(self,name,basedir=None):
    if basedir is None:
      basedir=os.path.join(os.environ['HOME'],'var','idsets')

    self._path=os.path.join(basedir,name)
    self.__entries={}

  def keys(self):
    for id in dircache.listdir(self._path):
      if numeric_re.match(id):
	yield int(id)

  def _newEntry(self,id):
    return _IdSetEntry(self,id)

  def __getitem__(self,key):
    if type(key) is not int:
      raise TypeError, "__getitem__ expects an int, received"+`type(key)`

    if key in self.__entries:
      return self.__entries[key]

    path=os.path.join(self._path,str(key))
    if not os.path.isdir(path):
      raise IndexError
    self.__entries[key]=self._newEntry(key)
    return self.__entries[key]

  def newid(self):
    return int(os.path.basename(mkdirn(self._path+os.sep)))

  def needId(self,id):
    if type(id) is not int:
      raise TypeError, "needId expects an int, received"+`type(id)`
    path=os.path.join(self._path,str(id))
    if not os.path.isdir(path):
      os.mkdir(path)

  def __iter__(self):
    for id in self.keys():
      yield self[id]

  def idByKey(self,key,value):
    for id in self.keys():
      E=self[id]
      if key in E.keys() and E[key] == value:
	return id
    return None

  def entryByKey(self,key,value):
    id=self.idByKey(key,value)
    if id is None:
      return None
    return self[id]

class _IdSetEntry:
  def __init__(self,parent,id):
    self._parent=parent
    self._id=id
    self.__path=os.path.join(self._parent._path,str(id))

  def __validkey(self,key):
    return len(key) > 0 and key[0] != '.' and key.find(os.sep) < 0

  def keys(self):
    for id in dircache.listdir(self.__path):
      if self.__validkey(id):
	yield id

  def _keypath(self,key):
    if not self.__validkey(key):
      raise IndexError
    return os.path.join(self.__path,key)

  def __getitem__(self,key):
    try:
      fp=file(self._keypath(key))
    except IOError, e:
      raise IndexError
    warn("read", self._keypath(key))
    lastline=None
    for line in fp:
      lastline=line
    if lastline is None:
      raise IndexError
    return chomp(lastline)

  def __setitem(self,key,value):
    fp=file(self._keypath(key),'w')
    fp.write(value)
    fp.write('\n')

  def __delitem__(self,key):
    os.remove(self._keypath(key))
