#!/usr/bin/python
#
# Useful HTML facilities.
#       - Cameron Simpson <cs@zip.com.au>
#

import re
import urllib
from types import StringTypes

# Characters safe to transcribe unescaped.
textSafeRe = re.compile(r'[^<>&]+')
# Characters safe to use inside "" in tag attribute values.
dqAttrValSafeRe = re.compile(r'[-=. \w:@/?~#+&]+')

def puttok(fp, tok):
  ''' Transcribe a token to HTML text.
      A token is either a string or a sequence.
      A string is safely transcribed as flat text.
      A sequence has 
  '''
  toktype = type(tok)
  if toktype in StringTypes:
    return puttext(fp, tok)

  elif f is T_SEQ:
    # token
    if hasattr(tok, 'tag'):
      # Tag class item
      tag=tok.tag
      attrs=tok.attrs
    else:
      # raw array [ tag[, attrs][, tokens...] ]
      tag=tok[0]; tok=tok[1:]
      if len(tok) > 0 and cs.hier.flavour(tok[0]) is T_MAP:
        attrs=tok[0]; tok=tok[1:]
      else:
        attrs={}

    isSCRIPT=(tag.upper() == 'SCRIPT')

    if isSCRIPT:
      if 'LANGUAGE' not in [a.upper() for a in attrs.keys()]:
        attrs['language']='JavaScript'

    fp.write('<')
    fp.write(tag)
    for k in attrs:
      fp.write(' ')
      fp.write(k)
      v=attrs[k]
      if v is not None:
        fp.write('="')
        fp.write(urllib.quote(str(v)))
        fp.write('"')
    fp.write('>')
    if isSCRIPT:
      fp.write("<!--\n")
    for t in tok:
      puttok(fp, t)
    if isSCRIPT:
      fp.write("\n-->")
    fp.write('</')
    fp.write(tag)
    fp.write('>')
  else:
    # unexpected
    raise TypeError

def puttext(fp, s, safeRe=None):
  ''' Transcribe plain text in HTML safe form.
  '''
  if safeRe is None: safeRe=textSafeRe
  while len(s):
    m=safeRe.match(s)
    if m:
      safetext=m.group(0)
      fp.write(safetext)
      s=s[len(safetext):]
    else:
      if s[0] == '<':
        fp.write('&lt;')
      elif s[0] == '>':
        fp.write('&gt;')
      elif s[0] == '&':
        fp.write('&amp;')
      else:
        fp.write('&#%d;'%ord(s[0]))

      s=s[1:]
