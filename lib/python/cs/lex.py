#!/usr/bin/python

r'''
Lexical analysis functions, tokenisers.

An assortment of lexcial and tokenisation functions useful for writing recursive descent parsers, of which I have several.

Generally the get_* functions accept a source string and an offset (often optional, default 0) and return a token and the new offset, raising ValueError on failed tokenisation.

* as_lines(chunks, partials=None): parse text chunks, yield complete individual lines

* get_chars(s, offset, gochars): collect adjacent characters from `gochars`

* get_decimal(s, offset): collect decimal characters (0-9, string.digits)

* get_delimited(s, offset, delim): collect text up to the first ocurrence of the character `delim`.

* get_envvar(s, offset=0, environ=None, default=None, specials=None): parse an environment variable reference such as $foo

* get_identifier(s, offset=0, alpha=ascii_letters, number=digits, extras='_'): parse an identifier

* get_nonwhite(s, offset=0): collect nonwhitespace characters

* get_other_chars(s, offset=0, stopchars=None): collect adjacent characters not from `stopchars`

* get_qstr(s, offset=0, q='"', environ=None, default=None, env_specials=None): collect a quoted string, honouring slosh escapes and optionally expanding environment variable references

* get_sloshed_text(s, delim, offset=0, slosh='\\', mapper=slosh_mapper, specials=None): collect some slosh escaped text with optional special tokens (such as '$' introducing '$foo')

* get_tokens(s, offset, getters): collect a sequence of tokens specified in `getters`

* match_tokens(s, offset, getters): wrapper for get_tokens which catches ValueError and returns None instead

* get_uc_identifier(s, offset=0, number=digits, extras='_'): collect an UPPERCASE identifier

* get_white(s, offset=0): collect whitespace characters

* isUC_(s): test if a string looks like an upper case identifier

* htmlify(s,nbsp=False): transcribe text in HTML safe form, using &lt; for "<", etc

* htmlquote(s): transcribe text as HTML quoted string suitable for HTML tag attribute values

* jsquote(s): transcribe text as JSON quoted string; essentially like htmlquote without its htmlify step

* parseUC_sAttr(attr): parse FOO or FOOs (or FOOes) and return (FOO, is_plural)

* slosh_mapper(c, charmap=SLOSH_CHARMAP): return a string to replace \c; the default charmap matches Python slosh escapes

* texthexify(bs, shiftin='[', shiftout=']', whitelist=None): a function like binascii.hexlify but also supporting embedded "printable text" subsequences for compactness and human readbility in the result; initial use case was for transcription of binary data with frequent text, specificly directory entry data

* untexthexify(s, shiftin='[', shiftout=']'): the inverse of texthexify()

* unctrl(s,tabsize=8): transcribe text removing control characters
'''

import binascii
from functools import partial
import os
from string import printable, whitespace, ascii_letters, ascii_uppercase, digits
import sys
from cs.py3 import bytes, ustr, sorted, StringTypes, joinbytes

DISTINFO = {
    'description': "lexical analysis, tokenisers",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.py3'],
}

unhexify = binascii.unhexlify
if sys.hexversion >= 0x030000:
  def hexify(bs):
    return binascii.hexlify(bs).decode()
else:
  hexify = binascii.hexlify

ord_space = ord(' ')

def unctrl(s, tabsize=8):
  s2 = ''
  sofar = 0
  for i, ch in enumerate(s):
    ch2 = None
    if ch == '\t':
      pass
    elif ch == '\f':
      ch2 = '\\f'
    elif ch == '\n':
      ch2 = '\\n'
    elif ch == '\r':
      ch2 = '\\r'
    elif ch == '\v':
      ch2 = '\\v'
    else:
      o = ord(ch)
      if o < ord_space or printable.find(ch) == -1:
        if o >= 256:
          ch2 = "\\u%04x" % o
        else:
          ch2 = "\\%03o" % o

    if ch2 is not None:
      if sofar < i:
        s2 += s[sofar:i]
      s2 += ch2
      sofar = i + 1

  if sofar < len(s):
    s2 += s[sofar:]

  return s2.expandtabs(tabsize)

def tabpadding(padlen, tabsize=8, offset=0):
  pad = ''
  nexttab = tabsize - offset % tabsize
  while nexttab <= padlen:
    pad += '\t'
    padlen -= nexttab
    nexttab = tabsize

  if padlen > 0:
    pad += "%*s" % (padlen, ' ')

  return pad

def strlist(ary, sep=", "):
  return sep.join([str(a) for a in ary])

def lastlinelen(s):
  ''' The length of text after the last newline in a string.
      Initially used by cs.hier to compute effective text width.
  '''
  return len(s) - s.rfind('\n') - 1

def htmlify(s, nbsp=False):
  s = s.replace("&", "&amp;")
  s = s.replace("<", "&lt;")
  s = s.replace(">", "&gt;")
  if nbsp:
    s = s.replace(" ", "&nbsp;")
  return s

def htmlquote(s):
  s = htmlify(s)
  s = s.replace("\"", "&dquot;")
  return "\"" + s + "\""

def jsquote(s):
  ''' Quote a string for use in JavaScript.
  '''
  s = s.replace("\"", "&dquot;")
  return "\"" + s + "\""

def phpquote(s):
  ''' Quote a string for use in PHP code.
  '''
  return "'" + s.replace('\\', '\\\\').replace("'", "\\'") + "'"

# characters that may appear in text sections of a texthexify result
# Notable exclusions:
#  \ - to avoid double in slosh escaped presentation
#  % - likewise, for percent escaped presentation
#  [ ] - the delimiters of course
#  { } - used for JSON data and some other markup
#  / - path separator
#
_texthexify_white_chars = ascii_letters + digits + '_-+.,'

def texthexify(bs, shiftin='[', shiftout=']', whitelist=None):
  ''' Transcribe the bytes `bs` to text.
      `whitelist`: a bytes or string object indicating byte values
        which may be represented directly in text; string objects are
        converted to hexify() and texthexify() output strings may be
        freely concatenated and decoded with untexthexify().
  '''
  if whitelist is None:
    whitelist = _texthexify_white_chars
  if isinstance(whitelist, StringTypes) and not isinstance(whitelist, bytes):
    whitelist = bytes(ord(ch) for ch in whitelist)
  inout_len = len(shiftin) + len(shiftout)
  chunks = []
  offset = 0
  offset0 = offset
  inwhite = False
  while offset < len(bs):
    b = bs[offset]
    if inwhite:
      if b not in whitelist:
        inwhite = False
        if offset - offset0 > inout_len:
          # gather up whitelist span if long enough to bother
          chunk = (shiftin
                   + ''.join(chr(bs[o]) for o in range(offset0, offset))
                   + shiftout
                   )
        else:
          # transcribe as hex anyway - too short
          chunk = hexify(bs[offset0:offset])
        chunks.append(chunk)
        offset0 = offset
    else:
      if b in whitelist:
        inwhite = True
        chunk = hexify(bs[offset0:offset])
        chunks.append(chunk)
        offset0 = offset
    offset += 1
  if offset > offset0:
    if inwhite and offset - offset0 > inout_len:
      chunk = (shiftin
               + ''.join(chr(bs[o]) for o in range(offset0, offset))
               + shiftout
               )
    else:
      chunk = hexify(bs[offset0:offset])
    chunks.append(chunk)
  return ''.join(chunks)

def untexthexify(s, shiftin='[', shiftout=']'):
  ''' Decode a textual representation of binary data into binary data.
      Outside of the `shiftin`/`shiftout` markers the binary data
      are represented as hexadecimal. Within the markers the bytes
      have the values of the ordinals of the characters.
  '''
  chunks = []
  while s:
    hexlen = s.find(shiftin)
    if hexlen < 0:
      break
    if hexlen > 0:
      hextext = s[:hexlen]
      if hexlen % 2 != 0:
        raise ValueError("uneven hex sequence %r" % (hextext,))
      chunks.append(unhexify(s[:hexlen]))
    s = s[hexlen + len(shiftin):]
    textlen = s.find(shiftout)
    if textlen < 0:
      raise ValueError("missing shift out marker \"%s\"" % (shiftout,))
    if sys.hexversion < 0x03000000:
      chunks.append(s[:textlen])
    else:
      chunks.append(bytes(ord(c) for c in s[:textlen]))
    s = s[textlen + len(shiftout):]
  if s:
    if len(s) % 2 != 0:
      raise ValueError("uneven hex sequence \"%s\"" % (s,))
    chunks.append(unhexify(s))
  return joinbytes(chunks)

def get_chars(s, offset, gochars):
  ''' Scan the string `s` for characters in `gochars` starting at `offset`.
      Return (match, new_offset).
  '''
  ooffset = offset
  while offset < len(s) and s[offset] in gochars:
    offset += 1
  return s[ooffset:offset], offset

def get_white(s, offset=0):
  ''' Scan the string `s` for characters in string.whitespace starting at `offset` (default 0).
      Return (match, new_offset).
  '''
  return get_chars(s, offset, whitespace)

def skipwhite(s, offset=0):
  ''' Convenience routine for skipping past whitespace; returns offset of next nonwhitespace character.
  '''
  _, offset = get_white(s, offset=offset)
  return offset

def get_nonwhite(s, offset=0):
  ''' Scan the string `s` for characters not in string.whitespace starting at `offset` (default 0).
      Return (match, new_offset).
  '''
  return get_other_chars(s, offset=offset, stopchars=whitespace)

def get_decimal(s, offset=0):
  ''' Scan the string `s` for decimal characters starting at `offset`.
      Return dec_string, new_offset.
  '''
  return get_chars(s, offset, digits)

def get_hexadecimal(s, offset=0):
  ''' Scan the string `s` for hexadecimal characters starting at `offset`.
      Return hex_string, new_offset.
  '''
  return get_chars(s, offset, '0123456789abcdefABCDEF')

def get_identifier(s, offset=0, alpha=ascii_letters, number=digits, extras='_'):
  ''' Scan the string `s` for an identifier (by default an ASCII letter or underscore followed by letters, digits or underscores) starting at `offset` (default 0).
      Return (match, new_offset).
      The empty string and an unchanged offset will be returned if
      there is no leading letter/underscore.
  '''
  if offset >= len(s):
    return '', offset
  ch = s[offset]
  if ch not in alpha and ch not in extras:
    return '', offset
  idtail, offset = get_chars(s, offset + 1, alpha + number + extras)
  return ch + idtail, offset

def is_identifier(s, offset=0, **kw):
  ''' Test if the string `s` is an identifier from position `offset` onward.
  '''
  s2, offset2 = get_identifier(s, offset=offset, **kw)
  return s2 and offset2 == len(s)

def get_uc_identifier(s, offset=0, number=digits, extras='_'):
  ''' Scan the string `s` for an identifier as for get_identifier(), but require the letters to be uppercase.
  '''
  return get_identifier(s, offset=offset, alpha=ascii_uppercase, number=number, extras=extras)

def get_dotted_identifier(s, offset=0, **kw):
  ''' Scan the string `s` for a dotted identifier (by default an ASCII letter or underscore followed by letters, digits or underscores) with optional trailing dot and another dotted identifier, starting at `offset` (default 0).
      Return (match, new_offset).
      The empty string and an unchanged offset will be returned if
      there is no leading letter/underscore.
  '''
  offset0 = offset
  _, offset = get_identifier(s, offset=offset, **kw)
  if _:
    while offset < len(s) - 1 and s[offset] == '.':
      _, offset2 = get_identifier(s, offset=offset + 1, **kw)
      if not _:
        break
      offset = offset2
  return s[offset0:offset], offset

def is_dotted_identifier(s, offset=0, **kw):
  ''' Test if the string `s` is an identifier from position `offset` onward.
  '''
  s2, offset2 = get_dotted_identifier(s, offset=offset, **kw)
  return s2 and offset2 == len(s)

def get_other_chars(s, offset=0, stopchars=None):
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
    'a': '\a',
    'b': '\b',
    'f': '\f',
    'n': '\n',
    'r': '\r',
    't': '\t',
    'v': '\v',
}

def slosh_mapper(c, charmap=SLOSH_CHARMAP):
  ''' Return a string to replace backslash-`c`, or None.
  '''
  return charmap.get(c)

def get_sloshed_text(s, delim, offset=0, slosh='\\', mapper=slosh_mapper, specials=None):
  ''' Collect slosh escaped text from the string `s` from position `offset` (default 0) and return the decoded unicode string and the offset of the completed parse.
      `delim`: end of string delimiter, such as a single or double quote.
      `offset`: starting offset within `s`, default 0.
      `slosh`: escape character, default a slosh ('\\').
      `mapper`: a mapping function which accepts a single character
        and returns a replacement string or None; this is used the
        replace things such as '\\t' or '\\n'. The default is the
        slosh_mapper function, whose default mapping is SLOSH_CHARMAP.
      `specials`: a mapping of other special character sequences and parse
        functions for gathering them up. When one of the special
        character sequences is found in the string, the parse
        function is called to parse at that point.
        The parse functions accept
        `s` and the offset of the special character. They return
        the decoded string and the offset past the parse.
      The escape character `slosh` introduces an encoding of some
      replacement text whose value depends on the following character.
      If the following character is:
        - the escape character `slosh`, insert the escape character.
        - the string delimiter `delim`, insert the delimiter.
        - the character 'x', insert the character with code from
          the following 2 hexadecimal digits.
        - the character 'u', insert the character with code from
          the following 4 hexadecimal digits.
        - the character 'U', insert the character with code from
          the following 8 hexadecimal digits.
        - a character from the keys of mapper
  '''
  if specials is not None:
    # gather up starting character of special keys and a list of
    # keys in reverse order of length
    special_starts = set()
    special_seqs = []
    for special in specials.keys():
      if not special:
        raise ValueError(
            'empty strings may not be used as keys for specials: %r' % (specials,))
      special_starts.add(special[0])
      special_seqs.append(special)
    special_starts = u''.join(special_starts)
    special_seqs = sorted(special_seqs, key=lambda s: -len(s))
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
    if c == slosh:
      # \something
      if offset >= slen:
        raise ValueError('incomplete slosh escape at offset %d' % (offset0,))
      offset1 = offset
      c = s[offset]
      offset += 1
      if c == slosh or (delim is not None and c == delim):
        chunks.append(c)
        continue
      if c == 'x':
        # \xhh
        if slen - offset < 2:
          raise ValueError(
              'short hexcode for %sxhh at offset %d' % (slosh, offset0))
        hh = s[offset:offset + 2]
        offset += 2
        chunks.append(chr(int(hh, 16)))
        continue
      if c == 'u':
        # \uhhhh
        if slen - offset < 4:
          raise ValueError(
              'short hexcode for %suhhhh at offset %d' % (slosh, offset0))
        hh = s[offset:offset + 4]
        offset += 4
        chunks.append(chr(int(hh, 16)))
        continue
      if c == 'U':
        # \Uhhhhhhhh
        if slen - offset < 8:
          raise ValueError(
              'short hexcode for %sUhhhhhhhh at offset %d' % (slosh, offset0))
        hh = s[offset:offset + 8]
        offset += 8
        chunks.append(chr(int(hh, 16)))
        continue
      chunk = mapper(c)
      if chunk is not None:
        # supplied \X mapping
        chunks.append(chunk)
        continue
      # check for escaped special syntax
      if specials is not None and c in special_starts:
        # test sequence prefixes from longest to shortest
        chunk = None
        for seq in special_seqs:
          if s.startswith(seq, offset1):
            # special sequence
            chunk = c
            break
        if chunk is not None:
          chunks.append(chunk)
          continue
      raise ValueError(
          'unrecognised %s%s escape at offset %d' % (slosh, c, offset0))
    if specials is not None and c in special_starts:
      # test sequence prefixes from longest to shortest
      chunk = None
      for seq in special_seqs:
        if s.startswith(seq, offset0):
          # special sequence
          chunk, offset = specials[seq](s, offset0)
          if offset < offset0 + 1:
            raise ValueError(
                "special parser for %r at offset %d moved offset backwards" % (c, offset0))
          break
      if chunk is not None:
        chunks.append(chunk)
        continue
      chunks.append(c)
      continue
    while offset < slen:
      c = s[offset]
      if (
          c == slosh
          or (delim is not None and c == delim)
          or (specials is not None and c in special_starts)
      ):
        break
      offset += 1
    chunks.append(s[offset0:offset])
  return u''.join(ustr(chunk) for chunk in chunks), offset

def get_envvar(s, offset=0, environ=None, default=None, specials=None):
  ''' Parse a simple environment variable reference to $varname or $x where "x" is a special character.
      `s`: the string with the variable reference
      `offset`: the starting point for the reference
      `default`: default value for missing environment variables;
         if None (the default) a ValueError is raised
      `environ`: the environment mapping, default os.environ
      `specials`: the mapping of special single character variables
  '''
  if environ is None:
    environ = os.environ
  offset0 = offset
  if not s.startswith('$', offset):
    raise ValueError("no leading '$' at offset %d: %r" % (offset, s))
  offset += 1
  if offset >= len(s):
    raise ValueError(
        "short string, nothing after '$' at offset %d" % (offset,))
  identifier, offset = get_identifier(s, offset)
  if identifier:
    value = environ.get(identifier, default)
    if value is None:
      raise ValueError("unknown envvar name $%s, offset %d: %r"
                       % (identifier, offset0, s))
    return value, offset
  c = s[offset]
  offset += 1
  if specials is not None and c in specials:
    return specials[c], offset
  raise ValueError("unsupported special variable $%s" % (c,))

def get_qstr(s, offset=0, q='"', environ=None, default=None, env_specials=None):
  ''' Get quoted text with slosh escapes and optional environment substitution.
      `s`: the string containg the quoted text.
      `offset`: the starting point, default 0.
      `q`: the quote character, default '"'. If `q` is set to None,
        do not expect the string to be delimited by quote marks.
      `environ`: if not None, also parse and expand $envvar references.
      `default`: passed to get_envvar
  '''
  if environ is None and default is not None:
    raise ValueError(
        "environ is None but default is not None (%r)" % (default,))
  if q is None:
    delim = None
  else:
    if offset >= len(s):
      raise ValueError("short string, no opening quote")
    delim = s[offset]
    offset += 1
    if delim != q:
      raise ValueError("expected opening quote %r, found %r" % (q, delim,))
  if environ is None:
    return get_sloshed_text(s, delim, offset)
  getvar = partial(
      get_envvar, environ=environ, default=default, specials=env_specials)
  return get_sloshed_text(s, delim, offset, specials={'$': getvar})

def get_delimited(s, offset, delim):
  ''' Collect text from the string `s` from position `offset` up to the first occurence of delimiter `delim`; return the text excluding the delimiter and the offset after the delimiter.
  '''
  pos = s.find(delim, offset)
  if pos < offset:
    raise ValueError(
        "delimiter %r not found after offset %d" % (delim, offset))
  return s[offset:pos], pos + len(delim)

def get_tokens(s, offset, getters):
  ''' Parse the string `s` from position `offset` using the supplied tokenise functions `getters`; return the list of tokens matched and the final offset.
      `s`: the string to parse.
      `offset`: the starting position for the parse.
      `getters`: an iterable of tokeniser specifications.
      Each tokeniser specification is either:
      - a callable expecting (s, offset) and returning (token, new_offset)
      - a literal string, to be matched exactly
      - a tuple or list with values (func, args, kwargs);
        call func(s, offset, *args, **kwargs)
      - an object with a .match method such as a regex;
        call getter.match(s, offset) and return a match object with
        a .end() method returning the offset of the end of the match
  '''
  tokens = []
  for getter in getters:
    args = ()
    kwargs = {}
    if callable(getter):
      func = getter
    elif isinstance(getter, StringTypes):
      def func(s, offset):
        if s.startswith(getter, offset):
          return getter, offset + len(getter)
        raise ValueError("string %r not found at offset %d" % (getter, offset))
    elif isinstance(getter, (tuple, list)):
      func, args, kwargs = getter
    elif hasattr(getter, 'match'):
      def func(s, offset):
        m = getter.match(s, offset)
        if m:
          return m, m.end()
        raise ValueError("no match for %s at offset %d" % (getter, offset))
    else:
      raise ValueError("unsupported getter: %r" % (getter,))
    token, offset = func(s, offset, *args, **kwargs)
    tokens.append(token)
  return tokens, offset

def match_tokens(s, offset, getters):
  ''' Wrapper for get_tokens which catches ValueError exceptions and returns (None, offset).
  '''
  try:
    tokens, offset2 = get_tokens(s, offset, getters)
  except ValueError:
    return None, offset
  else:
    return tokens, offset2

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
        k = attr[:-2]
        if isUC_(k):
          return k, True
      else:
        k = attr[:-1]
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
  if any(['\n' in p for p in partials]):
    raise ValueError("newline in partials: %r", partials)
  for chunk in chunks:
    pos = 0
    nl_pos = chunk.find('\n', pos)
    while nl_pos >= pos:
      partials.append(chunk[pos:nl_pos + 1])
      yield ''.join(partials)
      partials[:] = ()
      pos = nl_pos + 1
      nl_pos = chunk.find('\n', pos)
    if pos < len(chunk):
      partials.append(chunk[pos:])

if __name__ == '__main__':
  import cs.lex_tests
  cs.lex_tests.selftest(sys.argv)
