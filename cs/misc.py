def index(seq,val):
  for i in xrange(len(seq)-1):
    if val == seq[i]: return i
  return -1

# trivial wrapper for extension in subclasses
class SeqWrapper:
  def __init__(self,seq):
    self.seq=seq

  def __len__(self):
    return len(self.seq)

  def __getitem__(self,key):
    return self.seq[key]

  def __setitem__(self,key,value):
    print "__setitem(",key,"=",value,"): seq=", `self.seq`
    self.seq[key]=value

  def __delitem__(self,key):
    del(self.seq[key])

  def __iter__(self):
    for i in self.seq:
      yield i

class HasNameIndex:
  def __init__(self,names=None):
    if names is not None:
      self.initNameIndex(names)

  def initNameIndex(self,names):
    # compute column name index
    self.names=names
    self.nameIndex={}
    i=0
    for name in names:
      self.nameIndex[name]=i
      i+=1

  def getNameIndex(self):
    return self.nameIndex

  def lookupNameIndex(self,name):
    return self.nameIndex[name]

  def __iterkeys__(self):
    for k in self.nameIndex:
      yield k

  def keys(self):
    return self.names

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
