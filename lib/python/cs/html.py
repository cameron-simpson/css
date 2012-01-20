#!/usr/bin/python
#
# Useful HTML facilities.
#       - Cameron Simpson <cs@zip.com.au>
#

import re
import sys
import urllib
from types import StringTypes, IntType, LongType, FloatType
import cStringIO

# Characters safe to transcribe unescaped.
textSafeRe = re.compile(r'[^<>&]+')
# Characters safe to use inside "" in tag attribute values.
dqAttrValSafeRe = re.compile(r'[-=. \w:@/?~#+&]+')

BR = ('BR',)

def tok2s(*tokens):
  ''' Return transcription of token `tok` in HTML form.
  '''
  fp = cStringIO.StringIO()
  puttok(fp, *tokens)
  s = fp.getvalue()
  fp.close()
  return s

def puttok(fp, *tokens):
  ''' Transcribe tokens to HTML text.
      A token is a string, a sequence or a Tag object.
      A string is safely transcribed as flat text.
      A sequence has:
        [0] the tag name
        [1] optionally a mapping of attribute values
        Further elements are tokens contained within this token.
  '''
  for tok in tokens:
    toktype = type(tok)
    if toktype in StringTypes:
      return puttext(fp, tok)
    if toktype in (IntType, LongType, FloatType):
      return puttext(fp, str(tok))

    # token
    try:
      tag = tok.tag
      attrs = tok.attrs
    except AttributeError:
      # not a dict
      # [ "&ent;" ] is an HTML character entity
      if len(tok) == 1 and tok[0].startswith('&'):
        fp.write(tok[0])
        return
      # raw array [ tag[, attrs][, tokens...] ]
      tok = list(tok)
      tag = tok.pop(0)
      if len(tok) > 0 and hasattr(tok[0], 'keys'):
        attrs = tok.pop(0)
      else:
        attrs = {}

    isSCRIPT=(tag.upper() == 'SCRIPT')
    if isSCRIPT:
      if 'LANGUAGE' not in [a.upper() for a in attrs.keys()]:
        attrs['language']='JavaScript'

    fp.write('<')
    fp.write(tag)
    for k, v in attrs.items():
      fp.write(' ')
      fp.write(k)
      if v is not None:
        fp.write('="')
        fp.write(urllib.quote(str(v), '/#:'))
        fp.write('"')
    fp.write('>')
    if isSCRIPT:
      fp.write("<!--\n")
    for t in tok:
      puttok(fp, t)
    if isSCRIPT:
      fp.write("\n-->")
    if tag not in ('BR',):
      fp.write('</')
      fp.write(tag)
      fp.write('>')

def text2s(s, safeRe=None):
  ''' Return transcription of string in HTML safe form.
  '''
  fp = cStringIO.StringIO()
  puttext(fp, s, safeRe=safeRe)
  s = fp.getvalue()
  fp.close()
  return s

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
