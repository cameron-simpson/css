#!/usr/bin/python

r'''
Lexical analysis functions, tokenisers, transcribers:
an arbitrary assortment of lexical and tokenisation functions useful
for writing recursive descent parsers, of which I have several.
There are also some transcription functions for producing text
from various objects, such as `hexify` and `unctrl`.

Generally the get_* functions accept a source string and an offset
(usually optional, default `0`) and return a token and the new offset,
raising `ValueError` on failed tokenisation.
'''

# pylint: disable=too-many-lines

import binascii
from functools import partial
import os
import re
from string import (
    ascii_letters,
    ascii_uppercase,
    digits,
    printable,
    whitespace,
    Formatter,
)
import sys
from textwrap import dedent

from typeguard import typechecked

from cs.deco import fmtdoc, decorator
from cs.gimmicks import warning
from cs.pfx import Pfx, pfx_method
from cs.py.func import funcname
from cs.py3 import bytes, ustr, sorted, StringTypes, joinbytes  # pylint: disable=redefined-builtin
from cs.seq import common_prefix_length, common_suffix_length

__version__ = '20210306-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.gimmicks',
        'cs.py.func',
        'cs.py3',
        'cs.seq>=20200914',
    ],
}

unhexify = binascii.unhexlify
if sys.hexversion >= 0x030000:

  def hexify(bs):
    ''' A flavour of `binascii.hexlify` returning a `str`.
    '''
    return binascii.hexlify(bs).decode()
else:
  hexify = binascii.hexlify

ord_space = ord(' ')

# pylint: disable=too-many-branches
def unctrl(s, tabsize=8):
  ''' Return the string `s` with `TAB`s expanded and control characters
      replaced with printable representations.
  '''
  if tabsize < 1:
    raise ValueError("tabsize(%r) < 1" % (tabsize,))
  s2 = ''
  sofar = 0
  for i, ch in enumerate(s):
    ch2 = None
    if ch == '\t':
      if sofar < i:
        s2 += s[sofar:i]
        sofar = i
      ch2 = ' ' * (tabsize - (len(s2) % tabsize))
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

def lc_(value):
  ''' Return `value.lower()`
      with `'-'` translated into `'_'` and `' '` translated into `'-'`.

      I use this to construct lowercase filenames containing a
      readable transcription of a title string.

      See also `titleify_lc()`, an imperfect reversal of this.
  '''
  return value.lower().replace('-', '_').replace(' ', '-')

def titleify_lc(value_lc):
  ''' Translate `'-'` into `' '` and `'_'` translated into `'-'`,
      then titlecased.

      See also `lc_()`, which this reverses imperfectly.
  '''
  return value_lc.replace('-', ' ').replace('_', '-').title()

def tabpadding(padlen, tabsize=8, offset=0):
  ''' Compute some spaces to use a tab padding at an offfset.
  '''
  pad = ''
  nexttab = tabsize - offset % tabsize
  while nexttab <= padlen:
    pad += '\t'
    padlen -= nexttab
    nexttab = tabsize

  if padlen > 0:
    pad += "%*s" % (padlen, ' ')

  return pad

def typed_str(o, use_cls=False, use_repr=False, max_length=None):
  ''' Return "type(o).__name__:str(o)" for some object `o`.

      Parameters:
      * `use_cls`: default `False`;
        if true, use `str(type(o))` instead of `type(o).__name__`
      * `use_repr`: default `False`;
        if true, use `repr(o)` instead of `str(o)`

      I use this a lot when debugging. Example:

          from cs.lex import typed_str as s
          ......
          X("foo = %s", s(foo))
  '''
  s = "%s:%s" % (
      type(o) if use_cls else type(o).__name__,
      repr(o) if use_repr else str(o),
  )
  if max_length is not None:
    s = cropped(s, max_length)
  return s

def strlist(ary, sep=", "):
  ''' Convert an iterable to strings and join with `sep` (default `', '`).
  '''
  return sep.join([str(a) for a in ary])

def htmlify(s, nbsp=False):
  ''' Convert a string for safe transcription in HTML.

      Parameters:
      * `s`: the string
      * `nbsp`: replaces spaces with `"&nbsp;"` to prevent word folding,
        default `False`.
  '''
  s = s.replace("&", "&amp;")
  s = s.replace("<", "&lt;")
  s = s.replace(">", "&gt;")
  if nbsp:
    s = s.replace(" ", "&nbsp;")
  return s

def htmlquote(s):
  ''' Quote a string for use in HTML.
  '''
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
  ''' Transcribe the bytes `bs` to text using compact text runs for
      some common text values.

      This can be reversed with the `untexthexify` function.

      This is an ad doc format devised to be compact but also to
      expose "text" embedded within to the eye. The original use
      case was transcribing a binary directory entry format, where
      the filename parts would be somewhat visible in the transcription.

      The output is a string of hexadecimal digits for the encoded
      bytes except for runs of values from the whitelist, which are
      enclosed in the shiftin and shiftout markers and transcribed
      as is. The default whitelist is values of the ASCII letters,
      the decimal digits and the punctuation characters '_-+.,'.
      The default shiftin and shiftout markers are '[' and ']'.

      String objects converted with either `hexify` and `texthexify`
      output strings may be freely concatenated and decoded with
      `untexthexify`.

      Example:

          >>> texthexify(b'&^%&^%abcdefghi)(*)(*')
          '265e25265e25[abcdefghi]29282a29282a'

      Parameters:
      * `bs`: the bytes to transcribe
      * `shiftin`: Optional. The marker string used to indicate a shift to
        direct textual transcription of the bytes, default: `'['`.
      * `shiftout`: Optional. The marker string used to indicate a
        shift from text mode back into hexadecimal transcription,
        default `']'`.
      * `whitelist`: an optional bytes or string object indicating byte
        values which may be represented directly in text;
        the default value is the ASCII letters, the decimal digits
        and the punctuation characters `'_-+.,'`.
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
          chunk = (
              shiftin + ''.join(chr(bs[o])
                                for o in range(offset0, offset)) + shiftout
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
      chunk = (
          shiftin + ''.join(chr(bs[o])
                            for o in range(offset0, offset)) + shiftout
      )
    else:
      chunk = hexify(bs[offset0:offset])
    chunks.append(chunk)
  return ''.join(chunks)

def untexthexify(s, shiftin='[', shiftout=']'):
  ''' Decode a textual representation of binary data into binary data.

      This is the reverse of the `texthexify` function.

      Outside of the `shiftin`/`shiftout` markers the binary data
      are represented as hexadecimal. Within the markers the bytes
      have the values of the ordinals of the characters.

      Example:

          >>> untexthexify('265e25265e25[abcdefghi]29282a29282a')
          b'&^%&^%abcdefghi)(*)(*'

      Parameters:
      * `s`: the string containing the text representation.
      * `shiftin`: Optional. The marker string commencing a sequence
        of direct text transcription, default `'['`.
      * `shiftout`: Optional. The marker string ending a sequence
        of direct text transcription, default `']'`.
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
      raise ValueError("missing shift out marker %r" % (shiftout,))
    if sys.hexversion < 0x03000000:
      chunks.append(s[:textlen])
    else:
      chunks.append(bytes(ord(c) for c in s[:textlen]))
    s = s[textlen + len(shiftout):]
  if s:
    if len(s) % 2 != 0:
      raise ValueError("uneven hex sequence %r" % (s,))
    chunks.append(unhexify(s))
  return joinbytes(chunks)

def get_chars(s, offset, gochars):
  ''' Scan the string `s` for characters in `gochars` starting at `offset`.
      Return `(match,new_offset)`.

      `gochars` may also be a callable, in which case a character
      `ch` is accepted if `gochars(ch)` is true.
  '''
  ooffset = offset
  if callable(gochars):
    while offset < len(s) and gochars(s[offset]):
      offset += 1
  else:
    while offset < len(s) and s[offset] in gochars:
      offset += 1
  return s[ooffset:offset], offset

def get_white(s, offset=0):
  ''' Scan the string `s` for characters in `string.whitespace`
      starting at `offset` (default `0`).
      Return `(match,new_offset)`.
  '''
  return get_chars(s, offset, whitespace)

def skipwhite(s, offset=0):
  ''' Convenience routine for skipping past whitespace;
      returns the offset of the next nonwhitespace character.
  '''
  _, offset = get_white(s, offset=offset)
  return offset

def stripped_dedent(s):
  ''' Slightly smarter dedent which ignores a string's opening indent.

      Algorithm:
      strip the supplied string `s`, pull off the leading line,
      dedent the rest, put back the leading line.

      This supports my preferred docstring layout, where the opening
      line of text is on the same line as the opening quote.

      Example:

          >>> def func(s):
          ...   """ Slightly smarter dedent which ignores a string's opening indent.
          ...       Strip the supplied string `s`. Pull off the leading line.
          ...       Dedent the rest. Put back the leading line.
          ...   """
          ...   pass
          ...
          >>> from cs.lex import stripped_dedent
          >>> print(stripped_dedent(func.__doc__))
          Slightly smarter dedent which ignores a string's opening indent.
          Strip the supplied string `s`. Pull off the leading line.
          Dedent the rest. Put back the leading line.
  '''
  s = s.strip()
  lines = s.split('\n')
  if not lines:
    return ''
  line1 = lines.pop(0)
  if not lines:
    return line1
  adjusted = dedent('\n'.join(lines))
  return line1 + '\n' + adjusted

def get_nonwhite(s, offset=0):
  ''' Scan the string `s` for characters not in `string.whitespace`
      starting at `offset` (default `0`).
      Return `(match,new_offset)`.
  '''
  return get_other_chars(s, offset=offset, stopchars=whitespace)

def get_decimal(s, offset=0):
  ''' Scan the string `s` for decimal characters starting at `offset` (default `0`).
      Return `(dec_string,new_offset)`.
  '''
  return get_chars(s, offset, digits)

def get_decimal_value(s, offset=0):
  ''' Scan the string `s` for a decimal value starting at `offset` (default `0`).
      Return `(value,new_offset)`.
  '''
  value_s, offset = get_decimal(s, offset)
  if not value_s:
    raise ValueError("expected decimal value")
  return int(value_s), offset

def get_hexadecimal(s, offset=0):
  ''' Scan the string `s` for hexadecimal characters starting at `offset` (default `0`).
      Return `(hex_string,new_offset)`.
  '''
  return get_chars(s, offset, '0123456789abcdefABCDEF')

def get_hexadecimal_value(s, offset=0):
  ''' Scan the string `s` for a hexadecimal value starting at `offset` (default `0`).
      Return `(value,new_offset)`.
  '''
  value_s, offset = get_hexadecimal(s, offset)
  if not value_s:
    raise ValueError("expected hexadecimal value")
  return int('0x' + value_s), offset

def get_decimal_or_float_value(s, offset=0):
  ''' Fetch a decimal or basic float (nnn.nnn) value
      from the str `s` at `offset` (default `0`).
      Return `(value,new_offset)`.
  '''
  int_part, offset = get_decimal(s, offset)
  if not int_part:
    raise ValueError("expected decimal or basic float value")
  if offset == len(s) or s[offset] != '.':
    return int(int_part), offset
  sub_part, offset = get_decimal(s, offset + 1)
  return float('.'.join((int_part, sub_part))), offset

def get_identifier(
    s, offset=0, alpha=ascii_letters, number=digits, extras='_'
):
  ''' Scan the string `s` for an identifier (by default an ASCII
      letter or underscore followed by letters, digits or underscores)
      starting at `offset` (default 0).
      Return `(match,new_offset)`.

      *Note*: the empty string and an unchanged offset will be returned if
      there is no leading letter/underscore.

      Parameters:
      * `s`: the string to scan
      * `offset`: the starting offset, default `0`.
      * `alpha`: the characters considered alphabetic,
        default `string.ascii_letters`.
      * `number`: the characters considered numeric,
        default `string.digits`.
      * `extras`: extra characters considered part of an identifier,
        default `'_'`.
  '''
  if offset >= len(s):
    return '', offset
  ch = s[offset]
  if ch not in alpha and ch not in extras:
    return '', offset
  idtail, offset = get_chars(s, offset + 1, alpha + number + extras)
  return ch + idtail, offset

def is_identifier(s, offset=0, **kw):
  ''' Test if the string `s` is an identifier
      from position `offset` (default `0`) onward.
  '''
  s2, offset2 = get_identifier(s, offset=offset, **kw)
  return s2 and offset2 == len(s)

def get_uc_identifier(s, offset=0, number=digits, extras='_'):
  ''' Scan the string `s` for an identifier as for `get_identifier`,
      but require the letters to be uppercase.
  '''
  return get_identifier(
      s, offset=offset, alpha=ascii_uppercase, number=number, extras=extras
  )

def get_dotted_identifier(s, offset=0, **kw):
  ''' Scan the string `s` for a dotted identifier (by default an
      ASCII letter or underscore followed by letters, digits or
      underscores) with optional trailing dot and another dotted
      identifier, starting at `offset` (default `0`).
      Return `(match,new_offset)`.

      Note: the empty string and an unchanged offset will be returned if
      there is no leading letter/underscore.

      Keyword arguments are passed to `get_identifier`
      (used for each component of the dotted identifier).
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
  return len(s2) > 0 and offset2 == len(s)

def get_other_chars(s, offset=0, stopchars=None):
  ''' Scan the string `s` for characters not in `stopchars` starting
      at `offset` (default `0`).
      Return `(match,new_offset)`.
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

def slosh_mapper(c, charmap=None):
  ''' Return a string to replace backslash-`c`, or `None`.
  '''
  if charmap is None:
    charmap = SLOSH_CHARMAP
  return charmap.get(c)

# pylint: disable=too-many-arguments,too-many-locals,too-many-branches
# pylint: disable=too-many-statements,too-many-arguments
def get_sloshed_text(
    s, delim, offset=0, slosh='\\', mapper=slosh_mapper, specials=None
):
  ''' Collect slosh escaped text from the string `s` from position
      `offset` (default `0`) and return the decoded unicode string and
      the offset of the completed parse.

      Parameters:
      * `delim`: end of string delimiter, such as a single or double quote.
      * `offset`: starting offset within `s`, default `0`.
      * `slosh`: escape character, default a slosh ('\\').
      * `mapper`: a mapping function which accepts a single character
        and returns a replacement string or `None`; this is used the
        replace things such as '\\t' or '\\n'. The default is the
        `slosh_mapper` function, whose default mapping is `SLOSH_CHARMAP`.
      * `specials`: a mapping of other special character sequences and parse
        functions for gathering them up. When one of the special
        character sequences is found in the string, the parse
        function is called to parse at that point.
        The parse functions accept
        `s` and the offset of the special character. They return
        the decoded string and the offset past the parse.

      The escape character `slosh` introduces an encoding of some
      replacement text whose value depends on the following character.
      If the following character is:
      * the escape character `slosh`, insert the escape character.
      * the string delimiter `delim`, insert the delimiter.
      * the character 'x', insert the character with code from the following
        2 hexadecimal digits.
      * the character 'u', insert the character with code from the following
        4 hexadecimal digits.
      * the character 'U', insert the character with code from the following
        8 hexadecimal digits.
      * a character from the keys of `mapper`
  '''
  if specials is not None:
    # gather up starting character of special keys and a list of
    # keys in reverse order of length
    special_starts = set()
    special_seqs = []
    for special in specials.keys():
      if not special:
        raise ValueError(
            'empty strings may not be used as keys for specials: %r' %
            (specials,)
        )
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
              'short hexcode for %sxhh at offset %d' % (slosh, offset0)
          )
        hh = s[offset:offset + 2]
        offset += 2
        chunks.append(chr(int(hh, 16)))
        continue
      if c == 'u':
        # \uhhhh
        if slen - offset < 4:
          raise ValueError(
              'short hexcode for %suhhhh at offset %d' % (slosh, offset0)
          )
        hh = s[offset:offset + 4]
        offset += 4
        chunks.append(chr(int(hh, 16)))
        continue
      if c == 'U':
        # \Uhhhhhhhh
        if slen - offset < 8:
          raise ValueError(
              'short hexcode for %sUhhhhhhhh at offset %d' % (slosh, offset0)
          )
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
          'unrecognised %s%s escape at offset %d' % (slosh, c, offset0)
      )
    if specials is not None and c in special_starts:
      # test sequence prefixes from longest to shortest
      chunk = None
      for seq in special_seqs:
        if s.startswith(seq, offset0):
          # special sequence
          chunk, offset = specials[seq](s, offset0)
          if offset < offset0 + 1:
            raise ValueError(
                "special parser for %r at offset %d moved offset backwards" %
                (c, offset0)
            )
          break
      if chunk is not None:
        chunks.append(chunk)
        continue
      chunks.append(c)
      continue
    while offset < slen:
      c = s[offset]
      if (c == slosh or (delim is not None and c == delim)
          or (specials is not None and c in special_starts)):
        break
      offset += 1
    chunks.append(s[offset0:offset])
  return u''.join(ustr(chunk) for chunk in chunks), offset

def get_envvar(s, offset=0, environ=None, default=None, specials=None):
  ''' Parse a simple environment variable reference to $varname or
      $x where "x" is a special character.

      Parameters:
      * `s`: the string with the variable reference
      * `offset`: the starting point for the reference
      * `default`: default value for missing environment variables;
         if `None` (the default) a `ValueError` is raised
      * `environ`: the environment mapping, default `os.environ`
      * `specials`: the mapping of special single character variables
  '''
  if environ is None:
    environ = os.environ
  offset0 = offset
  if not s.startswith('$', offset):
    raise ValueError("no leading '$' at offset %d: %r" % (offset, s))
  offset += 1
  if offset >= len(s):
    raise ValueError(
        "short string, nothing after '$' at offset %d" % (offset,)
    )
  identifier, offset = get_identifier(s, offset)
  if identifier:
    value = environ.get(identifier, default)
    if value is None:
      raise ValueError(
          "unknown envvar name $%s, offset %d: %r" % (identifier, offset0, s)
      )
    return value, offset
  c = s[offset]
  offset += 1
  if specials is not None and c in specials:
    return specials[c], offset
  raise ValueError("unsupported special variable $%s" % (c,))

# pylint: disable=too-many-arguments
def get_qstr(
    s, offset=0, q='"', environ=None, default=None, env_specials=None
):
  ''' Get quoted text with slosh escapes and optional environment substitution.

      Parameters:
      * `s`: the string containg the quoted text.
      * `offset`: the starting point, default `0`.
      * `q`: the quote character, default `'"'`. If `q` is `None`,
        do not expect the string to be delimited by quote marks.
      * `environ`: if not `None`, also parse and expand `$`*envvar* references.
      * `default`: passed to `get_envvar`
  '''
  if environ is None and default is not None:
    raise ValueError(
        "environ is None but default is not None (%r)" % (default,)
    )
  if q is None:
    delim = None
  else:
    if offset >= len(s):
      raise ValueError("short string, no opening quote")
    delim = s[offset]
    offset += 1
    if delim != q:
      raise ValueError("expected opening quote %r, found %r" % (
          q,
          delim,
      ))
  if environ is None:
    return get_sloshed_text(s, delim, offset)
  getvar = partial(
      get_envvar, environ=environ, default=default, specials=env_specials
  )
  return get_sloshed_text(s, delim, offset, specials={'$': getvar})

def get_qstr_or_identifier(s, offset):
  ''' Parse a double quoted string or an identifier.
  '''
  if s.startswith('"', offset):
    return get_qstr(s, offset, q='"')
  return get_identifier(s, offset)

def get_delimited(s, offset, delim):
  ''' Collect text from the string `s` from position `offset` up
      to the first occurence of delimiter `delim`; return the text
      excluding the delimiter and the offset after the delimiter.
  '''
  pos = s.find(delim, offset)
  if pos < offset:
    raise ValueError(
        "delimiter %r not found after offset %d" % (delim, offset)
    )
  return s[offset:pos], pos + len(delim)

def get_tokens(s, offset, getters):
  ''' Parse the string `s` from position `offset` using the supplied
      tokeniser functions `getters`.
      Return the list of tokens matched and the final offset.

      Parameters:
      * `s`: the string to parse.
      * `offset`: the starting position for the parse.
      * `getters`: an iterable of tokeniser specifications.

      Each tokeniser specification `getter` is either:
      * a callable expecting `(s,offset)` and returning `(token,new_offset)`
      * a literal string, to be matched exactly
      * a `tuple` or `list` with values `(func,args,kwargs)`;
        call `func(s,offset,*args,**kwargs)`
      * an object with a `.match` method such as a regex;
        call `getter.match(s,offset)` and return a match object with
        a `.end()` method returning the offset of the end of the match
  '''
  tokens = []
  # pylint: disable=cell-var-from-loop
  for getter in getters:
    args = ()
    kwargs = {}
    if callable(getter):
      func = getter
    elif isinstance(getter, StringTypes):

      def func(s, offset):
        ''' Wrapper for a literal string: require the string to be
            present at the current offset.
        '''
        if s.startswith(getter, offset):
          return getter, offset + len(getter)
        raise ValueError("string %r not found at offset %d" % (getter, offset))
    elif isinstance(getter, (tuple, list)):
      func, args, kwargs = getter
    elif hasattr(getter, 'match'):

      def func(s, offset):
        ''' Wrapper for a getter with a .match method, such as a regular
            expression.
        '''
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
  ''' Wrapper for `get_tokens` which catches `ValueError` exceptions
      and returns `(None,offset)`.
  '''
  try:
    tokens, offset2 = get_tokens(s, offset, getters)
  except ValueError:
    return None, offset
  else:
    return tokens, offset2

def isUC_(s):
  ''' Check that a string matches the regular expression `^[A-Z][A-Z_0-9]*$`.
  '''
  if s.isalpha() and s.isupper():
    return True
  if not s:
    return False
  if not s[0].isupper():
    return False
  for c in s[1:]:
    if c != '_' and not c.isupper() and not c.isdigit():
      return False
  return True

def parseUC_sAttr(attr):
  ''' Take an attribute name `attr` and return `(key,is_plural)`.

      Examples:
      * `'FOO'` returns `('FOO',False)`.
      * `'FOOs'` or `'FOOes'` returns `('FOO',True)`.
      Otherwise return `(None,False)`.
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
  ''' Generator yielding complete lines from arbitrary pieces of text from
      the iterable of `str` `chunks`.

      After completion, any remaining newline-free chunks remain
      in the partials list; they will be unavailable to the caller
      unless the list is presupplied.
  '''
  if partials is None:
    partials = []
  if any(['\n' in p for p in partials]):
    raise ValueError("newline in partials: %r" % (partials,))
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

def cutprefix(s, prefix):
  ''' Strip a `prefix` from the front of `s`.
      Return the suffix if `s.startswith(prefix)`, else `s`.

      Example:

          >>> abc_def = 'abc.def'
          >>> cutprefix(abc_def, 'abc.')
          'def'
          >>> cutprefix(abc_def, 'zzz.')
          'abc.def'
          >>> cutprefix(abc_def, '.zzz') is abc_def
          True
  '''
  if prefix and s.startswith(prefix):
    return s[len(prefix):]
  return s

def cutsuffix(s, suffix):
  ''' Strip a `suffix` from the end of `s`.
      Return the prefix if `s.endswith(suffix)`, else `s`.

      Example:

          >>> abc_def = 'abc.def'
          >>> cutsuffix(abc_def, '.def')
          'abc'
          >>> cutsuffix(abc_def, '.zzz')
          'abc.def'
          >>> cutsuffix(abc_def, '.zzz') is abc_def
          True
  '''
  if suffix and s.endswith(suffix):
    return s[:-len(suffix)]
  return s

def common_prefix(*strs):
  ''' Return the common prefix of the strings `strs`.

      Examples:

          >>> common_prefix('abc', 'def')
          ''
          >>> common_prefix('abc', 'abd')
          'ab'
          >>> common_prefix('abc', 'abcdef')
          'abc'
          >>> common_prefix('abc', 'abcdef', 'abz')
          'ab'
          >>> # contrast with cs.fileutils.common_path_prefix
          >>> common_prefix('abc/def', 'abc/def1', 'abc/def2')
          'abc/def'
  '''
  return strs[0][:common_prefix_length(*strs)]

def common_suffix(*strs):
  ''' Return the common suffix of the strings `strs`.
  '''
  length = common_suffix_length(*strs)
  if not length:
    # catch 0 length suffix specially, because -0 == 0
    return ''
  return strs[0][-length:]

def cropped(
    s: str, max_length: int = 32, roffset: int = 1, ellipsis: str = '...'
):
  ''' If the length of `s` exceeds `max_length` (default `32`),
      replace enough of the tail with `ellipsis`
      and the last `roffset` (default `1`) characters of `s`
      to fit in `max_length` characters.
  '''
  if len(s) > max_length:
    if roffset > 0:
      s = s[:max_length - len(ellipsis) - roffset] + ellipsis + s[-roffset:]
    else:
      s = s[:max_length - len(ellipsis)] + ellipsis
  return s

def cropped_repr(o, roffset=1, max_length=32, inner_max_length=None):
  ''' Compute a cropped `repr()` of `o`.

      Parameters:
      * `o`: the object to represent
      * `max_length`: the maximum length of the representation, default `32`
      * `inner_max_length`: the maximum length of the representations
        of members of `o`, default `max_length//2`
      * `roffset`: the number of trailing characters to preserve, default `1`
  '''
  if inner_max_length is None:
    inner_max_length = max_length // 2
  if isinstance(o, (tuple, list)):
    left = '(' if isinstance(o, tuple) else '['
    right = (',)' if len(o) == 1 else ')') if isinstance(o, tuple) else ']'
    o_repr = left + ','.join(
        map(
            lambda m:
            cropped_repr(m, max_length=inner_max_length, roffset=roffset), o
        )
    ) + right
  elif isinstance(o, dict):
    o_repr = '{' + ','.join(
        map(
            lambda kv: cropped_repr(
                kv[0], max_length=inner_max_length, roffset=roffset
            ) + ':' +
            cropped_repr(kv[1], max_length=inner_max_length, roffset=roffset),
            o.items()
        )
    ) + '}'
  else:
    o_repr = repr(o)
  return cropped(o_repr, max_length=max_length, roffset=roffset)

def get_ini_clausename(s, offset=0):
  ''' Parse a `[`*clausename*`]` string from `s` at `offset` (default `0`).
      Return `(clausename,new_offset)`.
  '''
  if not s.startswith('[', offset):
    raise ValueError("missing opening '[' at position %d" % (offset,))
  offset = skipwhite(s, offset + 1)
  clausename, offset = get_qstr_or_identifier(s, offset)
  if not clausename:
    raise ValueError(
        "missing clausename identifier at position %d" % (offset,)
    )
  offset = skipwhite(s, offset)
  if not s.startswith(']', offset):
    raise ValueError("missing closing ']' at position %d" % (offset,))
  return clausename, offset + 1

def get_ini_clause_entryname(s, offset=0):
  ''' Parse a `[`*clausename*`]`*entryname* string
      from `s` at `offset` (default `0`).
      Return `(clausename,entryname,new_offset)`.
  '''
  clausename, offset = get_ini_clausename(s, offset=offset)
  offset = skipwhite(s, offset)
  entryname, offset = get_qstr_or_identifier(s, offset)
  if not entryname:
    raise ValueError("missing entryname identifier at position %d" % (offset,))
  return clausename, entryname, offset

def format_escape(s):
  ''' Escape `{}` characters in a string to protect them from `str.format`.
  '''
  return s.replace('{', '{{').replace('}', '}}')

class FormatAsError(LookupError):
  ''' Subclass of `LookupError` for use by `format_as`.
  '''

  DEFAULT_SEPARATOR = '; '

  def __init__(self, key, format_s, format_mapping, error_sep=None):
    if error_sep is None:
      error_sep = self.DEFAULT_SEPARATOR
    LookupError.__init__(self, key)
    self.args = (key, format_s, format_mapping, error_sep)

  def __str__(self):
    key, format_s, format_mapping, error_sep = self.args
    return error_sep.join(
        (
            "format fails, missing key: %s" % (key,),
            "format string was: %r" % (format_s,),
            "available keys: %s" % (' '.join(sorted(format_mapping.keys()))),
        )
    )

@decorator
def format_recover(method):
  ''' Decorator for `__format__` methods which replaces failed formats
      with `{self:format_spec}`.
  '''

  def format_recovered(self, format_spec):
    try:
      return method(self, format_spec)
    except ValueError as e:
      warning(
          "@format_recover: %s.%s(%r): %s, falling back via %r",
          type(self).__name__, funcname(method), format_spec, e,
          "f'{{{self}:{format_spec}}}'"
      )
      return f'{{{self}:{format_spec}}}'

  return format_recovered

@typechecked
@fmtdoc
def format_as(format_s: str, format_mapping, formatter=None, error_sep=None):
  ''' Format the string `format_s` using `str.format_mapping`,
      return the formatted result.
      This is a wrapper for `str.format_map`
      which raises a more informative `FormatAsError` exception on failure.

      Parameters:
      * `format_s`: the format string to use as the template
      * `format_mapping`: the mapping of available replacement fields
      * `formatter`: an optional `string.Formatter`-like instance
        with a `.vformat(format_string,args,kwargs)` method,
        usually a subclass of `string.Formatter`;
        if not specified then `str.Formatter` is used
      * `error_sep`: optional separator for the multipart error message,
        default from `FormatAsError.DEFAULT_SEPARATOR`:
        `'{FormatAsError.DEFAULT_SEPARATOR}'`
  '''
  if formatter is None:
    formatter = FormatableFormatter(format_mapping)
  try:
    formatted = formatter.vformat(format_s, (), format_mapping)
  except KeyError as e:
    # pylint: disable=raise-missing-from
    raise FormatAsError(
        e.args[0], format_s, format_mapping, error_sep=error_sep
    )
  return formatted

_format_as = format_as

class FormatableMixin(object):  # pylint: disable=too-few-public-methods
  ''' A mixin to supply a `format_as` method for classes,
      which formats a format string using `str.format_map`
      with a suitable mapping derived from the instance.

      An instance with no `format_kwargs` method
      is presumed to be a suitable mapping itself.
      Otherwise the `format_kwargs` method is expected to return a mapping
      for use with str.format_map`.

      The `format_as` method is like an inside out `str.format` or
      `object.__format__` method.
      The `str.format` method is designed for formatting a string
      from a variety of other objects supplied in the keyword arguments,
      and the `object.__format__` method is for filling out a single `str.format`
      replacement field from a single object.
      By contrast, `format_as` is designed to fill out an entire format
      string from the current object.

      For example, the `cs.tagset.TagSetMixin` class
      uses `FormatableMixin` to provide a `format_as` method
      whose replacement fields are derived from the tags in the tag set.
  '''

  @staticmethod
  def convert_via_method_or_attr(value, format_spec):
    ''' Apply a method name based conversion to `value`
        where `format_spec` starts with a method name
        applicable to `value`.
        Return `(converted,offset)`
        being the converted value and the offset after the method name.
    '''
    method_name, offset = get_identifier(format_spec)
    if not method_name:
      # no leading method/attribute name, return unchanged
      return value, 0
    try:
      method = getattr(value, method_name)
    except AttributeError:
      # pylint: disable=raise-missing-from
      raise ValueError(
          "unknown attribute name %s.%r" % (
              typed_str(value),
              method_name,
          )
      )
    if callable(method):
      try:
        converted = method()
      except TypeError as e:
        # pylint: disable=raise-missing-from
        raise ValueError("TypeError calling %s(): %s" % (method_name, e))
    else:
      converted = method
    return converted, offset

  @format_recover
  def __format__(self, format_spec):
    ''' Supply a default `__format__` method
        which falls back to object methods as implementations of `format_spec`.
    '''
    try:
      return super().__format__(format_spec)
    except ValueError:
      converted, offset = self.convert_via_method_or_attr(self, format_spec)
      fully_converted = self.format_get_subfield(
          converted, format_spec[offset:]
      )
      return str(fully_converted)

  def format_as(self, format_s, error_sep=None, **control_kw):
    ''' Return the string `format_s` formatted using the mapping
        returned by `self.format_kwargs(**control_kw)`.

        If a class using the mixin has no `format_kwargs(**control_kw)` method
        to provide a mapping for `str.format_map`
        then the instance itself is used as the mapping.
    '''
    try:
      get_format_mapping = self.format_kwargs
    except AttributeError:
      if control_kw:
        # pylint: disable=raise-missing-from
        raise ValueError(
            "no .format_kwargs() method, but control_kw=%r" % (control_kw,)
        )
      format_mapping = self
    else:
      format_mapping = get_format_mapping(**control_kw)
    return _format_as(
        format_s,
        format_mapping,
        formatter=FormatableFormatter(self),
        error_sep=error_sep
    )

  @staticmethod
  def format_get_arg_name(field_name):
    ''' Default initial arg_name is an identifier.

        Returns `(prefix,offset)`, and `('',0)` if there is no arg_name.
    '''
    return get_identifier(field_name)

  @staticmethod
  def format_convert_field(value, conversion):
    ''' Default converter for fields calls `Formatter.convert_field`.
    '''
    if conversion == '':
      conversion = None
    return Formatter().convert_field(value, conversion)

  # pylint: disable=no-self-use
  def format_format_field(self, value, format_spec):
    ''' Default `FormatableFormatter.format_field` implementation.

        Promote `str` to `FStr`, then use `value.__format__`.
    '''
    if type(value) is str:  # pylint: disable=unidiomatic-typecheck
      value = FStr(value)
    return value.__format__(format_spec)

  @staticmethod
  @typechecked
  def format_get_subfield(value, subfield_text: str):
    ''' Format a subfield of `value` using `FormatableFormatter.get_subfield`.
    '''
    if not subfield_text:
      return value
    return FormatableFormatter(value).get_subfield(value, subfield_text)

class FormatableFormatter(Formatter):
  ''' A `string.Formatter` subclass interacting with objects
      which inherit from `FormatableMixin`.
  '''

  def __init__(self, obj):
    assert isinstance(obj, FormatableMixin)
    self.obj = obj

  def __repr__(self):
    return "%s:%r" % (type(self).__name__, self.obj)

  RE_LITERAL_TEXT = re.compile(r'([^{]+|{{)*')
  RE_IDENTIFIER_s = r'[a-z_][a-z_0-9]*'
  RE_ARG_NAME_s = rf'({RE_IDENTIFIER_s}|\d+)'
  RE_ATTRIBUTE_NAME_s = rf'\.{RE_IDENTIFIER_s}'
  RE_ELEMENT_INDEX_s = r'[^]]*'
  RE_FIELD_EXPR_s = rf'{RE_ARG_NAME_s}({RE_ATTRIBUTE_NAME_s}|\[{RE_ELEMENT_INDEX_s}\])*'
  RE_FIELD_EXPR = re.compile(RE_FIELD_EXPR_s, re.I)
  RE_FIELD = re.compile(
      (
          r'{' + rf'(?P<arg_name>{RE_FIELD_EXPR_s})?' +
          r'(!(?P<conversion>[^:}]*))?' + r'(:(?P<format_spec>[^}]*))?' + r'}'
      ), re.I
  )

  if False:  # pylint: disable=using-constant-test

    @classmethod
    @typechecked
    def parse(cls, format_string: str):
      ''' Parse a format string after the fashion of `Formatter.parse`,
          yielding `(literal,arg_name,format_spec,conversion)` tuples.

          Unlike `Formatter.parse`,
          this does not validate the `conversion` part preemptively,
          supporting extended values for use with the `convert_field` method.
      '''
      offset = 0
      while offset < len(format_string):
        m_literal = cls.RE_LITERAL_TEXT.match(format_string, offset)
        literal = m_literal.group()
        offset = m_literal.end()
        if offset == len(format_string):
          # nothing after the literal text
          if literal:
            yield literal, None, None, None
          return
        m_field = cls.RE_FIELD.match(format_string, offset)
        if not m_field:
          raise ValueError(
              "expected a field at offset %d: found %r" %
              (offset, format_string[offset:])
          )
        yield (
            literal,
            m_field.group('arg_name'),
            m_field.group('format_spec') or '',
            m_field.group('conversion'),
        )
        offset = m_field.end()

  # pylint: disable=arguments-differ
  @pfx_method
  def get_field(self, field_name, a, kw):
    ''' Get the object referenced by the field text `field_name`.
    '''
    assert not a
    with Pfx(
        "self.obj=%s: field_name=%r: kw=%r",
        type(self.obj).__name__,
        field_name,
        kw,
    ):
      arg_name, offset = self.obj.format_get_arg_name(field_name)
      try:
        arg_value, _ = self.get_value(arg_name, a, kw)
      except KeyError as e:
        raise ValueError("no value for arg_name=%r: %s" % (arg_name, e)) from e
      # resolve the rest of the field
      return self.get_subfield(arg_value, field_name[offset:]), field_name

  @staticmethod
  def get_subfield(value, subfield_text: str):
    ''' Resolve `value` against `subfield_text`,
        the remaining field text after the term which resolved to `value`.

        For example, a format `{name.blah[0]}`
        has the field text `name.blah[0]`.
        A `get_field` implementation might initially
        resolve `name` to some value,
        leaving `.blah[0]` as the `subfield_text`.
        This method supports taking that value
        and resolving it against the remaining text `.blah[0]`.

        For generality, if `subfield_text` is the empty string
        `value` is returned unchanged.
    '''
    if subfield_text == '':
      return value
    subfield_fmt = f'{{value{subfield_text}}}'
    subfield_map = {'value': value}
    with Pfx("%r.format_map(%r)", subfield_fmt, subfield_map):
      value = subfield_fmt.format_map(subfield_map)
    if type(value) is str:  # pylint: disable=unidiomatic-typecheck
      value = FStr(value)
    return value

  # pylint: disable=arguments-differ
  @pfx_method
  def get_value(self, arg_name, a, kw):
    ''' Get the object with index `arg_name`.
    '''
    assert not a
    with Pfx("arg_name=%r", arg_name):
      arg_value = kw[arg_name]
      return arg_value, arg_name

  @pfx_method
  def convert_field(self, value, conversion):
    ''' Convert a value using `self._obj.format_convert_field`.
    '''
    return self.obj.format_convert_field(value, conversion)

  @classmethod
  @pfx_method
  @typechecked
  def format_field(cls, value, format_spec: str):
    ''' Format a value using `value.format_format_field`.

        We actually recognise colon separated chains of formats
        and apply each format to the previously converted value.
        As such, we look for a `.format_format_field` method
        on the value to be converted,
        falling back to `FStr.format_format_field` if the value is a `str`
        otherwise to `FormattableMixin.convert_via_method_or_attr`.
    '''
    format_subspecs = []
    offset = 0
    while offset < len(format_spec):
      if format_spec.startswith(':', offset):
        format_subspec = ''
        offset += 1
      else:
        m_format_subspec = cls.RE_FIELD_EXPR.match(format_spec, offset)
        if m_format_subspec:
          format_subspec = m_format_subspec.group()
        else:
          warning(
              "unrecognised subspec at %d: %r, falling back to split", offset,
              format_spec[offset:]
          )
          format_subspec, *_ = format_spec[offset:].split(':', 1)
        offset += len(format_subspec)
      format_subspecs.append(format_subspec)
    # chain the various subspecifications
    for format_subspec in format_subspecs or ('',):
      with Pfx("value=%r, format_subspec=%r", value, format_subspec):
        try:
          format_format_field = getattr(value, 'format_format_field')
        except AttributeError:
          # fall back to convert_via_method_or_attr
          value, offset = FormatableMixin.convert_via_method_or_attr(
              value, format_subspec
          )
          if offset < len(format_subspec):
            value = cls.get_subfield(value, format_subspec[offset:])
        else:
          # use value.format_format_field(format_subspec)
          format_format_field(format_subspec)
    return FStr(value)

# TODO: add a bunch of string conveniences here: plural, lc etc etc.
class FStr(FormatableMixin, str):
  ''' A `str` subclass with the `FormatableMixin` methods,
      particularly its `__format__`
      which use `str` method names as valid formats.

      It also has a bunch of utility methods which are available
      as `:`*method* in format strings.
  '''

  def basename(self):
    ''' Treat as a filesystem path and return the basename.
    '''
    return Path(self).name

  def dirname(self):
    ''' Treat as a filesystem path and return the dirname.
    '''
    return Path(self).parent

  def f(self):
    ''' Parse `self` as a `float`.
    '''
    return float(self)

  def i(self, base=10):
    ''' Parse `self` as an `int`.
    '''
    return int(self, base=base)

  def lc(self):
    ''' Lowercase using `lc_()`.
    '''
    return lc_(self)

  def path(self):
    ''' Convert to a native filesystem `pathlib.Path`.
    '''
    return Path(self)

  def unix_path(self):
    ''' Convert to a UNIX filesystem `pathlib.Path`.
    '''
    return PureUNIXPath(self)

  def windows_path(self):
    ''' Convert to a Windows filesystem `pathlib.Path`.
    '''
    return PureWindowsPath(self)

if __name__ == '__main__':
  import cs.lex_tests
  cs.lex_tests.selftest(sys.argv)
