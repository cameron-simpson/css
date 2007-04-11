import string
from StringIO import StringIO
import re

int_re=re.compile(r'\d+')
float_re=re.compile(r'[\-+]?\d+(\.\d+)(e[\-+]\d+)',re.I)
id_re =re.compile(r'[a-z_]\w*', re.I)
ord_space=ord(' ')

def isint(s):
  m=int_re.match(s)
  return m is not None and m.group() == s

def unctrl(s,tabsize=8):
  s2=''
  sofar=0
  for i in range(len(s)):
    ch=s[i]
    ch2=None
    if ch == '\t':
      pass
    elif ch == '\f':
      ch2='\\f'
    elif ch == '\n':
      ch2='\\n'
    elif ch == '\r':
      ch2='\\r'
    elif ch == '\v':
      ch2='\\v'
    else:
      o=ord(ch)
      if o < ord_space or string.printable.find(ch) == -1:
        if o >= 256:
          ch2="\\u%04x"%o
        else:
          ch2="\\%03o"%o

    if ch2 is not None:
      import cs.misc
      cs.misc.progress("ch2=["+ch2+"]")
      if sofar < i:
        s2+=s[sofar:i]
      s2+=ch2
      sofar=i+1

  if sofar < len(s):
    s2+=s[sofar:]

  return s2.expandtabs(tabsize)

def tabpadding(padlen,tabsize=8,offset=0):
  pad=''
  nexttab=tabsize-offset%tabsize
  while nexttab <= padlen:
    pad+='\t'
    padlen-=nexttab
    nexttab=tabsize

  if padlen > 0:
    pad+="%*s"%(padlen,' ')

  return pad

def skipwhite(s,start=0):
  ''' Returns the location of next nonwhite in string.
  '''
  while start < len(s) and s[start] in string.whitespace:
    start+=1
  return start

def strlist(ary,sep=", "):
  return sep.join([str(a) for a in ary])

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
  import cs.json
  return cs.json.json(d)
