import base64
import binascii
import quopri
from string import printable, whitespace, ascii_letters, ascii_uppercase, digits
import re
import sys
import os
from cs.py3 import unicode, ustr
##from cs.logutils import X

unhexify = binascii.unhexlify
if sys.hexversion >= 0x030000:
  def hexify(bs):
    return binascii.hexlify(bs).decode()
else:
  hexify = binascii.hexlify

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
      if o < ord_space or printable.find(ch) == -1:
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
  while start < len(s) and s[start] in whitespace:
    start+=1
  return start

def strlist(ary,sep=", "):
  return sep.join([str(a) for a in ary])

def lastlinelen(s):
  ''' The length of text after the last newline in a string.
      Initially used by cs.hier to compute effective text width.
  '''
  return len(s) - s.rfind('\n') - 1

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
  line=line.lstrip()
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

    line = line.lstrip()

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

# characters that may appear in text sections of a texthexify result
# Notable exclusions:
#  \ - to avoid double in slosh escaped presentation
#  % - likewise, for percent escaped presentation
#  [ ] - the delimiters of course
#  / - path separator
#
_texthexify_white_chars = ascii_letters + digits + '_-+.,'

def texthexify(bs, shiftin='[', shiftout=']', whitelist=None):
  ''' Transcribe the bytes `bs` to text.
      hexify() and texthexify() output strings may be freely
      concatenated and decoded with untexthexify().
  '''
  if sys.hexversion < 0x03000000:
    bschr = lambda bs, ndx: bs[ndx]
  else:
    bschr = lambda bs, ndx: chr(bs[ndx])
  if whitelist is None:
    whitelist = _texthexify_white_chars
  inout_len = len(shiftin) + len(shiftout)
  chunks = []
  offset = 0
  offset0 = offset
  inwhite = False
  while offset < len(bs):
    c = bschr(bs, offset)
    if inwhite:
      if c not in whitelist:
        inwhite = False
        if offset - offset0 > inout_len:
          chunk = ( shiftin
                  + ''.join( bschr(bs, o) for o in range(offset0, offset) )
                  + shiftout
                  )
        else:
          chunk = hexify(bs[offset0:offset])
        chunks.append(chunk)
        offset0 = offset
    else:
      if c in whitelist:
        inwhite = True
        chunk = hexify(bs[offset0:offset])
        chunks.append(chunk)
        offset0 = offset
    offset += 1
  if offset > offset0:
    if inwhite and offset - offset0 > inout_len:
      chunk = ( shiftin
              + ''.join( bschr(bs, o) for o in range(offset0, offset) )
              + shiftout
              )
    else:
      chunk = hexify(bs[offset0:offset])
    chunks.append(chunk)
  return ''.join(chunks)

def untexthexify(s, shiftin='[', shiftout=']'):
  chunks = []
  while len(s) > 0:
    hexlen = s.find(shiftin)
    if hexlen < 0:
      break
    if hexlen > 0:
      hextext = s[:hexlen]
      if hexlen % 2 != 0:
        raise TypeError("uneven hex sequence \"%s\"" % (hextext,))
      chunks.append(unhexify(s[:hexlen]))
    s = s[hexlen+len(shiftin):]
    textlen = s.find(shiftout)
    if textlen < 0:
      raise TypeError("missing shift out marker \"%s\"" % (shiftout,))
    if sys.hexversion < 0x03000000:
      chunks.append(s[:textlen])
    else:
      chunks.append(bytes( ord(c) for c in s[:textlen] ))
    s = s[textlen+len(shiftout):]
  if len(s) > 0:
    if len(s) % 2 != 0:
      raise TypeError("uneven hex sequence \"%s\"" % (s,))
    chunks.append(unhexify(s))
  return b''.join(chunks)

# regexp to match RFC2047 text chunks
re_RFC2047 = re.compile(r'=\?([^?]+)\?([QB])\?([^?]*)\?=', re.I)

def unrfc2047(s):
  ''' Accept a string containing RFC2047 text encodings (or the whitespace
      littered varieties that come from some low quality mail clients) and
      decode them into flat Unicode.
  '''
  from cs.logutils import warning
  if not isinstance(s, unicode):
    s = unicode(s, 'iso8859-1')
  chunks = []
  sofar = 0
  for m in re_RFC2047.finditer(s):
    start = m.start()
    end = m.end()
    if start > sofar:
      chunks.append(s[sofar:start])
    enccset = m.group(1)
    enctype = m.group(2).upper()
    enctext = m.group(3)
    if enctype == 'B':
      try:
        enctext = base64.b64decode(enctext)
      except TypeError as e:
        warning("%r: %e", enctext, e)
        enctext = m.group()
    elif enctype == 'Q':
      try:
        enctext = quopri.decodestring(enctext)
      except UnicodeEncodeError as e:
        warning("%r: %e", enctext, e)
        ##enctext = enctext.decode('iso8859-1')
    else:
      raise RuntimeError("unhandled RFC2047 string: %r" % (m.group(),))
    try:
      enctext = enctext.decode(enccset)
    except LookupError as e:
      warning("%r: %e", enctext, e)
      enctext = enctext.decode('iso8859-1')
    except UnicodeDecodeError as e:
      warning("%r: %e", enctext, e)
      enctext = enctext.decode(enccset, 'replace')
    except UnicodeEncodeError as e:
      warning("%r: %e", enctext, e)
      ##enctext = enctext.decode(enccset, 'replace')
    chunks.append(enctext)
    sofar = end
  if sofar < len(s):
    chunks.append(s[sofar:])
  return unicode('').join(chunks)

def get_chars(s, gochars, offset=0):
  ''' Scan the string `s` for characters in `gochars` starting at `offset`
      (default 0).
      Return (match, new_offset).
  '''
  ooffset = offset
  while offset < len(s) and s[offset] in gochars:
    offset += 1
  return s[ooffset:offset], offset

def get_white(s, offset=0):
  ''' Scan the string `s` for characters in string.whitespace starting at
      `offset` (default 0).
      Return (match, new_offset).
  '''
  return get_chars(s, whitespace, offset=offset)

def get_nonwhite(s, offset=0):
  ''' Scan the string `s` for characters not in string.whitespace starting at
      `offset` (default 0).
      Return (match, new_offset).
  '''
  return get_other_chars(s, whitespace, offset=offset)

def get_identifier(s, offset=0, alpha=ascii_letters, number=digits, extras='_'):
  ''' Scan the string `s` for an identifier (by default an ASCII
      letter or underscore followed by letters, digits or underscores)
      starting at `offset` (default 0).
      Return (match, new_offset).
      The empty string and an unchanged offset will be returned if
      there is no leading letter/underscore.
  '''
  ch = s[offset]
  if ch not in alpha and ch not in extras:
    return '', offset
  idtail, offset = get_chars(s, alpha + number + extras, offset+1)
  return ch + idtail, offset

def get_uc_identifier(s, offset=0, number=digits, extras='_'):
  ''' Scan the string `s` for an identifier as for get_identifier(),
      but require the letters to be uppercase.
  '''
  return get_identifier(s, offset=offset, alpha=ascii_uppercase, number=number, extras=extras)

def get_other_chars(s, stopchars, offset=0):
  ''' Scan the string `s` for characters not in `stopchars` starting
      at `offset` (default 0).
      Return (match, new_offset).
  '''
  ooffset = offset
  while offset < len(s) and s[offset] not in stopchars:
    offset += 1
  return s[ooffset:offset], offset

# default character map for \c notation
SLOSH_CHARMAP = {
    'a':    '\a',
    'b':    '\b',
    'f':    '\f',
    'n':    '\n',
    'r':    '\r',
    't':    '\t',
    'v':    '\v',
  }

def slosh_mapper(c, charmap=SLOSH_CHARMAP):
  ''' Return a string to replace \`c`, or None.
  '''
  return charmap.get(c)

def get_sloshed_text(s, delim, offset=0, slosh='\\', mapper=slosh_mapper):
  ''' Collect slosh escaped text from the string `s` from position `offset` (default 0) and return the decoded unicode string and the offset of the completed parse.
      `delim`: end of string delimiter, such as a single or double quote.
      `offset`: starting offset within `s`, default 0.
      `slosh`: escape character, default a slosh ('\\').
      `mapper`: a mapping function which accepts a single character
        and returns a replacement string or None; this is used the
        replace things such as '\\t' or '\\n'. The default is the
        slosh_mapper function, whose default mapping is SLOSH_CHARMAP.
      The escape character `slosh` introduces an encoding of some
      replacement text whose value depends on the following character.
      If the following character is:
        - the escape character `slosh`, insert the escape character
        - the string delimiter `delim`, insert the delimiter
        - the character 'x', insert the character with code from
          the following 2 hexadecimal digits
        - the character 'u', insert the character with code from
          the following 4 hexadecimal digits
        - the character 'U', insert the character with code from
          the following 8 hexadecimal digits
  '''
  chunks = []
  slen = len(s)
  while True:
    if offset >= slen:
      if delim is not None:
        raise ValueError("missing delimiter %r at offset %d" % (delim, offset))
      break
    offset0 = offset
    c = s[offset]
    offset += 1
    if delim is not None and c == delim:
      # delimiter; end text
      break
    elif c != slosh:
      # plain text
      while offset < slen:
        if s[offset] == slosh or (delim is not None and s[offset] == delim):
          break
        offset += 1
      chunks.append(s[offset0:offset])
    else:
      # \something
      if offset >= slen:
        raise ValueError('incomplete slosh escape at offset %d' % (offset0,))
      c = s[offset]
      offset += 1
      if c == slosh or (delim is not None and c == delim):
        chunks.append(c)
      elif c == 'x':
        # \xhh
        if slen - offset < 2:
          raise ValueError('short hexcode for %sxhh at offset %d' % (slosh, offset0))
        hh = s[offset:offset+2]
        offset += 2
        chunks.append(chr(int(hh, 16)))
      elif c == 'u':
        # \uhhhh
        if slen - offset < 4:
          raise ValueError('short hexcode for %suhhhh at offset %d' % (slosh, offset0))
        hh = s[offset:offset+4]
        offset += 4
        chunks.append(chr(int(hh, 16)))
      elif c == 'U':
        # \Uhhhhhhhh
        if slen - offset < 8:
          raise ValueError('short hexcode for %sUhhhhhhhh at offset %d' % (slosh, offset0))
        hh = s[offset:offset+8]
        offset += 8
        chunks.append(chr(int(hh, 16)))
      else:
        chunk = mapper(c)
        if chunk is None:
          raise ValueError('unrecognised %s%s escape at offset %d' % (slosh, c, offset0))
        chunks.append(chunk)
  return u''.join( ustr(chunk) for chunk in chunks ), offset

def get_envvar(s, offset=0, environ=None):
  ''' Parse a simple environment variable reference to $$ or $varname.
  '''
  if environ is None:
    environ = os.environ
  if not s.startswith('$'):
    raise ValueError("no leading '$' at offset %d" % (offset,))
  offset += 1
  if offset >= len(s):
    raise ValueError("short string, nothing after '$' at offset %d" % (offset,))
  identifier, offset = get_identifier(s, offset)
  if identifier:
    value = environ[identifier]
    return value, offset
  c = s[offset]
  offset += 1
  if c == '$':
    return c, offset
  raise ValueError("unsupported special variable $%s" % (c,))

def get_qstr(s, offset=0):
  if len(s) - offset < 1:
    raise ValueError("short string, no opening quote")
  delim = s[offset]
  if delim != '"':
    raise ValueError("expected opening double quote, found %r" % (delim,))
  return get_sloshed_text(s, delim, offset+1)

def isUC_(s):
  ''' Check that a string matches ^[A-Z][A-Z_0-9]*$.
  '''
  if s.isalpha() and s.isupper():
    return True
  if len(s) < 1:
    return False
  if not s[0].isupper():
    return False
  for c in s[1:]:
    if c != '_' and not c.isupper() and not c.isdigit():
      return False
  return True

def parseUC_sAttr(attr):
  ''' Take an attribute name and return (key, isplural).
      FOO returns (FOO, False).
      FOOs or FOOes returns (FOO, True).
      Otherwise return (None, False).
  '''
  if len(attr) > 1:
    if attr[-1] == 's':
      if attr[-2] == 'e':
        k=attr[:-2]
        if isUC_(k):
          return k, True
      else:
        k=attr[:-1]
        if isUC_(k):
          return k, True
  if isUC_(attr):
    return attr, False
  return None, False

def as_lines(chunks, partials=None):
  ''' Generator yielding complete lines from arbitrary pieces text from the iterable `chunks`.
      After completion, any remaining newline-free chunks remain
      in the partials list; this will be unavailable to the caller
      unless the list is presupplied.
  '''
  if partials is None:
    partials = []
  if any( [ '\n' in p for p in partials ] ):
    raise ValueError("newline in partials: %r", partials)
  for chunk in chunks:
    pos = 0
    nl_pos = chunk.find('\n', pos)
    while nl_pos >= pos:
      partials.append(chunk[pos:nl_pos+1])
      yield ''.join(partials)
      partials[:] = ()
      pos = nl_pos + 1
      nl_pos = chunk.find('\n', pos)
    if pos < len(chunk):
      partials.append(chunk[pos:])

if __name__ == '__main__':
  import cs.lex_tests
  cs.lex_tests.selftest(sys.argv)
