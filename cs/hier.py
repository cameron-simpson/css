#!/usr/bin/python

import re
from types import *
from cStringIO import StringIO
from cs.lex import skipwhite
import cs.io

safeChunkPtn = r'[\-+_a-zA-Z0-9./@]+'
safeChunkRe  = re.compile(safeChunkPtn)
safePrefixRe = re.compile('^'+safeChunkPtn)
safeStringRe = re.compile('^'+safeChunkPtn+'$')
integerRe    = re.compile('^-?[0-9]+$')

def h2a(o):
  io=StringIO()
  h2f(io,o)
  e=io.getvalue()
  io.close()
  return e

def h2f(fp,o):
  t=type(o)
  if t in [IntType, LongType]:
    fp.write(str(o))
  if t is FloatType:
    stringEncode(fp,str(o))
  elif t is BooleanType:
    fp.write(int(o))
  elif t in [StringType, UnicodeType]:
    stringEncode(fp,o)
  elif t in [TupleType, ListType]:
    listEncode(fp,o)
  elif t in [DictType, DictionaryType]:
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
    elif c >= ' ' and ord(c) < 0x7f:	enc=c
    else:				enc="\\x%02x" % ord(c)
    fp.write(enc)

def listEncode(fp,l):
  fp.write("[")
  if not(i is None): i+=1

  sep=""
  for o in l:
    fp.write(sep)
    h2f(fp,o,i)
    if i is None: sep=", "
    else:         sep=",\n"+' '*i

  if not(i is None):
    i-=1
    fp.write("\n")
    fp.write(' '*i)
  fp.write("]")
  fp.write("\n")
  fp.write(' '*i)

def dictEncode(fp,d,i=None):
  fp.write("{")
  if not(i is None): i+=1

  sep=""
  keys=d.keys()
  keys.sort()
  for k in keys:
    fp.write(sep)
    h2f(fp,k)
    fp.write(" => ")
    h2f(fp,d[k])
    sep=", "
  fp.write(" }")

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
  keys=dict.keys()
  keys.sort()
  for key in keys:
    print >>fp, "%-15s " % h2a(key), h2a(dict[key])
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
