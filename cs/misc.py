def index(seq,val):
  for i in xrange(len(seq)-1):
    if val == seq[i]: return i
  return -1

def dict2ary(d,keylist=None):
  if keylist is None: keylist=sort(keys(d))
  return [ [k,d[k]] for k in keylist ]

# trivial wrapper for extension in subclasses
class SeqWrapper:
  def __init__(self,seq):
    self.__seq=seq

  def __len__(self):
    return len(self.__seq)

  def __getitem__(self,key):
    return self.__seq[key]

  def __setitem__(self,key,value):
    print "__setitem(",key,"=",value,"): __seq=", `self.__seq`
    self.__seq[key]=value

  def __delitem__(self,key):
    del(self.__seq[key])

  def __iter__(self):
    for i in self.__seq:
      yield i

  def _updateAllValues(self,newvalues):
    self.__seq=newvalues

""" an object with an ordered set of keys eg SQL table row
"""
class HasNameIndex:
  def __init__(self,names=None):
    if names is not None:
      self.initNameIndex(names)

  def initNameIndex(self,names):
    # compute column name index
    self.__names=names
    self.__nameIndex={}
    i=0
    for name in names:
      self.__nameIndex[name]=i
      i+=1

  def getNames(self):
    return self.__names
  def getNameIndex(self):
    return self.__nameIndex

  def lookupNameIndex(self,name):
    return self.__nameIndex[name]

  def __iterkeys__(self):
    for k in self.__nameIndex:
      yield k

  def keys(self):
    return self.__names

class IndexedSeqWrapper(HasNameIndex,SeqWrapper):
  def __init__(self,seq,names=None):
    SeqWrapper.__init__(self,seq)
    HasNameIndex.__init__(self,names)

  def __getitem__(self,key):
    if type(key) is not int:
      key=self.lookupNameIndex(key)
    return SeqWrapper.__getitem__(self,key)

  def __setitem__(self,key,value):
    if type(key) is not int:
      key=self.lookupNameIndex(key)
    return SeqWrapper.__setitem__(self,key,value)

  def __keys__(self):
    return self.getNameIndex()
