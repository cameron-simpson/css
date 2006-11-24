import string
from StringIO import StringIO
import re

int_re=re.compile(r'^\d+$')

def isint(s):
  m=int_re.match(s)
  return m is not None and m.group() == s

def skipwhite(s,start=0):
  ''' Returns the location of next nonwhite in string.
  '''
  while start < len(s) and s[start] in string.whitespace:
    start+=1
  return start

def strlist(ary,sep=", "):
  return string.join([str(a) for a in ary],sep)

def lastlinelen(s):
  """ length of text after last newline in string
      initially used by cs.hier to compute effective text width
  """
  return len(s)-string.rfind(s,'\n')-1

dq_re=re.compile(r'"(([^\\"]|\\[\\"])*)"')
nq_re=re.compile(r'\S+')
 
# parse a line consisting of words or "quoted strings"
def parseline(line):
  words=[]
  line=string.lstrip(line)
  while len(line) > 0:
    m=dq_re.match(line)
    if m is not None:
      words.append(undq(m.group(1)))
      line=line[m.end():]
    else:
      m=nq_re.match(line)
      if m is not None:
        words.append(m.group(0))
        line=line[m.end():]
      else:
        cmderr("aborting parseline at:",line)
        return None
  
    line=string.lstrip(line)
 
  return words
 
# strip quotes from a "quoted string"
dqch_re=re.compile(r'([^\\]|\\.)')
def undq(s):
  result=''
  bs=s.find('\\')
  while bs >= 0:
    if bs > 0: result+=s[:bs]
    result.append(s[bs+1])
    s=s[bs+2:]
      
  result+=s
      
  return result

def htmlify(s,nbsp=False):
  s=s.replace("&","&amp;")
  s=s.replace("<","&lt;")
  s=s.replace(">","&gt;")
  if nbsp:
    s=s.replace(" ","&nbsp;")
  return s

def htmlquote(s):
  s=htmlify(s)
  s=s.replace("\"","&dquot;")
  return "\""+s+"\""

def jsquote(s):
  s=s.replace("\"","&dquot;")
  return "\""+s+"\""

def dict2js(d):
  fp=StringIO()
  fp.write("{");
  first=True
  for k in d.keys():
    if first:
      first=False
    else:
      fp.write(",")

    fp.write(k)
    fp.write(":")

    v=d[k]
    if v is None:
      fp.write('null')
    else:
      t=type(v)
      if t is str:
        fp.write(jsquote(v))
      elif t in (int, float):
        fp.write(str(v))
      else:
        fp.write(jsquote(str(v)))
  fp.write("}")
  return fp.getvalue()
