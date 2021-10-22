#!/usr/bin/python
#
# Useful HTML facilities.
#       - Cameron Simpson <cs@cskk.id.au>
#

from __future__ import absolute_import

DISTINFO = {
    'description': "easy HTML and XHTML transcription",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.logutils', 'cs.py3'],
}

from io import StringIO
import re
import sys
from cs.logutils import warning
from cs.py3 import StringTypes
from cs.x import X

# Characters safe to transcribe unescaped.
re_SAFETEXT = re.compile(r'[^<>&]+')
# Characters safe to use inside "" in tag attribute values.
# See HTML 4.01 section 3.2.2
re_SAFETEXT_DQ = re.compile(r'[-a-zA-Z0-9._:\s/;(){}%]+')

# convenience wrappers
A = lambda *tok: ['A'] + list(tok)
B = lambda *tok: ['B'] + list(tok)
NBSP = ['&nbsp;']
TH = lambda *tok: ['TH'] + list(tok)
TD = lambda *tok: ['TD'] + list(tok)
TR = lambda *tok: ['TR'] + list(tok)
META = lambda name, content: ['META', {'name': name, 'content': content}]
LINK = lambda rel, href, **kw: ['LINK',
                                dict([('rel', rel), ('href', href)] + list(kw.items()))]
SCRIPT_SRC = lambda src, ctype='text/javascript': [ 'SCRIPT', {'src': src, 'type': ctype}]

comment = lambda *tok: ['<!--'] + list(tok)
entity = lambda entity_name: [ '&' + entity_name + ';' ]

def page_HTML(title, *tokens, **kw):
  ''' Convenience function returning an '<HTML>' token for a page.
      Keyword parameters:
      `content_type`: "http-equiv" Content-Type, default: "text/html; charset=UTF-8".
      `head_tokens`: optional extra markup tokens for the HEAD section.
      `body_attrs`: optional attributes for the BODY section tag.
  '''
  content_type = kw.pop('content_type', 'text/html; charset=UTF-8')
  head_tokens = kw.pop('head_tokens', ())
  body_attrs = kw.pop('body_attrs', {})
  if kw:
    raise ValueError("unexpected keywords: %r" % (kw,))
  body = ['BODY', body_attrs]
  body.extend(tokens)
  head = ['HEAD',
          ['META', {
              'http-equiv': 'Content-Type', 'content': content_type}], '\n',
          ['TITLE', title], '\n',
          ]
  head.extend(head_tokens)
  return ['HTML',
          head,
          body,
          ]

def attrquote(s):
  ''' Quote a string for use as a tag attribute.
      See HTML 4.01 section 3.2.2.
  '''
  qsv = ['"']
  offset = 0
  while offset < len(s):
    m = re_SAFETEXT_DQ.search(s, offset)
    if not m:
      break
    for c in s[offset:m.start()]:
      qsv.extend( ('&#', str(ord(c)), ';') )
    qsv.append(m.group())
    offset = m.end()
  qsv.append(s[offset:])
  qsv.append('"')
  return ''.join(qsv)

def nbsp(s):
  ''' Generator yielding tokens to translate all whitespace in `s` into &nbsp; entitites.
      Example:
        list(nobr('a b  cd')) ==> ['a', ['&nbsp;'], 'b', ['&nbsp;'], ['&nbsp;'], 'cd']
  '''
  wordchars = []
  for c in s:
    if c.isspace():
      if wordchars:
        yield ''.join(wordchars)
        wordchars = []
      yield NBSP
    else:
      wordchars.append(c)
  if wordchars:
    yield ''.join(wordchars)

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
  return _transcribe(False, *tokens)

def transcribe_s(*tokens):
  ''' Transcribe tokens as HTML text and return the text.
      Convenience wrapper for transcribe().
  '''
  return ''.join(transcribe(*tokens))

def xtranscribe(*tokens):
  ''' Transcribe tokens as XHTML text and yield text portions as generated.
      A token is a string, a sequence or a Tag object.
      A string is safely transcribed as flat text.
      A sequence has:
        [0] the tag name
        [1] optionally a mapping of attribute values
        Further elements are tokens contained within this token.
  '''
  return _transcribe(True, *tokens)

def xtranscribe_s(*tokens):
  ''' Transcribe tokens as XHTML text and return the text.
      Convenience wrapper for xtranscribe().
  '''
  return ''.join(xtranscribe(*tokens))

def _transcribe(is_xhtml, *tokens):
  ''' Transcribe tokens as HTML or XHTML text and yield text portions as generated.
      A token is a string, a sequence or a Tag object.
      A string is safely transcribed as flat text.
      A sequence has:
        [0] the tag name
        [1] optionally a mapping of attribute values
        Further elements are tokens contained within this token.
  '''
  for tok in tokens:
    if isinstance(tok, StringTypes):
      for txt in transcribe_string(tok):
        yield txt
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
    if tag == '<!--':
      yield '<!--'
      buf = StringIO()
      for t in tok:
        if not isinstance(t, StringTypes):
          raise ValueError("invalid non-string inside \"<!--\" comment: %r" % (t,))
        buf.write(t)
      comment_text = buf.getvalue()
      buf.close()
      if '-->' in comment_text:
        raise ValueError("invalid \"-->\" inside \"<!--\" comment: %r" % (comment,))
      yield comment_text
      yield '-->'
      continue
    # HTML is case insensitive and XHTML has lower case tags
    tag = tag.lower()
    is_single = tag in ('br', 'img', 'hr', 'link', 'meta', 'input')
    is_SCRIPT = (tag.lower() == 'script')
    if is_SCRIPT:
      if 'language' not in [a.lower() for a in attrs.keys()]:
        attrs['language'] = 'JavaScript'
      if 'src' in attrs:
        if tok:
          warning("<script> with src=, discarding internal tokens: %r", tokens)
          tok = ()
    yield '<'
    yield tag
    for k in sorted(attrs.keys()):
      v = attrs[k]
      yield ' '
      yield k
      if is_xhtml and v is None:
        v = k
      if v is not None:
        yield '='
        yield attrquote(str(v))
    if is_xhtml and is_single:
      yield '/'
    yield '>'
    # protect inline SCRIPT source code with HTML comments
    if is_SCRIPT and 'src' not in attrs:
      yield "<!--\n"
    for txt in _transcribe(is_xhtml, *tok):
      if is_single:
        error("content inside singleton tag %r!", tag)
        break
      yield txt
    if is_SCRIPT and 'src' not in attrs:
      yield "\n-->"
    if not is_single:
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
        yield '&#%d;' % ord(s[0])
      s = s[1:]
