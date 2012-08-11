from __future__ import print_function
import os
import os.path
import errno
import sys
import logging
info = logging.info
warning = logging.warning
import string
import time
from threading import Lock
if sys.hexversion < 0x02060000: from sets import Set as set
from cs.lex import parseline, strlist
from cs.fileutils import saferename

def setcmd(ncmd):
  ''' Set the cs.misc.cmd string and friends.
  '''
  global cmd, cmd_, cmd__
  cmd = ncmd
  cmd_ = cmd + ':'
  cmd__ = cmd_ + ' '

setcmd(os.path.basename(sys.argv[0]))

class _NullUpd:
  ''' A dummy class with the same duck type as cs.upd.Upd
      used when cs.upd has not be instantiated by a program.
  '''
  def out(self, s):
    if len(s) > 0:
      print >>sys.stderr, s
      sys.stderr.flush()
    return ''
  def nl(self, s):
    print >>sys.stderr, s
    sys.stderr.flush()
    return ''
  def state(self):
    return ''
  def close(self):
    pass
  def closed(self):
    return False
  def without(self, func, *args, **kw):
    return func(*args, **kw)
_defaultUpd = _NullUpd()

def tb(limit=None):
  ''' Print a stack backtrace.
  '''
  import traceback
  global cmd__
  global _defaultUpd
  upd = _defaultUpd
  oldUpd = upd.out('')

  n = 0
  for elem in traceback.format_list(traceback.extract_stack())[:-1]:
    for line in elem.split("\n"):
      if len(line) > 0:
        sys.stderr.write(cmd__)
        sys.stderr.write(line)
        sys.stderr.write("\n")
    if limit is not None:
      n += 1
      if n >= limit:
        break

  upd.out(oldUpd)

_logPath = None
_logFP = sys.stderr
def logTo(logpath=None):
  ''' Cause logging to go to the specified filename.
      If logpath is omitted or None, return the current
      log file object, which starts as sys.stderr.
  '''
  global _logPath, _logFP
  if logpath is None:
    return _logFP
  TODO("port cs.misc.logTo() etc to logger module")
  _logFP = open(logpath, "a")
  _logPath = logpath
def _logline(line, mark):
  ''' Log a line with a prefix mark.
  '''
  global _logPath, _logFP
  when = time.time()
  pfx = "%d [%s]" % (when, mark)
  try:
    print >>_logFP, pfx, line.replace("\n", "\n%*s" % (len(pfx)+1, " "))
    _logFP.flush()
    if isdebug and _logFP is not sys.stderr:
      pfx = "%s: %s:" % (cmd, mark)
      print >>sys.stderr, pfx, line.replace("\n", "\n%*s" % (len(pfx)+1, " "))
  except IOError:
    pass

def logLine(line, mark=None):
  ''' Log a line, with optional prefix mark.
  '''
  if mark is None:
    mark = cmd
  return _defaultUpd.without(_logline, line, mark)

def logFnLine(line, frame=None, prefix=None, mark=None):
  ''' Log a line citing the calling function.
  '''
  if frame is None:
    frame = sys._getframe(1)
  elif type(frame) is int:
    frame = sys._getframe(frame)
  line = "%s [%s(), %s:%d]" \
         % (line, frame.f_code.co_name, frame.f_code.co_filename, frame.f_lineno)
  if prefix is not None:
    line = prefix+": "+line
  return logLine(line, mark=mark)

def elapsedTime(func, *args, **kw):
  ''' Call a function with the supplied arguments.
      Return start time, end time and return value.
  '''
  t0 = time.time()
  result = func(*args, **kw)
  t1 = time.time()
  return t0, t1, result

def reportElapsedTime(tag, func, *args, **kw):
  ''' Call a function with the supplied arguments.
      Return its return value.
      If isdebug, report elapsed time for the function.
  '''
  t, result = reportElapsedTimeTo(None, tag, func, *args, **kw)
  return result

def reportElapsedTimeTo(logfunc, tag, func, *args, **kw):
  ''' Call a function with the supplied arguments.
      Return its return value.
      If isdebug, report elapsed time for the function.
  '''
  if isdebug:
    old = out("%.100s" % " ".join((cmd_, tag, "...")))
  t0, t1, result = elapsedTime(func, *args, **kw)
  t = t1-t0
  if True: ##t >= 0.01:
    if logfunc is None:
      logfunc = logLine
    logfunc("TIME %6.4fs %s"%(t, tag))
  if isdebug:
    out(old)
  return t, result

class Loggable:
  ''' Base class for things that will use the above functions.
  '''
  def __init__(self, mark):
    self.__logMark = cmd+"."+mark

  def logmark(self, mark=None):
    ''' Set the log line prefix mark.
    '''
    if mark is None:
      mark = self.__logMark
    else:
      mark = self.__logMark+"."+mark
    return mark

  def log(self, line, mark=None):
    ''' Log a line with optional prefix mark.
    '''
    logLine(line, mark=self.logmark(mark))

  def logfn(self, line, mark=None, frame=None):
    ''' Log a line with optional prefix mark, along with the calling function.
    '''
    if frame is None:
      frame = sys._getframe(1)
    logFnLine(line, mark=self.logmark(mark), frame=frame)

  def logTime2(self, tag, func, *args, **kw):
    global reportElapsedTimeTo
    return reportElapsedTimeTo(self.log, tag, func, *args, **kw)

  def logTime(self, tag, func, *args, **kw):
    t, result = self.logTime2(tag, func, *args, **kw)
    return result

  def OBSOLETElogException(self, exc_type, exc_value, traceback, doSimpleExceptionReport=False):
    self.logfn("EXCEPTION: %s(%s)" % (exc_type, exc_value))
    if doSimpleExceptionReport:
      ##NO##from cs.excutils import NoExceptions
      NoExceptions.simpleExceptionReport(exc_type, exc_value, traceback, mark=self.__logMark)

T_SEQ = 'ARRAY'
T_MAP = 'HASH'
T_SCALAR = 'SCALAR'
def objFlavour(obj):
  """ Return the ``flavour'' of an object:
      T_MAP: DictType, DictionaryType, objects with an __keys__ or keys attribute.
      T_SEQ: TupleType, ListType, objects with an __iter__ attribute.
      T_SCALAR: Anything else.
  """
  t = type(obj)
  if isinstance(t, (tuple, list)):
    return T_SEQ
  if isinstance(t, dict):
    return T_MAP
  if hasattr(obj, '__keys__') or hasattr(obj, 'keys'):
    return T_MAP
  if hasattr(obj, '__iter__'):
    return T_SEQ
  return T_SCALAR

__seq = 0
__seqLock = Lock()
def seq():
  ''' Allocate a new sequential number.
      Useful for creating unique tokens.
  '''
  global __seq
  global __seqLock
  __seqLock.acquire()
  __seq += 1
  n = __seq
  __seqLock.release()
  return n

def isodate(when=None):
  ''' Return a date in ISO8601 YYYY-MM-DD format.
  '''
  from time import localtime, strftime
  if when is None: when=localtime()
  return strftime("%Y-%m-%d", when)

def a2date(s):
  ''' Create a date object from an ISO8601 YYYY-MM-DD date string.
  '''
  from date import date
  from time import strptime
  return date(*strptime(s, "%Y-%m-%d")[0:3])

def the(list, context=None):
  ''' Returns the first element of an iterable, but requires there to be
      exactly one.
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
      raise IndexError("%s: got more than one element (%s, %s, ...)"
                        % (icontext, it, elem)
                      )

  if first:
    raise IndexError("%s: got no elements" % (icontext,))

  return it

def eachOf(gs):
  ''' Return all the instances from a list of generators as a single generator.
  '''
  for g in gs:
    for i in g:
      yield i

def get0(seq, default=None):
  ''' Return first element of a sequence, or the default.
  '''
  for i in seq:
    return i
  return default

def winsize(f):
  '''   Return a (rows, columns) tuple or None for the specified file object.
  '''
  fd = os.dup(f.fileno()) # obtain fresh fd to pass to the shell
  sttycmd = "stty -a <&" + str(fd) + " 2>/dev/null"
  stty = os.popen(sttycmd).read()
  os.close(fd)
  import re
  m = re.compile(r' rows (\d+); columns (\d+)').search(stty)
  if not m:
    return None
  return (int(m.group(1)), int(m.group(2)))

# trim trailing newline, returning trimmied line
# unlike perl, requires the newline to be present
def chomp(s):
  assert s[-1] == '\n'
  return s[:-1]

def extend(arr, items):
  warning("replace use of cs.misc.extend with array extend builtin")
  for i in items:
    arr.append(i)

def index(seq, val):
  warning("replace use of cs.misc.index with array index/find builtin")
  for i in xrange(len(seq)-1):
    if val == seq[i]:
      return i
  return -1

def uniq(ary, canonical=None):
  assert False, "uniq() should be superceded by set()"
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
  def __getattr__(self, attr):
    if attr.isalpha() and attr.isupper():
      return self[attr]
    return dict.__getattr__(self, attr)
  def __setattr__(self, attr, value):
    if attr.isalpha() and attr.isupper():
      self[attr]=value
      return
    self.__dict__[attr]=value

class DictUCAttrs(dict, WithUCAttrs):
  ''' A dict where access to obj.FOO accesses obj['FOO']
      if FOO is all upper case.
  '''
  def __init__(self, fill=None):
    if fill is None:
      fill=()
    dict.__init__(self, fill)

class WithUC_Attrs:
  ''' An object where access to obj.FOO accesses obj['FOO']
      if FOO matches ^[A-Z][_A-Z0-9]*.
  '''
  def __uc_(self, s):
    if s.isalpha() and s.isupper():
      return True
    if len(s) < 1:
      return False
    if not s[0].isupper():
      return False
    for c in s[1:]:
      if c != '_' and not (c.isupper() or c.isdigit()):
        return False
    return True
  def __getattr__(self, attr):
    if self.__uc_(attr):
      return self[attr]
    return dict.__getattr__(self, attr)
  def __setattr__(self, attr, value):
    if self.__uc_(attr):
      self[attr]=value
      return
    self.__dict__[attr]=value

class DictUC_Attrs(dict, WithUC_Attrs):
  ''' A dict where access to obj.FOO accesses obj['FOO']
      if FOO matches ^[A-Z][_A-Z0-9]*.
  '''
  def __init__(self, fill=None):
    if fill is None:
      fill=()
    dict.__init__(self, fill)

class DictAttrs(dict):
  def __init__(self, d=None):
    dict.__init__()
    if d is not None:
      for k in d.keys():
        self[k]=d[k]

  def __getattr__(self, attr):
    return self[attr]
  def __setattr__(self, attr, value):
    self[attr]=value

class CanonicalSeq:
  def __init__(self, seq, canonical=None):
    self.__canon=canonical
    self.__seq=seq

  def __canonical(self, key):
    if self.__canon is None:
      return key
    return self.__canon(key)

  def __repr__(self):
    return repr(self.__seq)

  def __len__(self):
    return len(self.__seq)

  def __getitem__(self, ndx):
    return self.__seq[ndx]

  def __setitem__(self, ndx, value):
    self.__seq[ndx]=value

  def __iter__(self):
    for i in self.__seq:
      yield i

  def __delitem__(self, ndx):
    del self.__seq[ndx]

  def __contains__(self, value):
    cv=self.__canonical(value)
    for v in self.__seq:
      if self.__canonical(v) == cv:
        return True

    return False

class CanonicalDict(dict):
  def __init__(self, map=None, canonical=None):
    dict.__init__(self)
    self.__canon=canonical
    if map is not None:
      for k in map.keys():
        self[k]=map[k]

  def __canonical(self, key):
    if self.__canon is None:
      return key

    ckey=self.__canon(key)
    debug("CanonicalDict: %s => %s", key, ckey)
    return ckey

  def __getitem__(self, key):
    return dict.__getitem__(self, self.__canonical(key))

  def __setitem__(self, key, value):
    dict.__setitem__(self, self.__canonical(key), value)

  def __delitem__(self, key):
    dict.__delitem__(self, self.__canonical(key))

  def __contains__(self, key):
    ckey = self.__canonical(key)
    return dict.__contains__(self, ckey)

class LCDict(CanonicalDict):
  def __init__(self, dict):
    CanonicalDict.__init__(self, dict, canonical=string.lower)

class LCSeq(CanonicalSeq):
  def __init__(self, seq):
    CanonicalSeq.__init__(self, seq, canonical=string.lower)

# fill out an array with None to be at least "length" elements long
def padlist(l, length):
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

def dict2ary(d, keylist=None):
  if keylist is None: keylist=sort(keys(d))
  return [ [k, d[k]] for k in keylist ]

def maxFilenameSuffix(dir, pfx):
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
  pfx = ".%s.%d." % (cmd, os.getpid())
  n=maxFilenameSuffix(dir, pfx)
  if n is None: n=0
  return "%s%d" % (pfx, n)

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
  maxn=maxFilenameSuffix(dir, pfx)
  if maxn is None:
    newn=0
  else:
    newn=maxn

  while True:
    newn += 1
    newpath=path+str(newn)
    try:
      os.mkdir(newpath)
    except OSError as e:
      if sys.exc_value[0] == errno.EEXIST:
        # taken, try new value
        continue
      error("mkdir(%s): %s", newpath, e)
      return None
    if len(opath) == 0:
      newpath=os.path.basename(newpath)
    return newpath

def tmpdir():
  tmpdir=os.environ.setdefault('TMPDIR', '')
  if len(tmpdir) == 0:
    tmpdir='/tmp'
  return tmpdir

def tmpdirn(tmp=None):
  if tmp is None: tmp=tmpdir()
  return mkdirn(os.path.join(tmp, os.path.basename(sys.argv[0])))

def mailsubj(addrs, subj, body):
  import cs.sh
  pipe=cs.sh.vpopen(('set-x', 'mailsubj', '-s', subj)+addrs, mode="w")
  pipe.write(body)
  if len(body) > 0 and body[-1] != '\n':
    pipe.write('\n')

  return pipe.close() is None

def netgroup(*names):
  ''' Return hosts in a netgroup. Requires the 'ngr' script.
  '''
  from cs.sh import vpopen
  return [ chomp(line) for line in vpopen(('ngr', )+names, mode="r") ]

def runCommandPrompt(fnmap, prompt=None):
  ''' Accept a dict of the for key->(fn, help_string)
      and perform entered commands.
  '''
  if prompt is None:
    prompt = cmd+"> "
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
      error("syntax error in line: %s", line)
      continue

    op=words[0]
    words=words[1:]
    if op in fnmap:
      if not fnmap[op][0](op, words):
        ok=False
      continue

    xit=1
    error("unsupported operation: %s", op)
    ops=fnmap.keys()
    ops.sort()
    for op in ops:
      warning("  %-7s %s", op, fnmap[op][1])

# trivial wrapper for extension in subclasses
class SeqWrapper:
  def __init__(self, seq):
    self.__seq=seq

  def getSeq(self):
    return self.__seq

  def __len__(self):
    return len(self.__seq)

  def __getitem__(self, key):
    return self.__seq[key]

  def __setitem__(self, key, value):
    self.__seq[key]=value

  def __delitem__(self, key):
    del(self.__seq[key])

  def __iter__(self):
    return [i for i in self.__seq]
#    for i in self.__seq:
#      yield i

  def _updateAllValues(self, newvalues):
    self.__seq=newvalues

  def __repr__(self):
    return repr(self.__seq)

""" an object with an ordered set of keys eg SQL table row
"""
class OrderedKeys:
  def __init__(self, names=None):
    if names is not None:
      self.setKeyOrder(names)

  def setKeyOrder(self, names):
    # compute column name index
    ##print "SETKEYORDER: ", repr(names)
    self.__keys=names
    self.__keyIndex={}
    i=0
    for name in names:
      self.__keyIndex[name]=i
      i+=1

  def keyIndex(self, key=None):
    if key is None:
      return self.__keyIndex
    return self.__keyIndex[key]

  def keys(self):
    ##print "ORDEREDKEYS.keys()=", repr(self.__keys)
    return self.__keys

  def __iterkeys__(self):
    return self.keys()
#    for k in self.keys():
#      yield k

class IndexedSeqWrapper(OrderedKeys, SeqWrapper):
  def __init__(self, seq, names=None):
    ##print "init IndexedSeqWrapper"
    ##print "  seq=", repr(seq)
    ##print "  keys=", repr(names)
    SeqWrapper.__init__(self, seq)
    OrderedKeys.__init__(self, names)

  def __getitem__(self, key):
    if type(key) is not int:
      key=self.keyIndex(key)
    return SeqWrapper.__getitem__(self, key)

  def __setitem__(self, key, value):
    if type(key) is not int:
      key=self.keyIndex(key)
    return SeqWrapper.__setitem__(self, key, value)

  def __repr__(self):
    d={}
    okeys=self.keys()
    for i in xrange(0, len(okeys)):
      d[okeys[i]]=self[i]
    return repr(d)

class HasFlags:
  """ A collection of strings whose presence may be tested. """
  def __init__(self, flagfield='FLAGS'):
    self.__flagfield=flagfield

  def __flaglist(self):
    flagv=self[self.__flagfield]
    if flagv is None:
      flagv=set(())
    else:
      if type(flagv) is str:
        flagv=flagv.split(',')
      if type(flagv) is not set:
        flagv=set(flagv)

    return flagv

  def testFlag(self, flag):
    return flag in self.__flaglist()

  def setFlag(self, flag):
    if not self.testFlag(flag):
      flagv=self.__flaglist()
      flagv.add(flag)
      if type(self[self.__flagfield]) is str:
        flagv=",".join(flagv)
      self[self.__flagfield]=flagv

  def clearFlag(self, flag):
    if self.testFlag(flag):
      flagv=self.__flaglist()
      flagv.remove(flag)
      if type(self[self.__flagfield]) is str:
        flagv=",".join(flagv)
      self[self.__flagfield]=flagv

def O_str(o, no_recurse=False):
  omit = getattr(o, '_O_omit', ())
  return ( "<%s %s>"
           % ( o.__class__.__name__,
               (    str(o)
                 if type(o) in (tuple,)
                 else
                       "<%s len=%d>" % (type(o), len(o))
                    if type(o) in (set,)
                    else
                       ",".join([ ( "%s=<%s>" % (pattr, type(pvalue).__name__)
                                    if no_recurse else
                                    "%s=%s" % (pattr, pvalue)
                                  )
                                  for pattr, pvalue
                                  in [ (attr, getattr(o, attr))
                                       for attr in sorted(dir(o))
                                       if attr[0].isalpha()
                                          and not attr in omit
                                     ]
                                  if not callable(pvalue)
                                ])
               )
             )
         )

class O(object):
  ''' A bare object subclass to allow storing arbitrary attributes.
      It also has a nicer default str() action.
  '''

  _O_recurse = True

  def __init__(self, **kw):
    ''' Initialise this O.
        Fill in attributes from any keyword arguments if supplied.
        This call can be omitted in subclasses if desired.
    '''
    for k in kw:
      setattr(self, k, kw[k])

  def __str__(self):
    recurse = self._O_recurse
    self._O_recurse = False
    s = O_str(self, no_recurse = not recurse)
    self._O_recurse = recurse
    return s

def unimplemented(func):
  ''' Decorator for stub methods that must be implemented by a stub class.
  '''
  def wrapper(self, *a, **kw):
    raise NotImplementedError("%s.%s(*%s, **%s)" % (type(self), func.__name__, a, kw))
  return wrapper

class slist(list):
  def __str__(self):
    return "[" + ",".join(str(e) for e in self) + "]"
