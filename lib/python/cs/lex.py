import string
from StringIO import StringIO
import re
from binascii import hexlify as hexify, unhexlify as unhexify

ord_space=ord(' ')

__strs={}
def str1(s):
  ''' A persistent cache for heavily used strings.
  '''
  global __strs
  if s in __strs:
    return __strs[s]
  __strs[s]=s
  return s

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

DQ_RE=re.compile(r'"(([^\\"]|\\[\\"])*)"')
nq_re=re.compile(r'\S+')
 
def get_dqstring(s):
  ''' Read a double quoted string from the start of `s`.
      Return the decoded string and the remainder of `s`.
      Returns None for the decoded string on no match.
  '''
  m = DQ_RE.match(s)
  if not m:
    return None, s
  return undq(m.group(1)), s[m.end():]

# parse a line consisting of words or "quoted strings"
def parseline(line):
  words=[]
  line=string.lstrip(line)
  while len(line) > 0:
    m=DQ_RE.match(line)
    if m is not None:
      words.append(undq(m.group(1)))
      line=line[m.end():]
    else:
      m=nq_re.match(line)
      if m is not None:
        words.append(m.group(0))
        line=line[m.end():]
      else:
        error("aborting parseline at: %s", line)
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

_texthexify_white_re = re.compile(r'[a-zA-Z0-9_\-+.,/]+')

def texthexify(s, shiftin='[', shiftout=']', whitelist_re=None):
  ''' Transcribe the byte string `s` to text.
      hexify() and texthexify() output strings may be freely
      concatenated and decoded with untexthexify().
  '''
  if whitelist_re is None:
    whitelist_re = _texthexify_white_re
  elif type(whitelist_re) is str:
    whitelist_re = re.compile(whitelist_re)
  inout_len = len(shiftin) + len(shiftout)
  chunks = []
  sofar = 0
  pos = 0
  while pos < len(s):
    m = whitelist_re.search(s, pos)
    if not m:
      break
    offset = m.start(0)
    text = m.group(0)
    if len(text) >= inout_len:
      if offset > pos:
        chunks.append(hexify(s[sofar:offset]))
      chunks.append(shiftin + text + shiftout)
      sofar = m.end(0)
    pos = m.end(0)

  if sofar < len(s):
    chunks.append(hexify(s[sofar:]))

  return ''.join(chunks)

def untexthexify(s, shiftin='[', shiftout=']'):
  chunks = []
  while len(s) > 0:
    hexlen = s.find(shiftin)
    if hexlen < 0:
      break
    if hexlen > 0:
      hextext = s[:hexlen]
      assert hexlen % 2 == 0, "uneven hex sequence \"%s\"" % (hextext,)
      chunks.append(unhexify(s[:hexlen]))
    s = s[hexlen+len(shiftin):]
    textlen = s.find(shiftout)
    assert textlen >= 0, "missing shift out marker \"%s\"" % (shiftout,)
    chunks.append(s[:textlen])
    s = s[textlen+len(shiftout):]
  if len(s) > 0:
    assert len(s) % 2 == 0, "uneven hex sequence \"%s\"" % (s,)
    chunks.append(unhexify(s))
  return ''.join(chunks)

def get_chars(s, gochars, offset=0):
  ''' Scan the string `s` for characters in `gochars` starting at `offset`
      (default 0).
      Return (match, new_offset).
  '''
  ooffset = offset
  while offset < len(s) and s[offset] in gochars:
    offset += 1
  return s[offset:offset], offset

def get_white(s, offset=0):
  ''' Scan the string `s` for characters in string.whitespace starting at
      `offset` (default 0).
      Return (match, new_offset).
  '''
  return get_chars(s, string.whitespace, offset=offset)

def get_identifier(s, offset=0):
  ''' Scan the string `s` for an identifier (letter or underscore followed by
      letters, digits or underscores) starting at `offset` (default 0).
      Return (match, new_offset).
      The empty string and an unchanged offset will be returned if
      there is no leading letter/underscore.
  '''
  ch = s[offset]
  if ch != '_' and not ch.isalpha():
    return '', offset
  # NB: compute letters+digits now in case locale gets changed at runtime
  idtail, offset = get_chars(s, string.letters + string.digits + '_', offset+1)
  return ch + idtail, offset

def get_other_chars(s, stopchars, offset=0):
  ''' Scan the string `s` for characters not in `stopchars` starting
      at `offset` (default 0).
      Return (match, new_offset).
  '''
  ooffset = offset
  while offset < len(s) and s[offset] not in stopchars:
    offset += 1
  return s[offset:offset], offset
