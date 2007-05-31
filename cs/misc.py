import os
import os.path
import errno
import sys
import dircache
import string
import time
from StringIO import StringIO
from cs.lex import parseline, strlist

def setcmd(ncmd):
  global cmd, cmd_, cmd__
  cmd=ncmd
  cmd_=cmd+':'
  cmd__=cmd_+' '

setcmd(os.path.basename(sys.argv[0]))

warnFlushesUpd=True

# print to stderr
def warn(*args):
  import cs.upd
  global warnFlushesUpd
  if cs.upd.active:
    upd=cs.upd.default()
    if not warnFlushesUpd:
      oldUpd=upd.state()
    upd.out('')

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

  if not warnFlushesUpd:
    if cs.upd.active:
      upd.out(oldUpd)

# debug_level:
#   0 - quiet
#   1 - progress reporting
#   2 - verbose progress reporting
#   3 or more - more verbose, and activates the debug() function
#
debug_level=0
if sys.stderr.isatty():
  debug_level=1
  import cs.upd
  cs.upd.default()
if 'DEBUG' in os.environ \
   and len(os.environ['DEBUG']) > 0 \
   and os.environ['DEBUG'] != "0":
    debug_level=3

debug_level_stack=[]
def pushDebug(newlevel=True):
  global debug_level, debug_level_stack
  if type(newlevel) is bool:
    if newlevel: newlevel=3
    else:        newlevel=debug_level
  debug_level_stack.append(debug_level)
  debug_level=newlevel

def popDebug():
  global debug_level, debug_level_stack
  debug_level=debug_level_stack.pop()

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
def out(*args):
  import cs.upd
  if ifdebug(1):
    if cs.upd.active:
      if len(*args) > 0:
        cs.upd.out(" ".join(args))
    else:
      warn(*args)

def cmderr(*args):
  global cmd_
  warn(*[cmd_]+list(args))

def die(*args):
  assert False, strlist(args," ")

def tb():
  import traceback
  import cs.upd
  global cmd_
  if cs.upd.active:
    upd=cs.upd.default()
    oldUpd=upd.state()
    upd.out('')

  for elem in traceback.format_list(traceback.extract_stack())[:-1]:
    for line in elem.split("\n"):
      if len(line) > 0:
        sys.stderr.write(cmd__)
        sys.stderr.write(line)
        sys.stderr.write("\n")

  if cs.upd.active:
    upd.out(oldUpd)

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
  icontext="expected exactly one value"
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

class CanonicalDict(dict):
  def __init__(self,map=None,canonical=None):
    dict.__init__(self)
    self.__canon=canonical
    if map is not None:
      for k in map.keys():
        self[k]=map[k]

  def __canonical(self,key):
    if self.__canon is None:
      return key

    ckey=self.__canon(key)
    debug("CanonicalDict: %s => %s" % (key, ckey))
    return ckey

  def __getitem__(self,key):
    return dict.__getitem__(self,self.__canonical(key))

  def __setitem__(self,key,value):
    dict.__setitem__(self,self.__canonical(key),value)

  def __delitem__(self,key):
    dict.__delitem__(self,self.__canonical(key))

  def __contains__(self,key):
    ckey=self.__canonical(key)
    yep=dict.__contains__(self,ckey)
    if ifdebug():
      warn("CanonicalDict: __contains__(%s(%s)) = %s" % (key,ckey,`yep`))
    return yep

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
    except OSError:
      if sys.exc_value[0] == errno.EACCES:
	return None
      else:
        continue
    if len(opath) == 0:
      newpath=os.path.basename(newpath)
    return newpath

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
    from sets import Set
    flagv=self[self.__flagfield]
    if flagv is None:
      flagv=Set(())
    else:
      if type(flagv) is not Set:
        flagv=Set(flagv)

    return flagv

  def testFlag(self,flag):
    return flag in self.__flaglist()

  def setFlag(self,flag):
    if not self.testFlag(flag):
      flagv=self.__flaglist()
      flagv.add(flag)
      if type(self[self.__flagfield]) is str:
        flagv=" ".join(flagv)
      self[self.__flagfield]=flagv

  def clearFlag(self,flag):
    if self.testFlag(flag):
      flagv=self.__flaglist()
      flagv.remove(flag)
      if type(self[self.__flagfield]) is str:
        flagv=" ".join(flagv)
      self[self.__flagfield]=flagv

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

def fromBS(s):
  ''' Read an extensible value from the front of a string.
      Continuation octets have their high bit set.
      The value is big-endian.
  '''
  o=ord(s[0])
  n=o&0x7f
  used=1
  while o & 0x80:
    o=ord(s[used])
    used+=1
    n=(n<<7)|(o&0x7f)
  return (n,s[used:])

def fromBSfp(fp):
  ''' Read an extensible value from a file.
  '''
  s=c=fp.read(1)
  if len(s) == 0:
    return None
  while ord(c)&0x80:
    c=fp.read(1)
    assert len(c) == 1, "unexpected EOF"
    s+=c
  (n,s)=fromBS(s)
  assert len(s) == 0
  return n

def toBS(n):
  ''' Encode a value as an entensible octet sequence for decode by
      getExtensibleOctets().
  '''
  s=chr(n&0x7f)
  n>>=7
  while n > 0:
    s=chr(0x80|(n&0x7f))+s
    n>>=7
  return s
