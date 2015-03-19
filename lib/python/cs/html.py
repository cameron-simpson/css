#!/usr/bin/python
#
# Useful HTML facilities.
#       - Cameron Simpson <cs@zip.com.au>
#

DISTINFO = {
    'description': "easy HTML transcription",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'requires': ['cs.py3'],
}

import re
import sys
try:
  from urllib.parse import quote as urlquote
except ImportError:
  from urllib import quote as urlquote
from cs.py3 import StringTypes

# Characters safe to transcribe unescaped.
re_SAFETEXT = re.compile(r'[^<>&]+')
# Characters safe to use inside "" in tag attribute values.
re_SAFETEXT_DQ = re.compile(r'[-=. \w:@/?~#+&]+')

def page_HTML(title, *tokens):
    ''' Covenience function returning an '<HTML>' token for a page.
    '''
    body = ['BODY']
    body.extend(*tokens)
    return ['HTML',
             ['HEAD',
              ['TITLE', title]
             ],
             body,
           ]

def tok2s(*tokens):
  ''' Transcribe tokens to a string, return the string.
      Trivial wrapper for transcribe().
  '''
  return ''.join(transcribe(*tokens))

def puttok(fp, *tokens):
  ''' Transcribe tokens as HTML text to the file `fp`.
      Trivial wrapper for transcribe().
  '''
  for s in transcribe(*tokens):
    fp.write(s)

def transcribe(*tokens):
  ''' Transcribe tokens as HTML text and yield text portions as generated.
      A token is a string, a sequence or a Tag object.
      A string is safely transcribed as flat text.
      A sequence has:
        [0] the tag name
        [1] optionally a mapping of attribute values
        Further elements are tokens contained within this token.
  '''
  for tok in tokens:
    if isinstance(tok, StringTypes):
      yield from transcribe_string(tok)
      continue
    if isinstance(tok, (int, float)):
      yield str(tok)
      continue
    # token
    try:
      tag = tok.tag
      attrs = tok.attrs
    except AttributeError:
      # not a preformed token with .tag and .attrs
      # [ "&ent;" ] is an HTML character entity
      if len(tok) == 1 and tok[0].startswith('&'):
        yield tok[0]
        continue
      # raw array [ tag[, attrs][, tokens...] ]
      tok = list(tok)
      tag = tok.pop(0)
      if len(tok) > 0 and hasattr(tok[0], 'keys'):
        attrs = tok.pop(0)
      else:
        attrs = {}
    TAG = tag.upper()
    isSCRIPT=( TAG == 'SCRIPT' )
    if isSCRIPT:
      if 'LANGUAGE' not in [a.upper() for a in attrs.keys()]:
        attrs['language'] = 'JavaScript'
    yield '<'
    yield tag
    for k, v in attrs.items():
      yield ' '
      yield k
      if v is not None:
        yield '="'
        yield urlquote(str(v), safe=' /#:;')
        yield '"'
    yield '>'
    if isSCRIPT:
      yield "<!--\n"
    yield from transcribe(*tok)
    if isSCRIPT:
      yield "\n-->"
    if tag not in ('BR', 'IMG', 'HR'):
      yield '</'
      yield tag
      yield '>'

def quote(s, safe_re=None):
  ''' Return transcription of string in HTML safe form.
  '''
  return ''.join(transcribe_string(s))

def puttext(fp, s, safe_re=None):
  ''' Transcribe plain text in HTML safe form to the file `fp`.
      Trivial wrapper for transcribe_string().
  '''
  for chunk in transcribe_string(s, safe_re=safe_re):
    fp.write(chunk)

def transcribe_string(s, safe_re=None):
  ''' Generator yielding HTML text chunks transcribing the string `s`.
  '''
  if safe_re is None:
    safe_re = re_SAFETEXT
  while len(s):
    m = safe_re.match(s)
    if m:
      safetext = m.group(0)
      yield safetext
      s = s[len(safetext):]
    else:
      if s[0] == '<':
        yield '&lt;'
      elif s[0] == '>':
        yield '&gt;'
      elif s[0] == '&':
        yield '&amp;'
      else:
        yield '&#%d;'%ord(s[0])
      s=s[1:]
