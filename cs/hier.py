#!/usr/bin/python

import re
import os
import os.path
from types import *
from cStringIO import StringIO
from cs.lex import skipwhite, lastlinelen
import cs.io

safeChunkPtn = r'[\-+_a-zA-Z0-9./@]+'
safeChunkRe  = re.compile(safeChunkPtn)
safePrefixRe = re.compile('^'+safeChunkPtn)
safeStringRe = re.compile('^'+safeChunkPtn+'$')
integerRe    = re.compile('^-?[0-9]+$')

def flavour(o):
  t=type(o)
  if t in (TupleType, ListType): return 'ARRAY'
  if t in (DictType, DictionaryType): return 'HASH'
  if hasattr(o,'__keys__') or hasattr(o,'keys'): return 'HASH'
  if hasattr(o,'__iter__'): return 'ARRAY'
  return 'SCALAR'

def h2a(o,i=None):
  buf=StringIO()
  io=cs.io.IndentedFile(buf,i)
  h2f(io,o)
  e=buf.getvalue()
  buf.close()
  return e

# NB: fp must be a cs.io.IndentedFile
def h2f(fp,o):
  t=type(o)
  if t in [IntType, LongType]:
    fp.write(str(o))
  elif t is FloatType:
    stringEncode(fp,str(o))
  elif t is BooleanType:
    fp.write(int(o))
  elif t in (StringType, UnicodeType):
    stringEncode(fp,o)
  else:
    fl=flavour(o)
    if fl is 'ARRAY':
      listEncode(fp,o)
    elif fl is 'HASH':
      dictEncode(fp,o)
    else:
      h2f(fp,`o`)

def stringEncode(fp,s):
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

def listEncode(fp,l):
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
      h2f(fp,o)

    fp.popindent()

  fp.write("]")

def dictEncode(fp,d,i=None):
  fp.write("{")
  keys=d.keys()

  if len(keys) > 0:
    fp.adjindent(1)
    dofold = fp.getindent() is not None

    sep=""
    if dofold: nsep=",\n"
    else:      nsep=", "

    keys.sort()
    for k in keys:
      fp.write(sep)
      sep=nsep
      keytxt=h2a(k,0)
      fp.write(keytxt)
      fp.write(" => ")

      fp.adjindent(lastlinelen(keytxt)+4)	# key width + " => "
      h2f(fp,d[k])
      fp.popindent()

    fp.popindent()

  fp.write("}")

def load(path):
  if os.path.isdir(path):
    val=loaddir(path)
  else:
    val=loadfile(path)
  return val

def loaddir(dirname):
  "Read hier data from a directory."
  dict={}
  dents=[ dirent for dirent in os.listdir(dirname) if dirent[0] != '.']
  for dent in dents:
    dict[dent]=load(os.path.join(dirname,dent))
  return dict

def loadfile(filename):
  "Read hier data from a file"
  fp=cs.io.ContLineFile(filename)
  dict={}
  for line in fp:
    kv=kvline(line)
    dict[kv[0]]=kv[1]
  fp.close()
  return dict

def savefile(dict,filename):
  "Write dictionary to a file."
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
  "Parse (key,value) from file, return tuple or None"
  (key,pos)=tok(line)
  (value,pos)=tok(line,pos)
  pos=skipwhite(line,pos)
  if pos < len(line):
    print "unparsed data on line: \""+line[pos:]+"\""
  return (key,value)

def tok(s,pos=0):
  "Fetch token from string, return (value,end)"
  pos=skipwhite(s,pos)
  if pos == len(s): return (None,pos)

  ch1=s[pos]
  if ch1 == '"': return a2str(s,pos)
  if ch1 == '{': return a2dict(s,pos)
  if ch1 == '[': return a2list(s,pos)

  match=safePrefixRe.match(s[pos:])
  if not match: return (None,pos)
  end=match.end()
  str=s[pos:pos+end]
  pos+=end

  return (str,pos)

def a2str(s,pos):
  "Read text from opening quote, assemble into string."
  assert s[pos] == '"', "expected '\"', found '"+s[pos]+"'"
  buf=StringIO()
  pos+=1
  while pos < len(s):
    ch1=s[pos]
    pos+=1
    if ch1 == '"': break
    if ch1 == '\\':
      ch2=s[pos]
      pos+=1
      if ch2 == 't':
	buf.write('\t')
      elif ch2 == 'n':
	buf.write('\n')
      elif ch2 == 'x':
	buf.write(chr(eval("0x"+s[pos:pos+2])))
	pos+=2
      else:
	buf.write(ch2)
    else:
      buf.write(ch1)

  str=buf.getvalue()
  buf.close();

  return (str,pos)

def a2list(s,pos=0):
  "Read text from opening left square bracket, assemble into list."
  assert s[pos] == '[', "expected '[', found '"+s[pos]+"'"
  ary=[]
  pos=skipwhite(s,pos+1)
  while pos < len(s):
    ch1=s[pos]
    if ch1 == ']':
      pos+=1
      break
    if ch1 == ',':
      pos=skipwhite(s,pos+1)
    else:
      (val,pos)=tok(s,pos)
      ary.append(val)

  return (ary,pos)

def a2dict(s,pos=0):
  "Read text from opening left curly bracket, assemble into dict."
  assert s[pos] == '{', "expected '{', found '"+s[pos]+"'"
  dict={}
  pos=skipwhite(s,pos+1)
  while pos < len(s):
    ch1=s[pos]
    if ch1 == '}':
      pos+=1
      break
    if ch1 == ',':
      pos=skipwhite(s,pos+1)
    else:
      (key,pos)=tok(s,pos)
      pos=skipwhite(s,pos)
      if  pos+2 < len(s) \
      and s[pos] == '=' \
      and s[pos+1] == '>':
	pos=skipwhite(s,pos+2)
	(val,pos)=tok(s,pos)
	dict[key]=val
      else:
	# XXX - syntax exception or something?
	break

  return (dict,pos)
