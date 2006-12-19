#!/usr/bin/python

import re
import os
import os.path
from types import *
from cStringIO import StringIO
from cs.lex import skipwhite, lastlinelen
import cs.io
from cs.misc import progress, cmderr, all

T_SEQ='ARRAY'
T_MAP='HASH'
T_SCALAR='SCALAR'

safeChunkPtn = r'[\-+_a-zA-Z0-9./@]+'
safeChunkRe  = re.compile(safeChunkPtn)
safePrefixRe = re.compile('^'+safeChunkPtn)
safeStringRe = re.compile('^'+safeChunkPtn+'$')
integerRe    = re.compile('^-?[0-9]+$')

def flavour(o):
  """ Return the ``flavour'' of an object:
      T_MAP: DictType, DictionaryType, objects with an __keys__ or keys attribute.
      T_SEQ: TupleType, ListType, objects with an __iter__ attribute.
      T_SCALAR: Anything else.
  """
  t=type(o)
  if t in (TupleType, ListType): return T_SEQ
  if t in (DictType, DictionaryType): return T_MAP
  if hasattr(o,'__keys__') or hasattr(o,'keys'): return T_MAP
  if hasattr(o,'__iter__'): return T_SEQ
  return T_SCALAR

def h2a(o,i=None,seen=None,dictSep=None):
  """ ``Hier'' to ``ASCII''- convert an object to text.
      Return a textual representation of an object in Hier format.
      i is the indent mode, default None.
  """
  if seen is None: seen={}
  buf=StringIO()
  io=cs.io.IndentedFile(buf,i)
  h2f(io,o,seen=seen,dictSep=dictSep)
  e=buf.getvalue()
  buf.close()
  return e

def h2f(fp,o,seen,dictSep):
  """ ``Hier'' to ``ASCII''- convert an object to text.
      Transcribe a textual representation of an object in Hier format to the File fp.
      NB: fp must be a cs.io.IndentedFile
  """
  t=type(o)
  if isinstance(o,int):
    fp.write(str(o))
  elif t is FloatType:
    stringEncode(fp,str(o))
  elif t is BooleanType:
    fp.write(int(o))
  elif isinstance(o, StringTypes):
    stringEncode(fp,o)
  else:
    if id(o) in seen:
      stringEncode(fp,"id#"+str(id(o)))
    else:
      seen[id(o)]=o
      fl=flavour(o)
      if fl is T_SEQ:
        listEncode(fp,o,seen=seen)
      elif fl is T_MAP:
        dictEncode(fp,o,seen=seen)
      else:
        h2f(fp,`o`,seen=seen)

def stringEncode(fp,s):
  """ Transcribe a string to the File fp in Hier format.
  """
  if safeStringRe.match(s):
    fp.write(s)
  else:
    fp.write('"')
    start=0
    while start < len(s):
      m=safeChunkRe.search(s,start)
      if not m: break

      nstart=m.start()
      if (nstart > start):
	unsafeSubstringEncode(fp,s[start:nstart])

      end=m.end()
      fp.write(s[nstart:end])
      start=end

    unsafeSubstringEncode(fp,s[start:])
    fp.write('"')

def unsafeSubstringEncode(fp,s):
  """ Transcribe the characters of a string in escaped form.
      This is the inner portion of stringEncode() for unsafe strings.
  """
  for c in s:
    if c == '\t':			enc='\\t'
    elif c == '\n':			enc='\\n'
    elif c == '\r':			enc='\\r'
    elif c >= ' ' and c <= '~':		enc=c
    else:
      oc=ord(c)
      if oc <= 0xff:			enc="\\x%02x" % oc
      else:				enc="\\u%04x" % oc
    fp.write(enc)

def listEncode(fp,l,seen):
  """ Transcribe a List to the File fp in Hier format.
  """
  fp.write("[")

  if len(l) > 0:
    fp.adjindent(1)
    dofold = fp.getindent() is not None

    sep=""
    if dofold: nsep=",\n"
    else:      nsep=", "

    for o in l:
      fp.write(sep)
      sep=nsep
      h2f(fp,o,seen=seen)

    fp.popindent()

  fp.write("]")

def dictEncode(fp,d,seen,i=None):
  """ Transcribe a Dictionary to the File fp in Hier format.
  """
  fp.write("{")
  keys=d.keys()

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
      keytxt=h2a(k,0,seen={})
      fp.write(keytxt)
      fp.write(" => ")

      fp.adjindent(lastlinelen(keytxt)+4)	# key width + " => "
      h2f(fp,d[k],seen=seen)
      fp.popindent()

    fp.popindent()

  fp.write("}")

def load(path):
  """ Read an object structure from the named file or directory.
  """
  if os.path.isdir(path):
    val=loaddir(path)
  else:
    val=loadfile(path)
  return val

def loaddir(dirname):
  """ Read Hier data from the named directory.
  """
  progress("loaddir", dirname)
  dict={}
  dents=[ dirent for dirent in os.listdir(dirname) if dirent[0] != '.']
  for dent in dents:
    dict[dent]=load(os.path.join(dirname,dent))
  return dict

def loadfile(filename):
  """ Read Hier data from the named file.
  """
  fp=cs.io.ContLineFile(filename)
  dict={}
  for line in fp:
    kv=kvline(line)
    dict[kv[0]]=kv[1]
  fp.close()
  return dict

def savefile(dict,filename):
  """ Write a Dictionary to the named file in Hier format.
  """
  fp=file(filename,"w")
  ifp=cs.io.IndentedFile(fp,0)
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
    h2f(ifp,dict[key])
    ifp.popindent()
    ifp.write('\n')
  fp.close()

def kvline(line):
  """ Parse a (key,value) pair from a line of text, return (key,value) tuple or None.
      This is the inner operation of loadfile().
  """
  oline=line
  line=line.lstrip()
  (key,line)=tok(line)
  line=line.lstrip()
  (value,line)=tok(line)
  line=line.lstrip()
  if len(line):
    raise ValueError, "unparsed data on line: \""+line+"\", from original line: \""+oline+"\""

  return (key,value)

def tok(s):
  """ Fetch a token from the string s.
      Return the tuple (value, s) with s just past the text of the token.
  """
  s=s.lstrip()
  if s[0] == '"' or s[0] == "'":
    return a2str(s)
  if s[0] == '{':
    return a2dict(s)
  if s[0] == '[':
    return a2list(s)

  m=safePrefixRe.match(s)
  if not m:
    raise ValueError, "syntax error at: \""+s+"\""

  return (m.group(),s[m.end():])

def a2str(s):
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

  return (str,s)

def a2list(s):
  """ Read text from opening left square bracket, assemble into list.
      Return (list,s) with s just after closing ']'.
  """
  ary=[]
  assert s[0] == '[', "expected '[', found '"+s[0]+"'"
  s=s.lstrip()
  while s[0] != ']':
    if s[0] == ',':
      # commas are optional
      s=s[1:].lstrip()
      continue

    (val,s)=tok(s)
    ary.append(val)
    s=s.lstrip()

  return (ary,s[1:])

def a2dict(s):
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

    (key,s)=tok(s)
    s=s.lstrip()
    if s[0] == ':':
      s=s[1:]
    elif s[0:2] == '=>':
      s=s[2:]
    else:
      raise ValueError, "expected \":\" or \"=>\", found: \""+s+"\""

    s=s.lstrip()
    (val,s)=tok(s)
    dict[key]=val
    s=s.lstrip()

  return (dict,s[1:])
