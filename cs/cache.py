from cs.misc import warn, debug, ifdebug, die

_caches=[]
def overallHitRatio():
  if len(_caches) == 0:
    return None

  hits=0
  misses=0
  for c in _caches:
    (h,m)=c.hitMiss()
    hits+=h
    misses+=m

  total=hits+misses
  if total == 0:
    return None

  return hits/total

class Cache:
  def __init__(self,backend):
    _caches.append(self)
    self.__cache={}
    self.__seq=0
    self.__backend=backend
    self.__hits=0
    self.__misses=0
    self.__flushes=0
    self.__xrefs=[]

  def addCrossReference(self,xref):
    self.__xrefs.append(xref)

  def inCache(self,key):
    if key not in self.__cache:
      return False
    return self.__cache[key] is not None

  def hitMiss(self):
    return (self.__hits, self.__misses)

  def hitRatio(self):
    gets=self.__hits+self.__misses
    if gets == 0:
      return None
    return self.__hits/gets

  def __getattr__(self,attr):
    ##debug("CACHE GETATTR",`attr`)
    return getattr(self.__backend,attr)

  def bump(self):
    self.__seq+=1

  def keys(self):
    return self.__backend.keys()

  def getitems(self,keylist):
    items=self.__backend.getitems(keylist)
    for i in items:
      self.store(i)
    return items

  def findrowByKey(self,key):
    if key in self.__cache:
      value=self.__cache[key]
      if value[0] == self.__seq:
        self.__hits+=1
        ##if ifdebug(): warn("HIT, key =", `key`, "value[] =", `value`)
        return value[1]
      # out of date - discard the cache line
      self.__flushes+=1
      del self.__cache[key]

    self.__misses+=1

    try:
      value=self.__backend[key]
    except IndexError, e:
      value=None

    self.store(value,key)
    return value

  def __getitem__(self,key):
    # Note: we're looking up the backend, _not_ calling some subclass' findrowbykey()
    row=Cache.findrowByKey(self,key)
    if row is None:
      raise IndexError, "no entry with key "+`key`

    return row

  def store(self,value,key=None):
    if key is not None:
      assert type(key) in (tuple, int, long), "store"+`key`+"="+`value`
    else:
      key=value[self.key()]

    self.__cache[key]=(self.__seq,value)
    if value is not None:
      for xref in self.__xrefs:
        xref.store(value)

  def __setitem__(self,key,value):
    self.__backend[key]=value
    self.store(key,value)

  def __delitem__(self,key):
    del self.__backend[key]
    if key in self.__cache:
      # BUG: doesn't undo cross references
      del self.__cache[key]

class CrossReference:
  def __init__(self):
    self.flush()

  def flush(self):
    self.__index={}

  def __getitem__(self,key):
    value=self.find(key)
    if value is None:
      raise IndexError
    return value

  def __delitem__(self,key):
    if key in self.__index:
      del self.__index[key]

  def find(self,key):
    if key not in self.__index:
      try:
        self.__index[key]=self.byKey(key)
      except IndexError:
        self.__index[key]=None

    return self.__index[key]

  def store(self,value):
    key=self.key(value)
    self.__index[self.key(value)]=value
