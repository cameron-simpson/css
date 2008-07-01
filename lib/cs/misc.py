from types import *
import os
import os.path
import errno
import sys
import string
import time
from StringIO import StringIO
from thread import allocate_lock
from cs.lex import parseline, strlist

def setcmd(ncmd):
  global cmd, cmd_, cmd__
  cmd=ncmd
  cmd_=cmd+':'
  cmd__=cmd_+' '

setcmd(os.path.basename(sys.argv[0]))

class _NoUpd:
  def out(self,s):
    if len(s) > 0:
      print >>sys.stderr, s
      sys.stderr.flush()
    return ''
  def nl(self,s):
    print >>sys.stderr, s
    sys.stderr.flush()
    return ''
  def state(self):
    return ''
  def close(self):
    pass
  def closed(self):
    return False
  def without(self,func,*args,**kw):
    return func(*args,**kw)
_defaultUpd=_NoUpd()

# print to stderr
def warn(*args):
  global _defaultUpd
  return _defaultUpd.nl(" ".join([str(s) for s in args]))

# debug_level:
#   0 - quiet
#   1 - progress reporting
#   2 - verbose progress reporting
#   3 or more - more verbose, and activates the debug() function
#
def setDebug(newlevel):
  if newlevel is None:
    newlevel=0
    if sys.stderr.isatty():
      newlevel=1
    env=os.environ.get('DEBUG_LEVEL','')
    if len(env) > 0 and env.isdigit():
      newlevel=int(env)
    else:
      env=os.environ.get('DEBUG','')
      if len(env) > 0 and env != "0":
        newlevel=3
  global debug_level, isdebug, isverbose, isprogress
  debug_level=newlevel
  isdebug=(debug_level >= 3)
  isverbose=(debug_level >= 2)
  isprogress=(debug_level >= 1)

setDebug(None)
if isdebug:
  def D(fmt,*args):
    sys.stderr.write(fmt % args)
else:
  def D(*args):
    pass

debug_level_stack=[]
def pushDebug(newlevel):
  global debug_level, debug_level_stack
  debug_level_stack.append(debug_level)
  setDebug(newlevel)

def popDebug():
  global debug_level, debug_level_stack
  setDebug(debug_level_stack.pop())

class DebugLevel:
  def __init__(self,level=None):
    global debug_level
    if level is None:
      level=debug_level
    self.level=level
  def __enter__(self):
    pushDebug(self.level)
  def __exit__(self,exc_type,exc_value,traceback):
    popDebug()
    return False

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
  global _defaultUpd
  if len(args) > 0 and ifdebug(1):
    return _defaultUpd.out(" ".join(args))
  return _defaultUpd.state()

def cmderr(*args):
  global cmd_
  warn(*[cmd_]+list(args))

def TODO(msg):
  if ifverbose():
    logFnLine(msg, frame=sys._getframe(1), prefix="TODO(%s)"%cmd)

def FIXME(msg):
  logFnLine(msg, frame=sys._getframe(1), prefix="FIXME(%s)"%cmd)

def tb(limit=None):
  import traceback
  global cmd__
  global _defaultUpd
  upd=_defaultUpd
  oldUpd=upd.out('')

  n=0
  for elem in traceback.format_list(traceback.extract_stack())[:-1]:
    for line in elem.split("\n"):
      if len(line) > 0:
        sys.stderr.write(cmd__)
        sys.stderr.write(line)
        sys.stderr.write("\n")
    if limit is not None:
      n+=1
      if n >= limit:
        break

  upd.out(oldUpd)

_logPath=None
_logFP=sys.stderr
def logTo(logpath=None):
  global _logPath, _logFP
  if logpath is None:
    return _logFP
  TODO("port cs.misc.logTo() etc to logger module")
  _logFP=open(logpath,"a")
  _logPath=logpath
def _logline(line,mark):
  global _logPath, _logFP
  when=time.time()
  pfx="%d [%s] " % (when, mark)
  try:
    print >>_logFP, pfx, line.replace("\n", "\n%*s" % (len(pfx)+1, " "))
    _logFP.flush()
    if isdebug and _logFP is not sys.stderr:
      pfx="%s: %s:" % (cmd, mark)
      print >>sys.stderr, pfx, line.replace("\n", "\n%*s" % (len(pfx)+1, " "))
  except IOError:
    pass
def logLine(line,mark=None):
  if mark is None:
    mark=cmd
  return _defaultUpd.without(_logline,line,mark)
def logFnLine(line,frame=None,prefix=None,mark=None):
  ''' Log a line citing the calling function.
  '''
  if frame is None:
    frame=sys._getframe(1)
  line="%s [%s(), %s:%d]" \
       % (line, frame.f_code.co_name, frame.f_code.co_filename, frame.f_lineno)
  if prefix is not None:
    line=prefix+": "+line
  return logLine(line,mark=mark)

class Loggable:
  ''' Base class for things that will use the above functions.
  '''
  def __init__(self,mark):
    self.__logMark=cmd+"."+mark
  def logmark(self,mark=None):
    if mark is None:
      mark=self.__logMark
    else:
      mark=self.__logMark+"."+mark
    return mark
  def log(self,line,mark=None):
    logLine(line,mark=self.logmark(mark))
  def logfn(self,line,mark=None,frame=None):
    if frame is None:
      frame=sys._getframe(1)
    logFnLine(line,mark=self.logmark(mark),frame=frame)
  def logTime2(self,tag,func,*args,**kw):
    global reportElapsedTimeTo
    return reportElapsedTimeTo(self.log,tag,func,*args,**kw)
  def logTime(self,tag,func,*args,**kw):
    t, result = self.logTime2(tag,func,*args,**kw)
    return result

def elapsedTime(func,*args,**kw):
  ''' Call a function with the supplied arguments.
      Return start time, end time and return value.
  '''
  t0=time.time()
  result=func(*args,**kw)
  t1=time.time()
  return t0, t1, result

def reportElapsedTime(tag,func,*args,**kw):
  t, result = reportElapsedTimeTo(None,tag,func,*args,**kw)
  return result
def reportElapsedTimeTo(logfunc,tag,func,*args,**kw):
  ''' Call a function with the supplied arguments.
      Return its return value.
      If isdebug, report elapsed time for the function.
  '''
  if isdebug:
    old=out("%.100s" % " ".join((cmd_,tag,"...")))
  t0, t1, result = elapsedTime(func, *args, **kw)
  t=t1-t0
  if isdebug:
    if t >= 0.01:
      if logfunc is None:
        logfunc=logLine
      logfunc("%6.4fs %s"%(t,tag))
    out(old)
  return t, result

T_SEQ='ARRAY'
T_MAP='HASH'
T_SCALAR='SCALAR'
def objFlavour(obj):
  """ Return the ``flavour'' of an object:
      T_MAP: DictType, DictionaryType, objects with an __keys__ or keys attribute.
      T_SEQ: TupleType, ListType, objects with an __iter__ attribute.
      T_SCALAR: Anything else.
  """
  t=type(obj)
  if t in (TupleType, ListType): return T_SEQ
  if t in (DictType, DictionaryType): return T_MAP
  if hasattr(obj,'__keys__') or hasattr(obj,'keys'): return T_MAP
  if hasattr(obj,'__iter__'): return T_SEQ
  return T_SCALAR

__seq=0
__seqLock=allocate_lock()
def seq():
  global __seq
  global __seqLock
  __seqLock.acquire()
  __seq+=1
  n=__seq
  __seqLock.release()
  return n

def all(gen):
  ''' Returns all the values from a generator as an array.
  '''
  assert False, "OBSOLETE: all() is a python builtin meaning 'is every item true?', use list() or tuple()"

def isodate(when=None):
  from time import localtime, strftime
  if when is None: when=localtime()
  return strftime("%Y-%m-%d",when)

def a2date(s):
  from date import date
  from time import strptime
  return date(*strptime(s, "%Y-%m-%d")[0:3])

def exactlyOne(list,context=None):
  cmderr("OBSOLETE CALL TO cs.misc.exactlyOne(), use the() instead")
  tb()
  return the(list,context)

def the(list,context=None):
  ''' Returns the first element of an iterable, but requires there to be exactly one.
  '''
  icontext="expected exactly one value"
  if context is not None:
    icontext=icontext+" for "+context

  first=True
  for elem in list:
    if first:
      it=elem
      first=False
    else:
      raise IndexError, "%s: more than one element" % icontext

  if first:
    raise IndexError, "%s: no elements" % icontext
    
  return it

def eachOf(gs):
  ''' Return all the instances from a list of generators as a single generator.
  '''
  for g in gs:
    for i in g:
      yield i

def winsize(f):
  '''   Return a (rows,columns) tuple or None for the specified file object.
  '''
  fd=os.dup(f.fileno()) # obtain fresh fd to pass to the shell
  sttycmd="stty -a <&"+str(fd)+" 2>/dev/null"
  stty=os.popen(sttycmd).read()
  os.close(fd)
  import re
  m=re.compile(r' rows (\d+); columns (\d+)').search(stty)
  if not m:
    return None
  return (int(m.group(1)),int(m.group(2)))

# trim trailing newline, returning trimmied line
# unlike perl, requires the newline to be present
def chomp(s):
  assert s[-1] == '\n'
  return s[:-1]

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

class WithUCAttrs:
  ''' An object where access to obj.FOO accesses obj['FOO']
      if FOO is all upper case.
  '''
  def __getattr__(self,attr):
    if attr.isalpha() and attr.isupper():
      return self[attr]
    return dict.__getattr__(self,attr)
  def __setattr__(self,attr,value):
    if attr.isalpha() and attr.isupper():
      self[attr]=value
      return
    self.__dict__[attr]=value

class DictUCAttrs(dict, WithUCAttrs):
  ''' A dict where access to obj.FOO accesses obj['FOO']
      if FOO is all upper case.
  '''
  def __init__(self,fill=None):
    if fill is None:
      fill=()
    dict.__init__(self,fill)

class WithUC_Attrs:
  ''' An object where access to obj.FOO accesses obj['FOO']
      if FOO matches ^[A-Z][_A-Z0-9]*.
  '''
  def __uc_(self,s):
    if s.isalpha() and s.isupper():
      return True
    if len(s) < 1:
      return False
    if not s[0].isupper():
      return False
    for c in s[1:]:
      if c != '_' and (not c.isupper() or c.isdigit()):
        return False
    return True
  def __getattr__(self,attr):
    if self.__uc_(attr):
      return self[attr]
    return dict.__getattr__(self,attr)
  def __setattr__(self,attr,value):
    if self.__uc_(attr):
      self[attr]=value
      return
    self.__dict__[attr]=value

class DictUC_Attrs(dict, WithUC_Attrs):
  ''' A dict where access to obj.FOO accesses obj['FOO']
      if FOO matches ^[A-Z][_A-Z0-9]*.
  '''
  def __init__(self,fill=None):
    if fill is None:
      fill=()
    dict.__init__(self,fill)

class DictAttrs(dict):
  def __init__(self,d=None):
    dict.__init__()
    if d is not None:
      for k in d.keys():
        self[k]=d[k]

  def __getattr__(self,attr):
    return self[attr]
  def __setattr__(self,attr,value):
    self[attr]=value

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

def maxFilenameSuffix(dir,pfx):
  from dircache import listdir
  maxn=None
  pfxlen=len(pfx)
  for tail in [ e[pfxlen:] for e in listdir(dir)
                if len(e) > pfxlen and e.startswith(pfx)
              ]:
    if tail.isdigit():
      n=int(tail)
      if maxn is None:
        maxn=n
      elif maxn < n:
        maxn=n
  return maxn

def tmpfilename(dir=None):
  if dir is None:
    dir=tmpdir()
  pfx = ".%s.%d." % (cmd,os.getpid())
  n=maxFilenameSuffix(dir,pfx)
  if n is None: n=0
  return "%s%d" % (pfx,n)

def mkdirn(path):
  opath=path
  if len(path) == 0:
    path='.'+os.sep

  if path.endswith(os.sep):
    dir=path[:-len(os.sep)]
    pfx=''
  else:
    dir=os.path.dirname(path)
    if len(dir) == 0: dir='.'
    pfx=os.path.basename(path)

  if not os.path.isdir(dir):
    return None

  # do a quick scan of the directory to find
  # if any names of the desired form already exist
  # in order to start after them
  maxn=maxFilenameSuffix(dir,pfx)
  if maxn is None:
    newn=0
  else:
    newn=maxn

  while True:
    newn += 1
    newpath=path+str(newn)
    try:
      os.mkdir(newpath)
    except OSError, e:
      if sys.exc_value[0] == errno.EEXIST:
        # taken, try new value
        continue
      cmderr("mkdir(%s): %s" % (newpath,e))
      return None
    if len(opath) == 0:
      newpath=os.path.basename(newpath)
    return newpath

def tmpdir():
  tmpdir=os.environ.setdefault('TMPDIR','')
  if len(tmpdir) == 0:
    tmpdir='/tmp'
  return tmpdir

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
  from cs.sh import vpopen
  return [ chomp(line) for line in vpopen(('ngr',)+names, mode="r") ]

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
   
    line=string.lstrip(line)
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
      if type(flagv) is str:
        flagv=flagv.split(',')
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
        flagv=",".join(flagv)
      self[self.__flagfield]=flagv

  def clearFlag(self,flag):
    if self.testFlag(flag):
      flagv=self.__flaglist()
      flagv.remove(flag)
      if type(self[self.__flagfield]) is str:
        flagv=",".join(flagv)
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
      Return None at EOF.
  '''
  ##debug("fromBSfp: reading first BS byte...")
  s=c=fp.read(1)
  ##debug("fromBSfp: c=0x%02x" % ord(c))
  if len(s) == 0:
    return None
  while ord(c)&0x80:
    ##debug("fromBSfp: reading another BS byte...")
    c=fp.read(1)
    assert len(c) == 1, "unexpected EOF"
    ##debug("fromBSfp: c=0x%02x" % ord(c))
    s+=c
  (n,s)=fromBS(s)
  ##debug("fromBSfp: n==%d" % n)
  assert len(s) == 0
  return n

def toBS(n):
  ''' Encode a value as an entensible octet sequence for decode by
      fromBS().
  '''
  s=chr(n&0x7f)
  n>>=7
  while n > 0:
    s=chr(0x80|(n&0x7f))+s
    n>>=7
  return s
