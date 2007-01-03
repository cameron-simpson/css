#!/usr/bin/python

import re
import os
import os.path
from types import *
from cStringIO import StringIO
from cs.lex import skipwhite, lastlinelen
import cs.io
from cs.misc import progress, cmderr, all, debug, ifdebug

T_SEQ='ARRAY'
T_MAP='HASH'
T_SCALAR='SCALAR'

DEFAULT_OPTS={'dictSep': '=>',
              'bareWords': True,
              'charset':   'latin1',
              'inputEncoding': 'latin1',
              'quoteChar': '"',
             }

safeChunkPtn = r'[\-+_a-zA-Z0-9./@]+'
safeChunkRe  = re.compile(safeChunkPtn)
safePrefixRe = re.compile('^'+safeChunkPtn)
safeStringRe = re.compile('^'+safeChunkPtn+'$')
integerRe    = re.compile('^-?[0-9]+$')

def flavour(obj):
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

def h2a(obj,i=None,opts=None):
  return HierOutput(opts).h2a(obj,i=i)

def load(path,opts=None):
  return HierInput(opts).load(path)

def tok(s,opts=None):
  return HierInput(opts).tok(s)

class _Hier:
  def __init__(self,opts=None):
    self.seen={}
    for k in DEFAULT_OPTS.keys():
      debug("Hier: DFLT",`k`,"=",DEFAULT_OPTS[k])
      setattr(self,k,DEFAULT_OPTS[k])
    if opts is not None:
      for k in opts.keys():
        if k in DEFAULT_OPTS:
          debug("Hier: OVERRIDE",`k`,"=",DEFAULT_OPTS[k])
          setattr(self,k,opts[k])

class HierOutput(_Hier):
  def __init__(self,opts=None):
    _Hier.__init__(self,opts=opts)

  def h2a(self,obj,i=None):
    """ ``Hier'' to ``ASCII''- convert an object to text.
        Return a textual representation of an object in Hier format.
        i is the indent mode, default None.
    """
    buf=StringIO()
    self.fp=cs.io.IndentedFile(buf,i)
    self.__h2f(obj)
    e=buf.getvalue()
    buf.close()
    return e

  def __h2f(self,fp,obj):
    """ ``Hier'' to ``ASCII''- convert an object to text.
        Transcribe a textual representation of an object in Hier format to the File fp.
        NB: fp must be a cs.io.IndentedFile
    """
    t=type(obj)
    if isinstance(obj,int):
      self.fp.write(str(obj))
    elif t is FloatType:
      stringEncode(str(obj))
    elif t is BooleanType:
      self.fp.write(int(obj))
    elif isinstance(obj, StringTypes):
      self.stringEncode(obj)
    else:
      if id(obj) in seen:
        oBareWords=self.bareWords
        self.bareWords=False
        self.__stringEncode("id#"+str(id(obj)))
        self.bareWords=oBareWords
      else:
        self.seen[id(obj)]=obj
        fl=flavour(obj)
        if fl is T_SEQ:
          self.__listEncode(obj)
        elif fl is T_MAP:
          self.__dictEncode(obj)
        else:
          self.__h2f(`obj`)

  def __stringEncode(self,s):
    """ Transcribe a string to the File fp in Hier format.
    """
    if self.bareWords and safeStringRe.match(s):
      self.fp.write(s)
    else:
      self.fp.write(self.quoteChar)
      while s:
        m=safeChunkRe.search(s)
        if not m: break

        start=m.start()
        if start > 0:
          self.__unsafeSubstringEncode(s[:start])

        self.fp.write(m.group())
        s=s[m.end():]

      if s:
        self.__unsafeSubstringEncode(s)

      self.fp.write(self.quoteChar)

  def __unsafeSubstringEncode(s):
    """ Transcribe the characters of a string in escaped form.
        This is the inner portion of stringEncode() for unsafe strings.
    """
    for c in s:
      if c == self.quoteChar:                          enc='\\'+self.quoteChar
      elif c == '\t':			enc='\\t'
      elif c == '\n':			enc='\\n'
      elif c == '\r':			enc='\\r'
      elif c >= ' ' and c <= '~':		enc=c
      else:
        oc=ord(c)
        if oc <= 0xff:			enc="\\x%02x" % oc
        else:				enc="\\u%04x" % oc
      fp.write(enc)

  def __listEncode(self,listobj):
    """ Transcribe a List to the File fp in Hier format.
    """
    self.fp.write("[")

    if len(listobj) > 0:
      self.fp.adjindent(1)
      dofold = self.fp.getindent() is not None

      sep=""
      if dofold: nsep=",\n"
      else:      nsep=", "

      for obj in listobj:
        self.fp.write(sep)
        sep=nsep
        self.__h2f(obj)

      self.fp.popindent()

    self.fp.write("]")

  def __dictEncode(self,dictobj):
    """ Transcribe a Dictionary to the File fp in Hier format.
    """
    if dictSep is None: dictSep=' =>'

    fp.write("{")
    keys=dictobj.keys()

    if len(keys) > 0:
      fp.adjindent(1)
      dofold = fp.getindent() is not None

      sep=""
      if dofold: nsep=",\n"
      else:      nsep=", "

      if type(keys) is TupleType: keys=all(keys)
      keys.sort()

      for k in keys:
        fp.write(sep)
        sep=nsep
        keytxt=h2a(k,0,seen={},dictSep=dictSep,bareWords=bareWords)
        fp.write(keytxt)
        fp.write(dictSep)
        fp.write(" ")

        fp.adjindent(lastlinelen(keytxt)+len(dictSep)+1)
        h2f(fp,dictobj[k],seen=seen,dictSep=dictSep,bareWords=bareWords)
        fp.popindent()

      fp.popindent()

    fp.write("}")

class HierInput(_Hier):
  def __init__(self,opts=None):
    _Hier.__init__(self,opts=opts)

  def load(self,path):
    """ Read an object structure from the named file or directory.
    """
    if os.path.isdir(path):
      val=self.loaddir(path)
    else:
      val=self.loadfile(path)
    return val

  def loaddir(self,dirname):
    """ Read Hier data from the named directory.
    """
    progress("loaddir", dirname)
    dict={}

    dents=[ dirent for dirent in os.listdir(dirname) if dirent[0] != '.']
    for dent in dents:
      dict[dent]=self.load(os.path.join(dirname,dent))

    return dict

  def loadfile(self,filename,charset=None):
    """ Read Hier data from the named file.
    """
    fp=cs.io.ContLineFile(filename)
    dict={}

    for line in fp:
      kv=self.kvline(line,charset=charset)
      dict[kv[0]]=kv[1]
    fp.close()

    return dict

  def savefile(self,dict,filename):
    """ Write a Dictionary to the named file in Hier format.
    """
    ofp=file(filename,"w")
    ifp=cs.io.IndentedFile(ofp,0)

    keys=dict.keys()
    keys.sort()
    for key in keys:
      keytxt=h2a(key)
      ifp.write(keytxt)
      if len(keytxt) < 16:
        ifp.write(' '*(16-len(keytxt)))
        ifp.pushindent(16)
      else:
        ifp.write(' ')
        ifp.pushindent(len(keytxt)+1)

      oldfp=self.fp
      self.fp=ifp
      self.__h2f(dict[key])
      self.fp=oldfp

      ifp.popindent()
      ifp.write('\n')

    ofp.close()

  def kvline(self,line,charset=None):
    """ Parse a (key,value) pair from a line of text, return (key,value) tuple or None.
        This is the inner operation of loadfile().
    """
    oline=line
    line=line.lstrip()
    if ifdebug: debug("kvline: key tok at:", line)
    (key,line)=self.tok(line)
    line=line.lstrip()
    if ifdebug: debug("kvline: value tok at:", line)
    (value,line)=self.tok(line)
    line=line.lstrip()
    if len(line):
      raise ValueError, "unparsed data on line: \""+line+"\", from original line: \""+oline+"\""

    return (key,value)

  def tok(self,s):
    """ Fetch a token from the string s.
        Return the tuple (value, s) with s just past the text of the token.
    """
    s=s.lstrip()
    if s[0] == '"' or s[0] == "'":
      return self.__a2str(s)
    if s[0] == '{':
      return self.__a2dict(s)
    if s[0] == '[':
      return self.__a2list(s)

    m=safePrefixRe.match(s)
    if not m:
      raise ValueError, "syntax error at: \""+s+"\""

    return (m.group(),s[m.end():])

  def __a2str(self,s):
    """ Read a quoted string from the opening quote, assemble into string.
        Return (string, s) with s just past the closing quote.
    """
    buf=StringIO()
    assert s[0] == '"' or s[0] == "'", "expected '\"' or \"'\", found '"+s[0]+"'"
    q=s[0]
    s=s[1:]
    while s[0] != q:
      if s[0] == '\\':
        sloshc=s[1]
        s=s[2:]
        if sloshc == 't':
          buf.write('\t')
        elif sloshc == 'n':
          buf.write('\n')
        elif sloshc == 'x':
          buf.write(chr(eval("0x"+s[:2])))
          s=s[2:]
        else:
          buf.write(sloshc)
      else:
        buf.write(s[0])
        s=s[1:]

    s=s[1:]
    str=buf.getvalue()
    buf.close();

    return (str.decode(self.inputEncoding),s)

  def __a2list(self,s):
    """ Read text from opening left square bracket, assemble into list.
        Return (list,s) with s just after closing ']'.
    """
    ary=[]
    assert s[0] == '[', "expected '[', found '"+s[0]+"'"
    s=s[1:].lstrip()
    while s[0] != ']':
      if s[0] == ',':
        # commas are optional
        s=s[1:].lstrip()
        continue

      (val,s)=self.tok(s)
      ary.append(val)
      s=s.lstrip()

    return (ary,s[1:])

  def __a2dict(self,s):
    """ Read text from opening left curly bracket, assemble into dict.
        Return (dict,s) with s just after closing '}'.
    """
    dict={}
    assert s[0] == '{', "expected '{', found '"+s[0]+"'"
    s=s[1:].lstrip()
    while s[0] != '}':
      if s[0] == ',':
        # commas are optional
        s=s[1:].lstrip()
        continue

      (key,s)=self.tok(s)
      s=s.lstrip()
      if s[0] == ':':
        s=s[1:]
      elif s[:2] == '=>':
        s=s[2:]
      else:
        raise ValueError, "expected \":\" or \"=>\", found: \""+s+"\""

      s=s.lstrip()
      (val,s)=self.tok(s)
      dict[key]=val
      s=s.lstrip()

    return (dict,s[1:])
