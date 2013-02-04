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
if sys.hexversion < 0x02060000: from sets import Set as set
from cs.lex import parseline, strlist
from cs.fileutils import saferename

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
  ''' A list with a shorter str().
  '''

  def __str__(self):
    return "[" + ",".join(str(e) for e in self) + "]"
