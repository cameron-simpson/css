import os
import os.path
import errno
import sys
import dircache
import string
import time

cmd=os.path.basename(sys.argv[0])

# print to stderr
def warn(*args):
  first=True
  for arg in args:
    if first:
      first=False
    else:
      sys.stderr.write(' ')

    sys.stderr.write(str(arg))
    sys.stderr.flush()

  sys.stderr.write("\n")
  sys.stderr.flush()

def debug(*args):
  if 'DEBUG' in os.environ and len(os.environ['DEBUG']) > 0 and os.environ['DEBUG'] != "0":
    warn(*args)

def cmderr(*args):
  global cmd
  sys.stderr.write(cmd)
  sys.stderr.write(": ")
  warn(*args)

def isodate(when=time.localtime()):
  return time.strftime("%Y-%m-%d",when)

# trim trailing newline if present, a la the perl func of the same name
def chomp(s):
  slen=len(s)
  if slen > 0 and s[-1:] == '\n':
    return s[:-1]
  return s

def extend(arr,items):
  for i in items:
    arr.append(i)

def index(seq,val):
  for i in xrange(len(seq)-1):
    if val == seq[i]:
      return i
  return -1

def uniq(ary):
  u=[]
  d={}
  for a in ary:
    if a not in d:
      u.append(a)
      d[a]=None

  return u

def listpermute(lol):
  # empty list
  if len(lol) == 0:
    return ()

  # single element list
  if len(lol) == 1:
    return [[l] for l in lol[0]]

  # short circuit if any element is empty
  for l in lol:
    if len(l) == 0:
      return ()

  left=lol[0]
  right=lol[1:]
  pright=listpermute(right)
  return [[item]+ritem for item in left for ritem in pright]

def dict2ary(d,keylist=None):
  if keylist is None: keylist=sort(keys(d))
  return [ [k,d[k]] for k in keylist ]

def mkdirn(path):
  opath=path
  if len(path) == 0:
    path='.'+os.sep

  if path[-1:] == os.sep:
    dir=path[:-1]
    pfx=''
  else:
    dir=os.path.dirname(path)
    if len(dir) == 0: dir='.'
    pfx=os.path.basename(path)

  if not os.path.isdir(dir):
    return None

  maxn=0
  pfxlen=len(pfx)
  for base in dircache.listdir(dir):
    if len(base) > pfxlen and base[:pfxlen] == pfx:
      numeric=True
      for c in base[pfxlen:]:
	if c not in string.digits:
	  numeric=False
	  break
      if numeric:
	sfxval=int(base[pfxlen:])
	if sfxval > maxn:
	  maxn=sfxval

  newn=maxn
  while True:
    newn=newn+1
    newpath=path+str(newn)
    try:
      os.mkdir(newpath)
      if len(opath) == 0:
	newpath=os.path.basename(newpath)
      return newpath
    except OSError:
      if sys.exc_value[0] == errno.EACCES:
	return None

def mailsubj(addrs,subj,body):
  import cs.sh
  pipe=cs.sh.vpopen(('set-x','mailsubj','-s',subj)+addrs,mode="w")
  pipe.write(body)
  if len(body) > 0 and body[-1] != '\n':
    pipe.write('\n')

  return pipe.close() is None

# trivial wrapper for extension in subclasses
class SeqWrapper:
  def __init__(self,seq):
    self.__seq=seq

  def getSeq(self):
    return self.__seq

  def __len__(self):
    return len(self.__seq)

  def __getitem__(self,key):
    return self.__seq[key]

  def __setitem__(self,key,value):
    self.__seq[key]=value

  def __delitem__(self,key):
    del(self.__seq[key])

  def __iter__(self):
    return [i for i in self.__seq]
#    for i in self.__seq:
#      yield i

  def _updateAllValues(self,newvalues):
    self.__seq=newvalues

  def __repr__(self):
    return `self.__seq`

""" an object with an ordered set of keys eg SQL table row
"""
class OrderedKeys:
  def __init__(self,names=None):
    if names is not None:
      self.setKeyOrder(names)

  def setKeyOrder(self,names):
    # compute column name index
    ##print "SETKEYORDER: ",`names`
    self.__keys=names
    self.__keyIndex={}
    i=0
    for name in names:
      self.__keyIndex[name]=i
      i+=1

  def keyIndex(self,key=None):
    if key is None:
      return self.__keyIndex
    return self.__keyIndex[key]

  def keys(self):
    ##print "ORDEREDKEYS.keys()=",`self.__keys`
    return self.__keys

  def __iterkeys__(self):
    return self.keys()
#    for k in self.keys():
#      yield k

class IndexedSeqWrapper(OrderedKeys,SeqWrapper):
  def __init__(self,seq,names=None):
    ##print "init IndexedSeqWrapper"
    ##print "  seq=",`seq`
    ##print "  keys=",`names`
    SeqWrapper.__init__(self,seq)
    OrderedKeys.__init__(self,names)

  def __getitem__(self,key):
    if type(key) is not int:
      key=self.keyIndex(key)
    return SeqWrapper.__getitem__(self,key)

  def __setitem__(self,key,value):
    if type(key) is not int:
      key=self.keyIndex(key)
    return SeqWrapper.__setitem__(self,key,value)

  def __repr__(self):
    d={}
    okeys=self.keys()
    for i in xrange(0,len(okeys)):
      d[okeys[i]]=self[i]
    return `d`

class HasFlags:
  """ A collection of strings whose presence may be tested. """
  def __init__(self,flagfield='FLAGS'):
    self.__flagfield=flagfield

  def __flaglist(self):
    flagv=self[self.__flagfield]
    if flagv is None:
      return ()

    return string.split(flagv)

  def testFlag(self,flag):
    flags=self.__flaglist()
    return flag in flags

  def setFlag(self,flag):
    if not self.testFlags(flag):
      flagv=self[self.__flagfield]
      if flagv is None: flagv=''
      self[self.__flagfield]=flagv+' '+flag

  def clearFlag(self,flag):
    if self.testFlag(flag):
      self[self.__flagfield]=string.join([f for f in self.__flaglist() if f != flag])
