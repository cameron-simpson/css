import os
import os.path
import errno
import sys
import dircache
import string
import time
from cs.lex import parseline

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

# debug_level:
#   0 - quiet
#   1 - progress reporting
#   2 - verbose progress reporting
#   3 or more - more verbose, and activates the debug() function
#
debug_level=0
if sys.stderr.isatty(): debug_level=1
if 'DEBUG' in os.environ \
   and len(os.environ['DEBUG']) > 0 \
   and os.environ['DEBUG'] != "0":
    debug_level=3

def debugif(level,*args):
  if debug_level >= level:
    warn(*args)

def progress(*args): debugif(1,*args)
def verbose(*args):  debugif(2,*args)
def debug(*args):    debugif(3,*args)

def cmderr(*args):
  global cmd
  sys.stderr.write(cmd)
  sys.stderr.write(": ")
  warn(*args)

def isodate(when=None):
  if when is None: when=time.localtime()
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

# fill out an array with None to be at least "length" elements long
def padlist(l,length):
  if len(l) < length:
    l+=[None]*(length-len(l))

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

# Accept a dict of the for key->(fn, help_string)
# and perform entered commands.
def runCommandPrompt(fnmap,prompt=None):
  if prompt is None: promt=cmd+"> "
  ok=True
  while True:
    try:
      line=raw_input(cmd+"> ")
    except EOFError:
      break

    if line is None:
      return ok
   
    line=chomp(line)
   
    line=string.lstrip(chomp(line))
    if len(line) == 0 or line[0] == "#":
      continue
   
    words=parseline(line)
    if words is None:
      xit=1
      cmderr("syntax error in line:", line)
      continue
   
    op=words[0]
    words=words[1:]
    if op in fnmap:
      if not fnmap[op][0](op,words):
        ok=False
      continue
   
    xit=1
    cmderr("unsupported operation:", op)
    ops=fnmap.keys()
    ops.sort()
    for op in ops:
      warn("  %-7s %s" % (op,fnmap[op][1]))

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
