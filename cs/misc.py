import os
import os.path
import errno
import sys
import dircache
import string
import time
from StringIO import StringIO
from cs.lex import parseline, strlist

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

def ifdebug(level=3):
  ''' Tests if the debug_level is above the specified threshold.
  '''
  return debug_level >= level

def debugif(level,*args):
  ''' Emits the specified warning if the debug_level is above the specified
      threshold.
  '''
  if debug_level >= level:
    warn(*args)

def ifprogress(): return ifdebug(1)
def ifverbose():  return ifdebug(2)

def progress(*args): debugif(1,*args)
def verbose(*args):  debugif(2,*args)
def debug(*args):    debugif(3,*args)

def cmderr(*args):
  global cmd
  sys.stderr.write(cmd)
  sys.stderr.write(": ")
  warn(*args)

def die(*args):
  assert False, strlist(args," ")

_seq=0
def seq():
  global _seq
  _seq+=1
  return _seq

def all(gen):
  ''' Returns all the values from a generator as an array.
  '''
  return [x for x in gen]
def isodate(when=None):
  from time import localtime, strftime
  if when is None: when=localtime()
  return strftime("%Y-%m-%d",when)

def a2date(s):
  from date import date
  from time import strptime
  return date(*strptime(s, "%Y-%m-%d")[0:3])

def exactlyOne(list,context=None):
  ''' Returns the first element of a list, but requires there to be exactly one.
  '''
  icontext="expected exactly one"
  if context is not None:
    icontext=icontext+" for "+context
  if len(list) == 0:
    raise IndexError, icontext+", got none"
  if len(list) > 1:
    raise IndexError, icontext+", got "+str(len(list))+": "+strlist(list)
  return list[0]

def winsize(f):
  '''	Return a (rows,columns) tuple or None for the specified file object.
  '''
  fd=os.dup(f.fileno())	# obtain fresh fd to pass to the shell
  sttycmd="stty -a <&"+str(fd)+" 2>/dev/null"
  stty=os.popen(sttycmd).read()
  os.close(fd)
  import re
  m=re.compile(r' rows (\d+); columns (\d+)').search(stty)
  if not m:
    return None
  return (int(m.group(1)),int(m.group(2)))

# trim trailing newline if present, a la the perl func of the same name
def chomp(s):
  slen=len(s)
  if slen > 0 and s[-1:] == '\n':
    return s[:-1]
  return s

def extend(arr,items):
  warn("replace use of cs.misc.extend with array extend builtin")
  for i in items:
    arr.append(i)

def index(seq,val):
  warn("replace use of cs.misc.index with array index/find builtin")
  for i in xrange(len(seq)-1):
    if val == seq[i]:
      return i
  return -1

def uniq(ary,canonical=None):
  u=[]
  d={}
  for a in ary:
    if canonical is None:
      ca=a
    else:
      ca=canonical(a)

    if ca not in d:
      u.append(a)
      d[ca]=None

  return u

class CanonicalSeq:
  def __init__(self,seq,canonical=None):
    self.__canon=canonical
    self.__seq=seq

  def __canonical(self,key):
    if self.__canon is None:
      return key
    return self.__canon(key)

  def __repr__(self):
    return `self.__seq`

  def __len__(self):
    return len(self.__seq)

  def __getitem__(self,ndx):
    return self.__seq[ndx]

  def __setitem__(self,ndx,value):
    self.__seq[ndx]=value

  def __iter__(self):
    for i in self.__seq:
      yield i

  def __delitem__(self,ndx):
    del self.__seq[ndx]

  def __contains__(self,value):
    cv=self.__canonical(value)
    for v in self.__seq:
      if self.__canonical(v) == cv:
	return True

    return False

class CanonicalDict:
  def __init__(self,map,canonical=None):
    self.__canon=canonical
    self.__dict={}
    if map is not None:
      self.update(map)

  def __repr__(self):
    d={}
    for k in self.keys():
      d[k]=self[k]
    return `d`

  def __canonical(self,key):
    if self.__canon is None:
      return key
    return self.__canon(key)

  def __len__(self):
    return len(self.__dict)

  def keys(self):
    return [self.__dict[self.__canonical(k)][0] for k in self.__dict.keys()]

  def update(self,map):
    for k in map.keys():
      self[k]=map[k]

  def __getitem__(self,key):
    return self.__dict[self.__canonical(key)][1]

  def __setitem__(self,key,value):
    self.__dict[self.__canonical(key)]=(key,value)

  def __iter__(self):
    for k in self.__dict.keys():
      yield k
  def iterkeys(self):
    return self.__iter__()

  def __delitem__(self,key):
    del self.__dict[self.__canonical(key)]

  def __contains__(self,key):
    return self.__canonical(key) in self.__dict

class LCDict(CanonicalDict):
  def __init__(self,dict):
    CanonicalDict.__init__(self,dict,canonical=string.lower)

class LCSeq(CanonicalSeq):
  def __init__(self,seq):
    CanonicalSeq.__init__(self,seq,canonical=string.lower)

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

def tmpdir():
  if 'TMPDIR' in os.environ:
    tmpdir=os.environ['TMPDIR']
    if len(tmpdir) > 0:
      return tmpdir

  return '/tmp'

def tmpdirn(tmp=None):
  if tmp is None: tmp=tmpdir()
  return mkdirn(os.path.join(tmp,os.path.basename(sys.argv[0])))

def mailsubj(addrs,subj,body):
  import cs.sh
  pipe=cs.sh.vpopen(('set-x','mailsubj','-s',subj)+addrs,mode="w")
  pipe.write(body)
  if len(body) > 0 and body[-1] != '\n':
    pipe.write('\n')

  return pipe.close() is None

def netgroup(*names):
  ''' Return hosts in a netgroup. Requires the 'ngr' script.
  '''
  import cs.sh
  return [chomp(line) for line in cs.sh.vpopen(('ngr',)+names, mode="r")]

def runCommandPrompt(fnmap,prompt=None):
  ''' Accept a dict of the for key->(fn, help_string)
      and perform entered commands.
  '''
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

def saferename(oldpath,newpath):
  ''' Rename a path using os.rename(), but raise an exception if the target
      path already exists. Slightly racey.
  '''
  try:
    os.lstat(newpath)
    raise OSError(errno.EEXIST)
  except OSError, e:
    if e.errno != errno.ENOENT:
      raise e
    os.rename(oldpath,newpath)

def trysaferename(oldpath,newpath):
  ''' A saferename() that returns True on success, False on failure.
  '''
  try:
    saferename(oldpath,newpath)
  except:
    return False
  return True
