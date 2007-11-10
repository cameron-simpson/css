import os
import os.path
import dircache
import re
from cs.misc import warn, mkdirn, WithUCAttrs

numeric_re = re.compile(r'^(0|[1-9][0-9]*)$')
def is_idnum(s):
  return s.isdigit() and numeric_re.match(s)
def is_name(s):
  return len(s) > 0 and s[0] != '.' and s.find(os.sep) < 0 and not is_idnum(s)
def is_entryKey(key):
  return len(key) > 0 and key[0] != '.' and key.find(os.sep) < 0

class IdSet(WithUCAttrs):
  def __init__(self,name,basedir=None):
    if basedir is None:
      basedir=os.path.join(os.environ['HOME'],'var','idsets')
    self.path=os.path.join(basedir,name)
    self.__entries={}

  def listdir(self):
    dircache.reset()
    return [ e for e in dircache.listdir(self.path)
             if len(e) > 0 and e[0] != '.'
           ]

  def keys(self):
    return [ int(e) for e in self.listdir() if is_idnum(e) ]

  def names(self):
    for name in self.listdir():
      if is_name(name):
        E=self.byName(name)
        if E is not None:
          yield name

  def byName(self,name):
    if is_name(name):
      try:
        sym=os.readlink(os.path.join(self.path,name))
        if is_idnum(sym):
          id=int(sym)
          E=self[id]
          if E['name'] == name:
            return E
      except:
        pass
    return None

  def _newid(self):
    return int(os.path.basename(mkdirn(self.path+os.sep)))

  def addName(self,name):
    E=self.byName(name)
    assert E is None, "name \"%s\" already exists" % name
    id=self._newid()
    E=self[id]
    E['name']=name
    return E

  def __getitem__(self,key):
    if type(key) is not int:
      assert is_name(key), "illegal name \"%s\"" % key
      E=self.byName(key)
      if E is None:
        E=addName(key)
      return E

    if key not in self.__entries:
      path=os.path.join(self.path,str(key))
      if not os.path.isdir(path):
        os.mkdir(path)
      self.__entries[key]=IdSetEntry(self,key)
    return self.__entries[key]

  def __iter__(self):
    for id in self.keys():
      yield self[id]

  def byValue(self,key,value):
    for id in self.keys():
      E=self[id]
      if key in E and E[key] == value:
        yield E

class IdSetEntry:
  def __init__(self,parent,id):
    self.parent=parent
    self.id=id
    self.path=os.path.join(self.parent.path,str(id))
    self.__values={}

  def reset(self,key=None):
    if key is None:
      self.__values={}
    else:
      if key in self.__values:
        del self.__values[key]

  def is_entryKey(self,key):
    return len(key) > 0 and key[0] != '.' and key.find(os.sep) < 0

  def keys(self):
    for key in dircache.listdir(self.path):
      if is_entryKey(key):
        yield key
      else:
        warn("reject %s"%key)

  def _keypath(self,key):
    assert is_entryKey(key), "invalid key \"%s\"" % key
    return os.path.join(self.path,key)

  def __getitem__(self,key):
    if key in self.__values:
      return self.__values[key]
    assert is_entryKey(key), "invalid key \"%s\"" % key
    path=self._keypath(key)
    if not os.access(path,os.F_OK):
      return None
    lastline=None
    for line in file(path):
      lastline=line
    if lastline is None:
      return None
    assert len(lastline) > 0 and lastline[-1] == '\n', \
      "%s: incomplete last line" % (cmd, path)
    return lastline[:-1]

  def __setitem(self,key,value):
    assert is_entryKey(key), "invalid key \"%s\"" % key
    if type(value) in (int, float):
      value=str(value)
    assert value.find('\n') < 0, "newlines not allowed in entry values"

    if key == "name":
      assert is_name(name), "illegal name \"%s\"" % name
      if "name" in self:
        del self["name"]
    else:
      self.reset(key)

    fp=file(self._keypath(key),'w')
    fp.write(value)
    fp.write('\n')
    fp.close()
    if key == "name":
      symlink(str(self.id), os.path.join(self.parent.path,value))
    self.__values[key]=value

  def __delitem__(self,key):
    assert is_entryKey(key), "invalid key \"%s\"" % key
    if key == "name":
      value=self["name"]
      if is_name(value):
        os.remove(os.path.join(self.parent,value))
    os.remove(self._keypath(key))
    self.reset(key)
