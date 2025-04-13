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
from dataclasses import dataclass
from functools import partial
from json import JSONEncoder
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from pprint import pformat
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
from threading import Lock
from typing import Any, Iterable, Tuple, Union

from dateutil.tz import tzlocal
from icontract import require
from typeguard import typechecked

from cs.dateutils import unixtime2datetime, UTC
from cs.deco import fmtdoc, decorator, OBSOLETE, Promotable
from cs.gimmicks import warning
from cs.obj import public_subclasses
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.py.func import funcname
from cs.seq import common_prefix_length, common_suffix_length

__version__ = '20250414'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Topic :: Text Processing",
    ],
    'install_requires': [
        'cs.dateutils',
        'cs.deco',
        'cs.gimmicks',
        'cs.obj',
        'cs.pfx',
        'cs.py.func',
        'cs.seq>=20200914',
        'python-dateutil',
        'icontract',
        'typeguard',
    ],
}

unhexify = binascii.unhexlify  # pylint: disable=c-extension-no-member
hexify = binascii.hexlify  # pylint: disable=c-extension-no-member
if sys.hexversion >= 0x030000:
  _hexify = hexify

  # pylint: disable=function-redefined
  def hexify(bs):
    ''' A flavour of `binascii.hexlify` returning a `str`.
    '''
    return _hexify(bs).decode()

ord_space = ord(' ')

# pylint: disable=too-many-branches,redefined-outer-name
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

def typed_str(o, use_cls=False, use_repr=False, max_length=32):
  ''' Return "type(o).__name__:str(o)" for some object `o`.
      This is available as both `typed_str` and `s`.

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
  # pylint: disable=redefined-outer-name
  o_s = repr(o) if use_repr else str(o)
  if max_length is not None:
    o_s = cropped(o_s, max_length)
  s = "%s:%s" % (type(o) if use_cls else type(o).__name__, o_s)
  return s

# convenience alias
s = typed_str

def typed_repr(o, max_length=None, *, use_cls=False):
  ''' Like `typed_str` but using `repr` instead of `str`.
      This is available as both `typed_repr` and `r`.
  '''
  return typed_str(o, use_cls=use_cls, max_length=max_length, use_repr=True)

# convenience alias
r = typed_repr

def strlist(ary, sep=", "):
  ''' Convert an iterable to strings and join with `sep` (default `', '`).
  '''
  return sep.join([str(a) for a in ary])

# pylint: disable=redefined-outer-name
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
  s = s.replace('"', "&dquot;")
  return '"' + s + '"'

def jsquote(s):
  ''' Quote a string for use in JavaScript.
  '''
  s = s.replace('"', "&dquot;")
  return '"' + s + '"'

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
  if isinstance(whitelist, str):
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
    elif b in whitelist:
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

# pylint: disable=redefined-outer-name
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
  return b''.join(chunks)

# pylint: disable=redefined-outer-name
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

# pylint: disable=redefined-outer-name
def get_white(s, offset=0):
  ''' Scan the string `s` for characters in `string.whitespace`
      starting at `offset` (default `0`).
      Return `(match,new_offset)`.
  '''
  return get_chars(s, offset, whitespace)

# pylint: disable=redefined-outer-name
def skipwhite(s, offset=0):
  ''' Convenience routine for skipping past whitespace;
      returns the offset of the next nonwhitespace character.
  '''
  _, offset = get_white(s, offset=offset)
  return offset

def indent(paragraph, line_indent="  "):
  ''' Return the `paragraph` indented by `line_indent` (default `"  "`).
  '''
  return "\n".join(
      line and line_indent + line for line in paragraph.split("\n")
  )

# TODO: add an optional detab=n parameter?
def stripped_dedent(s, post_indent='', sub_indent=''):
  ''' Slightly smarter dedent which ignores a string's opening indent.

      Algorithm:
      strip the supplied string `s`, pull off the leading line,
      dedent the rest, put back the leading line.

      This is a lot like the `inspect.cleandoc()` function.

      This supports my preferred docstring layout, where the opening
      line of text is on the same line as the opening quote.

      The optional `post_indent` parameter may be used to indent
      the dedented text before return.

      The optional `sub_indent` parameter may be used to indent
      the second and following lines if the dedented text before return.

      Examples:

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
          >>> print(stripped_dedent(func.__doc__, sub_indent='  '))
          Slightly smarter dedent which ignores a string's opening indent.
            Strip the supplied string `s`. Pull off the leading line.
            Dedent the rest. Put back the leading line.
          >>> print(stripped_dedent(func.__doc__, post_indent='  '))
            Slightly smarter dedent which ignores a string's opening indent.
            Strip the supplied string `s`. Pull off the leading line.
            Dedent the rest. Put back the leading line.
          >>> print(stripped_dedent(func.__doc__, post_indent='  ', sub_indent='| '))
            Slightly smarter dedent which ignores a string's opening indent.
            | Strip the supplied string `s`. Pull off the leading line.
            | Dedent the rest. Put back the leading line.
  '''
  s = s.strip()
  lines = s.split('\n')
  if not lines:
    return ''
  line1 = lines.pop(0)
  if not lines:
    return indent(line1, post_indent)
  adjusted = indent(dedent('\n'.join(lines)), sub_indent)
  return indent(line1 + '\n' + adjusted, post_indent)

@require(lambda offset: offset >= 0)
def get_prefix_n(s, prefix, n=None, *, offset=0):
  ''' Strip a leading `prefix` and numeric value `n` from the string `s`
      starting at `offset` (default `0`).
      Return the matched prefix, the numeric value and the new offset.
      Returns `(None,None,offset)` on no match.

      Parameters:
      * `s`: the string to parse
      * `prefix`: the prefix string which must appear at `offset`
        or an object with a `match(str,offset)` method
        such as an `re.Pattern` regexp instance
      * `n`: optional integer value;
        if omitted any value will be accepted, otherwise the numeric
        part must match `n`

      If `prefix` is a `str`, the "matched prefix" return value is `prefix`.
      Otherwise the "matched prefix" return value is the result of
      the `prefix.match(s,offset)` call. The result must also support
      a `.end()` method returning the offset in `s` beyond the match,
      used to locate the following numeric portion.

      Examples:

         >>> import re
         >>> get_prefix_n('s03e01--', 's')
         ('s', 3, 3)
         >>> get_prefix_n('s03e01--', 's', 3)
         ('s', 3, 3)
         >>> get_prefix_n('s03e01--', 's', 4)
         (None, None, 0)
         >>> get_prefix_n('s03e01--', re.compile('[es]',re.I))
         (<re.Match object; span=(0, 1), match='s'>, 3, 3)
         >>> get_prefix_n('s03e01--', re.compile('[es]',re.I), offset=3)
         (<re.Match object; span=(3, 4), match='e'>, 1, 6)
  '''
  no_match = None, None, offset
  if isinstance(prefix, str):
    if s.startswith(prefix, offset):
      matched = prefix
      offset += len(prefix)
    else:
      # no match, return unchanged
      return no_match
  else:
    matched = pfx_call(prefix.match, s, offset)
    if not matched:
      return no_match
    offset = matched.end()
  if offset >= len(s) or not s[offset].isdigit():
    return no_match
  gn, offset = get_decimal_value(s, offset)
  if n is not None and gn != n:
    return no_match
  return matched, gn, offset

NUMERAL_NAMES = {
    'en': {
        # all the single word numbers
        'zero': 0,
        'nought': 0,
        'one': 1,
        'two': 2,
        'three': 3,
        'four': 4,
        'five': 5,
        'six': 6,
        'seven': 7,
        'eight': 8,
        'nine': 9,
        'ten': 10,
        'eleven': 11,
        'twelve': 12,
        'thirteen': 13,
        'fourteen': 14,
        'fifteen': 15,
        'sixteen': 16,
        'seventeen': 17,
        'eighteen': 18,
        'nineteen': 19,
        'twenty': 20,
    },
}

def get_suffix_part(s, *, keywords=('part',), numeral_map=None):
  ''' Strip a trailing "part N" suffix from the string `s`.
      Return the matched suffix and the number part number.
      Retrn `(None,None)` on no match.

      Parameters:
      * `s`: the string
      * `keywords`: an iterable of `str` to match, or a single `str`;
        default `'part'`
      * `numeral_map`: an optional mapping of numeral names to numeric values;
        default `NUMERAL_NAMES['en']`, the English numerals

      Exanmple:

          >>> get_suffix_part('s09e10 - A New World: Part One')
          (': Part One', 1)
  '''
  if isinstance(keywords, str):
    keywords = (keywords,)
  if numeral_map is None:
    numeral_map = NUMERAL_NAMES['en']
  regexp_s = ''.join(
      (
          r'\W+(',
          r'|'.join(keywords),
          r')\s+(?P<numeral>\d+|',
          r'|'.join(numeral_map.keys()),
          r')\s*$',
      )
  )
  regexp = re.compile(regexp_s, re.I)
  m = regexp.search(s)
  if not m:
    return None, None
  numeral = m.group('numeral')
  try:
    part_n = int(numeral)
  except ValueError:
    try:
      part_n = numeral_map[numeral]
    except KeyError:
      try:
        part_n = numeral_map[numeral.lower()]
      except KeyError:
        return None, None
  return m.group(0), part_n

# pylint: disable=redefined-outer-name
def get_nonwhite(s, offset=0):
  ''' Scan the string `s` for characters not in `string.whitespace`
      starting at `offset` (default `0`).
      Return `(match,new_offset)`.
  '''
  return get_other_chars(s, offset=offset, stopchars=whitespace)

# pylint: disable=redefined-outer-name
def get_decimal(s, offset=0):
  ''' Scan the string `s` for decimal characters starting at `offset` (default `0`).
      Return `(dec_string,new_offset)`.
  '''
  return get_chars(s, offset, digits)

# pylint: disable=redefined-outer-name
def get_decimal_value(s, offset=0):
  ''' Scan the string `s` for a decimal value starting at `offset` (default `0`).
      Return `(value,new_offset)`.
  '''
  value_s, offset = get_decimal(s, offset)
  if not value_s:
    raise ValueError("expected decimal value")
  return int(value_s), offset

# pylint: disable=redefined-outer-name
def get_hexadecimal(s, offset=0):
  ''' Scan the string `s` for hexadecimal characters starting at `offset` (default `0`).
      Return `(hex_string,new_offset)`.
  '''
  return get_chars(s, offset, '0123456789abcdefABCDEF')

# pylint: disable=redefined-outer-name
def get_hexadecimal_value(s, offset=0):
  ''' Scan the string `s` for a hexadecimal value starting at `offset` (default `0`).
      Return `(value,new_offset)`.
  '''
  value_s, offset = get_hexadecimal(s, offset)
  if not value_s:
    raise ValueError("expected hexadecimal value")
  return int('0x' + value_s), offset

# pylint: disable=redefined-outer-name
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

# pylint: disable=redefined-outer-name
def is_identifier(s, offset=0, **kw):
  ''' Test if the string `s` is an identifier
      from position `offset` (default `0`) onward.
  '''
  s2, offset2 = get_identifier(s, offset=offset, **kw)
  return s2 and offset2 == len(s)

# pylint: disable=redefined-outer-name
def get_uc_identifier(s, offset=0, number=digits, extras='_'):
  ''' Scan the string `s` for an identifier as for `get_identifier`,
      but require the letters to be uppercase.
  '''
  return get_identifier(
      s, offset=offset, alpha=ascii_uppercase, number=number, extras=extras
  )

def is_uc_identifier(s, offset=0, **kw):
  ''' Test if the string `s` is an uppercase identifier
      from position `offset` (default `0`) onward.
  '''
  s2, offset2 = get_uc_identifier(s, offset=offset, **kw)
  return s2 and offset2 == len(s)

# pylint: disable=redefined-outer-name
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

# pylint: disable=redefined-outer-name
def is_dotted_identifier(s, offset=0, **kw):
  ''' Test if the string `s` is an identifier from position `offset` onward.
  '''
  s2, offset2 = get_dotted_identifier(s, offset=offset, **kw)
  return len(s2) > 0 and offset2 == len(s)

# pylint: disable=redefined-outer-name
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
    special_starts = ''.join(special_starts)
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
  return ''.join(chunks), offset

def slosh_quote(raw_s: str, q: str):
  ''' Quote a string `raw_s` with quote character `q`.
  '''
  return q + raw_s.replace('\\', '\\\\').replace(q, '\\' + q)

# pylint: disable=redefined-outer-name
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

# pylint: disable=redefined-outer-name
def get_qstr_or_identifier(s, offset):
  ''' Parse a double quoted string or an identifier.
  '''
  if s.startswith('"', offset):
    return get_qstr(s, offset, q='"')
  return get_identifier(s, offset)

# pylint: disable=redefined-outer-name
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

# pylint: disable=redefined-outer-name
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
    elif isinstance(getter, str):

      # pylint: disable=redefined-outer-name
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

      # pylint: disable=redefined-outer-name
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

# pylint: disable=redefined-outer-name
def match_tokens(s, offset, getters):
  ''' Wrapper for `get_tokens` which catches `ValueError` exceptions
      and returns `(None,offset)`.
  '''
  try:
    return get_tokens(s, offset, getters)
  except ValueError:
    return None, offset

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
  if any('\n' in p for p in partials):
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

# pylint: disable=redefined-outer-name
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

# pylint: disable=redefined-outer-name
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

# pylint: disable=redefined-outer-name,unsubscriptable-object
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

# pylint: disable=redefined-outer-name
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

# pylint: disable=redefined-outer-name
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

def camelcase(snakecased, first_letter_only=False):
  ''' Convert a snake cased string `snakecased` into camel case.

      Parameters:
      * `snakecased`: the snake case string to convert
      * `first_letter_only`: optional flag (default `False`);
        if true then just ensure that the first character of a word
        is uppercased, otherwise use `str.title`

      Example:

          >>> camelcase('abc_def')
          'abcDef'
          >>> camelcase('ABc_def')
          'abcDef'
          >>> camelcase('abc_dEf')
          'abcDef'
          >>> camelcase('abc_dEf', first_letter_only=True)
          'abcDEf'
  '''
  words = snakecased.split('_')
  for i, word in enumerate(words):
    if not word:
      continue
    if first_letter_only:
      word = word[0].upper() + word[1:]
    else:
      word = word.title()
    if i == 0:
      word = word[0].lower() + word[1:]
    words[i] = word
  return ''.join(words)

def snakecase(camelcased):
  ''' Convert a camel cased string `camelcased` into snake case.

      Parameters:
      * `cameelcased`: the cameel case string to convert
      * `first_letter_only`: optional flag (default `False`);
        if true then just ensure that the first character of a word
        is uppercased, otherwise use `str.title`

      Example:

          >>> snakecase('abcDef')
          'abc_def'
          >>> snakecase('abcDEf')
          'abc_def'
          >>> snakecase('AbcDef')
          'abc_def'
  '''
  strs = []
  was_lower = False
  for _, c in enumerate(camelcased):
    if c.isupper():
      c = c.lower()
      if was_lower:
        # boundary
        was_lower = False
        strs.append('_')
    else:
      was_lower = True
    strs.append(c)
  return ''.join(strs)

@OBSOLETE('cs.fs.RemotePath.from_str')
def split_remote_path(remotepath: str) -> Tuple[Union[str, None], str]:
  ''' Split a path with an optional leading `[user@]rhost:` prefix
      into the prefix and the remaining path.
      `None` is returned for the prefix is there is none.
      This is useful for things like `rsync` targets etc.

      OBSOLETE, use `cs.fs.RemotePath.from_str` instead.
  '''
  from cs.fs import RemotePath
  return RemotePath.from_str(remotepath)

def tabulate(*rows, sep='  '):
  r''' A generator yielding lines of values from `rows` aligned in columns.

      Each row in rows is a list of strings. Non-`str` objects are
      promoted to `str` via `pprint.pformat`. If the strings contain
      newlines they will be split into subrows.

      Example:

          >>> for row in tabulate(
          ...     ['one col'],
          ...     ['three', 'column', 'row'],
          ...     ['row3', 'multi\nline\ntext', 'goes\nhere', 'and\nhere'],
          ...     ['two', 'cols'],
          ... ):
          ...     print(row)
          ...
          one col
          three    column  row
          row3     multi   goes  and
                   line    here  here
                   text
          two      cols
          >>>
  '''
  if not rows:
    # avoids max of empty list
    return
  # promote all table cells to str via pformat
  rows = [
      [
          (cell if isinstance(cell, str) else pformat(cell, compact=True))
          for cell in row
      ]
      for row in rows
  ]
  # pad short rows with empty columns
  max_cols = max(map(len, rows))
  for row in rows:
    if len(row) < max_cols:
      row += [''] * (max_cols - len(row))
  # break rows on newlines
  srows = []
  for row in rows:
    if all("\n" not in cell for cell in row):
      # no multiline row cells
      srows.append(row)
    else:
      # split multiline cells int columns, pad columns to match
      cols = [
          [subcell.rstrip() for subcell in cell.split("\n")] for cell in row
      ]
      max_height = max(map(len, cols))
      for subrow in range(max_height):
        srows.append(
            [col[subrow] if subrow < len(col) else '' for col in cols]
        )
    rows = srows
  col_widths = [
      max(map(len, (row[c]
                    for row in rows)))
      for c in range(max(map(len, rows)))
  ]
  for row in rows:
    yield sep.join(
        f'{col_val:<{col_widths[c]}}' for c, col_val in enumerate(row)
    ).rstrip()

def printt(
    *table, file=None, flush=False, indent='', print_func=None, **tabulate_kw
):
  ''' A wrapper for `tabulate()` to print the results.
      Each positional argument is a table row.

      Parameters:
      * `file`: optional output file, passed to `print_func`
      * `flush`: optional flush flag, passed to `print_func`
      * `indent`: optional leading indent for the output lines
      * `print_func`: optional `print()` function, default `builtins.print`
      Other keyword arguments are passed to `tabulate()`.
  '''
  if print_func is None:
    from builtins import print as print_func
  for line in tabulate(*table, **tabulate_kw):
    print_func(indent + line, file=file, flush=flush)

# pylint: disable=redefined-outer-name
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
def format_as(
    format_s: str,
    format_mapping,
    formatter=None,
    error_sep=None,
    strict=None,
):
  ''' Format the string `format_s` using `Formatter.vformat`,
      return the formatted result.
      This is a wrapper for `str.format_map`
      which raises a more informative `FormatAsError` exception on failure.

      Parameters:
      * `format_s`: the format string to use as the template
      * `format_mapping`: the mapping of available replacement fields
      * `formatter`: an optional `string.Formatter`-like instance
        with a `.vformat(format_string,args,kwargs)` method,
        usually a subclass of `string.Formatter`;
        if not specified then `FormatableFormatter` is used
      * `error_sep`: optional separator for the multipart error message,
        default from `FormatAsError.DEFAULT_SEPARATOR`:
        `'{FormatAsError.DEFAULT_SEPARATOR}'`
      * `strict`: optional flag (default `False`)
        indicating that an unresolveable field should raise a
        `KeyError` instead of inserting a placeholder
  '''
  if formatter is None:
    formatter = FormatableFormatter(format_mapping)
  if strict is None:
    strict = formatter.format_mode.strict
  with formatter.format_mode(strict=strict):
    try:
      formatted = formatter.vformat(format_s, (), format_mapping)
    except KeyError as e:
      # pylint: disable=raise-missing-from
      raise FormatAsError(
          e.args[0], format_s, format_mapping, error_sep=error_sep
      )
    return formatted

_format_as = format_as  # for reuse in the format_as method below

def format_attribute(method):
  ''' A decorator to mark a method as available as a format method.
      Requires the enclosing class to be decorated with `@has_format_attributes`.

      For example,
      the `FormatableMixin.json` method is defined like this:

          @format_attribute
          def json(self):
              return self.FORMAT_JSON_ENCODER.encode(self)

      which allows a `FormatableMixin` subclass instance
      to be used in a format string like this:

          {instance:json}

      to insert a JSON transcription of the instance.

      It is recommended that methods marked with `@format_attribute`
      have no side effects and do not modify state,
      as they are intended for use in ad hoc format strings
      supplied by an end user.
  '''
  method.is_format_attribute = True
  return method

@decorator
def has_format_attributes(cls, inherit=()):
  ''' Class decorator to walk this class for direct methods
      marked as for use in format strings
      and to include them in `cls.format_attributes()`.

      Methods are normally marked with the `@format_attribute` decorator.

      If `inherit` is true the base format attributes will be
      obtained from other classes:
      * `inherit` is `True`: use `cls.__mro__`
      * `inherit` is a class: use that class
      * otherwise assume `inherit` is an iterable of classes
      For each class `otherclass`, update the initial attribute
      mapping from `otherclass.get_format_attributes()`.
  '''
  attributes = cls.get_format_attributes()
  if inherit:
    if inherit is True:
      classes = cls.__mro__
    elif isinstance(inherit, type):
      classes = (inherit,)
    else:
      classes = inherit
    for superclass in classes:
      try:
        super_attributes = superclass.get_format_attributes()
      except AttributeError:
        pass
      else:
        attributes.update(super_attributes)
  for attr in dir(cls):
    try:
      attribute = getattr(cls, attr)
    except AttributeError:
      pass
    else:
      if getattr(attribute, 'is_format_attribute', False):
        attributes[attr] = attribute
  return cls

class FormatableFormatter(Formatter):
  ''' A `string.Formatter` subclass interacting with objects
      which inherit from `FormatableMixin`.
  '''

  FORMAT_RE_LITERAL_TEXT = re.compile(r'([^{]+|{{)*')
  FORMAT_RE_IDENTIFIER_s = r'[a-z_][a-z_0-9]*'
  FORMAT_RE_ARG_NAME_s = rf'({FORMAT_RE_IDENTIFIER_s}|\d+(\.\d+)?[a-z]+)'
  FORMAT_RE_ATTRIBUTE_NAME_s = rf'\.{FORMAT_RE_IDENTIFIER_s}'
  FORMAT_RE_ELEMENT_INDEX_s = r'[^]]*'
  FORMAT_RE_FIELD_EXPR_s = (
      rf'{FORMAT_RE_ARG_NAME_s}'
      rf'({FORMAT_RE_ATTRIBUTE_NAME_s}|\[{FORMAT_RE_ELEMENT_INDEX_s}\]'
      rf')*'
  )
  FORMAT_RE_FIELD_EXPR = re.compile(FORMAT_RE_FIELD_EXPR_s, re.I)
  FORMAT_RE_FIELD = re.compile(
      (
          r'{' + rf'(?P<arg_name>{FORMAT_RE_FIELD_EXPR_s})?' +
          r'(!(?P<conversion>[^:}]*))?' + r'(:(?P<format_spec>[^}]*))?' + r'}'
      ), re.I
  )

  @property
  def format_mode(self):
    ''' Thread local state object.

        Attributes:
        * `strict`: initially `False`; raise a `KeyError` for
          unresolveable field names
    '''
    try:
      lock = self.__dict__['_lock']
    except KeyError:
      lock = self.__dict__['_lock'] = Lock()
    with lock:
      try:
        mode = self.__dict__['format_mode']
      except KeyError:
        # pylint: disable=import-outside-toplevel
        from cs.threads import ThreadState
        mode = self.__dict__['format_mode'] = ThreadState(strict=False)
    return mode

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
        m_literal = cls.FORMAT_RE_LITERAL_TEXT.match(format_string, offset)
        literal = m_literal.group()
        offset = m_literal.end()
        if offset == len(format_string):
          # nothing after the literal text
          if literal:
            yield literal, None, None, None
          return
        m_field = cls.FORMAT_RE_FIELD.match(format_string, offset)
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

  @staticmethod
  def get_arg_name(field_name):
    ''' Default initial arg_name is an identifier.

        Returns `(prefix,offset)`, and `('',0)` if there is no arg_name.
    '''
    return get_identifier(field_name)

  # pylint: disable=arguments-differ
  @pfx_method
  def get_field(self, field_name, args, kwargs):
    ''' Get the object referenced by the field text `field_name`.
        Raises `KeyError` for an unknown `field_name`.
    '''
    assert not args
    with Pfx("field_name=%r: kwargs=%r", field_name, kwargs):
      arg_name, offset = self.get_arg_name(field_name)
      arg_value, _ = self.get_value(arg_name, args, kwargs)
      # resolve the rest of the field
      subfield = self.get_subfield(arg_value, field_name[offset:])
      return subfield, field_name

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
    if subfield_text[0] in '.[':
      subfield_fmt = f'{{value{subfield_text}}}'
      subfield_map = {'value': value}
      with Pfx("%r.format_map(%r)", subfield_fmt, subfield_map):
        value = subfield_fmt.format_map(subfield_map)
    else:
      # use the subfield_text after the colon
      fmt = f'{{value:{subfield_text}}}'
      value = fmt.format(value=value)
    return value

  # pylint: disable=arguments-differ,arguments-renamed
  @pfx_method
  def get_value(self, arg_name, args, kwargs):
    ''' Get the object with index `arg_name`.

        This default implementation returns `(kwargs[arg_name],arg_name)`.
    '''
    assert not args
    return kwargs[arg_name], arg_name

  @classmethod
  def get_format_subspecs(cls, format_spec):
    ''' Parse a `format_spec` as a sequence of colon separated components,
        return a list of the components.
    '''
    subspecs = []
    offset = 0
    while offset < len(format_spec):
      if format_spec.startswith(':', offset):
        # an empty spec
        subspec = ''
        offset += 1
      else:
        # match a FORMAT_RE_FIELD_EXPR
        m_subspec = cls.FORMAT_RE_FIELD_EXPR.match(format_spec, offset)
        if m_subspec:
          subspec = m_subspec.group()
        else:
          warning(
              "unrecognised subspec at %d: %r, falling back to split", offset,
              format_spec[offset:]
          )
          subspec, *_ = format_spec[offset:].split(':', 1)
        offset += len(subspec)
      subspecs.append(subspec)
    return subspecs

  @classmethod
  @pfx_method
  @typechecked
  def format_field(cls, value, format_spec: str):
    ''' Format a value using `value.format_format_field`,
        returning an `FStr`
        (a `str` subclass with additional `format_spec` features).

        We actually recognise colon separated chains of formats
        and apply each format to the previously converted value.
        The final result is promoted to an `FStr` before return.
    '''
    # parse the format_spec into multiple subspecs
    format_subspecs = cls.get_format_subspecs(format_spec) or []
    for format_subspec in format_subspecs:
      with Pfx("subspec %r", format_subspec):
        assert isinstance(format_subspec, str)
        assert len(format_subspec) > 0
        with Pfx("value=%r, format_subspec=%r", value, format_subspec):
          # promote bare str to FStr
          if value is None or type(value) is str:  # pylint: disable=unidiomatic-typecheck
            value = FStr(value)
          if format_subspec[0].isalpha():
            try:
              value.convert_via_method_or_attr  # noqa
            except AttributeError:
              # promote to something with convert_via_method_or_attr
              if isinstance(value, str):
                value = FStr(value)
              else:
                value = pfx_call(format, value, format_subspec)
            value, offset = value.convert_via_method_or_attr(
                value, format_subspec
            )
            if offset < len(format_subspec):
              subspec_tail = format_subspec[offset:]
              value = cls.get_subfield(value, subspec_tail)
          else:
            value = format(value, format_subspec)
    return FStr(value)

@has_format_attributes
class FormatableMixin(FormatableFormatter):  # pylint: disable=too-few-public-methods
  ''' A subclass of `FormatableFormatter` which  provides 2 features:
      - a `__format__` method which parses the `format_spec` string
        into multiple colon separated terms whose results chain
      - a `format_as` method which formats a format string using `str.format_map`
        with a suitable mapping derived from the instance
        via its `format_kwargs` method
        (whose default is to return the instance itself)

      The `format_as` method is like an inside out `str.format` or
      `object.__format__` method.

      The `str.format` method is designed for formatting a string
      from a variety of other objects supplied in the keyword arguments.

      The `object.__format__` method is for filling out a single `str.format`
      replacement field from a single object.

      By contrast, `format_as` is designed to fill out an entire format
      string from the current object.

      For example, the `cs.tagset.TagSetMixin` class
      uses `FormatableMixin` to provide a `format_as` method
      whose replacement fields are derived from the tags in the tag set.

      Subclasses wanting to provide additional `format_spec` terms
      should:
      - override `FormatableFormatter.format_field1` to implement
        terms with no colons, letting `format_field` do the split into terms
      - override `FormatableFormatter.get_format_subspecs` to implement
        the parse of `format_spec` into a sequence of terms.
        This might recognise a special additional syntax
        and quietly fall back to `super().get_format_subspecs`
        if that is not present.
  '''

  FORMAT_JSON_ENCODER = JSONEncoder(separators=(',', ':'))

  # pylint: disable=invalid-format-returned
  def __format__(self, format_spec):
    ''' Format `self` according to `format_spec`.

        This implementation calls `self.format_field`.
        As such, a `format_spec` is considered
        a sequence of colon separated terms.

        Classes wanting to implement additional format string syntaxes
        should either:
        - override `FormatableFormatter.format_field1` to implement
          terms with no colons, letting `format_field1` do the split into terms
        - override `FormatableFormatter.get_format_subspecs` to implement
          the term parse.

        The default implementation of `__format1__` just calls `super().__format__`.
        Implementations providing specialised formats
        should implement them in `__format1__`
        with fallback to `super().__format1__`.
    '''
    return self.format_field(self, format_spec)

  @classmethod
  def get_format_attributes(cls):
    ''' Return the mapping of format attributes.
    '''
    try:
      attributes = cls.__dict__['_format_attributes']
    except KeyError:
      cls._format_attributes = attributes = {}
    return attributes

  def get_format_attribute(self, attr):
    ''' Return a mapping of permitted methods to functions of an instance.
        This is used to whitelist allowed `:`*name* method formats
        to prevent scenarios like little Bobby Tables calling `delete()`.
    '''
    # this shuffle is because cls.__dict__ is a proxy, not a dict
    cls = type(self)
    attributes = cls.get_format_attributes()
    if attr in attributes:
      return getattr(self, attr)
    raise AttributeError(
        "disallowed attribute %r: not in %s._format_attributes" %
        (attr, cls.__name__)
    )

  ##@staticmethod
  def convert_field(self, value, conversion):
    ''' The default converter for fields calls `Formatter.convert_field`.
    '''
    if conversion == '':
      warning(
          "%s.convert_field(%s, conversion=%r): turned conversion into None",
          type(self).__name__, typed_str(value, use_repr=True), conversion
      )
      conversion = None
    return super().convert_field(value, conversion)

  @pfx_method
  def convert_via_method_or_attr(self, value, format_spec):
    ''' Apply a method or attribute name based conversion to `value`
        where `format_spec` starts with a method name
        applicable to `value`.
        Return `(converted,offset)`
        being the converted value and the offset after the method name.

        Note that if there is not a leading identifier on `format_spec`
        then `value` is returned unchanged with `offset=0`.

        The methods/attributes are looked up in the mapping
        returned by `.format_attributes()` which represents allowed methods
        (broadly, one should not allow methods which modify any state).

        If this returns a callable, it is called to obtain the converted value
        otherwise it is used as is.

        As a final tweak,
        if `value.get_format_attribute()` raises an `AttributeError`
        (the attribute is not an allowed attribute)
        or calling the attribute raises a `TypeError`
        (the `value` isn't suitable)
        and the `value` is not an instance of `FStr`,
        convert it to an `FStr` and try again.
        This provides the common utility methods on other types.

        The motivating example was a `PurePosixPath`,
        which does not JSON transcribe;
        this tweak supports both
        `posixpath:basename` via the pathlib stuff
        and `posixpath:json` via `FStr`
        even though a `PurePosixPath` does not subclass `FStr`.
    '''
    try:
      attr, offset = get_identifier(format_spec)
      if not attr:
        # no leading method/attribute name, return unchanged
        return value, 0
      try:
        attribute = value.get_format_attribute(attr)
      except AttributeError as e:
        raise TypeError(
            "convert_via_method_or_attr(%s,%r): %s" %
            (typed_repr(value), format_spec, e)
        ) from e
      if callable(attribute):
        converted = attribute()
      else:
        converted = attribute
      return converted, offset
    except TypeError:
      if not isinstance(value, FStr):
        with Pfx("fall back to FStr(value=%s).convert_via_method_or_attr"):
          return self.convert_via_method_or_attr(FStr(value), format_spec)
      raise

  def format_as(self, format_s, error_sep=None, strict=None, **control_kw):
    ''' Return the string `format_s` formatted using the mapping
        returned by `self.format_kwargs(**control_kw)`.

        If a class using the mixin has no `format_kwargs(**control_kw)` method
        to provide a mapping for `str.format_map`
        then the instance itself is used as the mapping.
    '''
    get_format_mapping = getattr(self, 'format_kwargs', None)
    if get_format_mapping is None:
      if control_kw:
        # pylint: disable=raise-missing-from
        raise ValueError(
            "no .format_kwargs() method, but control_kw=%r" % (control_kw,)
        )
      format_mapping = self
    else:
      format_mapping = get_format_mapping(**control_kw)  # pylint:disable=not-callable
    if strict is None:
      strict = self.format_mode.strict
    with self.format_mode(strict=strict):
      return pfx_call(
          _format_as,
          format_s,
          format_mapping,
          formatter=self,
          error_sep=error_sep,
      )

  # Utility methods for formats.
  @format_attribute
  def json(self):
    ''' The value transcribed as compact JSON.
    '''
    return self.FORMAT_JSON_ENCODER.encode(self)

@has_format_attributes
class FStr(FormatableMixin, str):
  ''' A `str` subclass with the `FormatableMixin` methods,
      particularly its `__format__` method
      which uses `str` method names as valid formats.

      It also has a bunch of utility methods which are available
      as `:`*method* in format strings.
  '''

  # str is immutable: prefill with all public class attributes
  _format_attributes = {
      attr: getattr(str, attr)
      for attr in dir(str)
      if attr[0].isalpha()
  }

  @format_attribute
  def basename(self):
    ''' Treat as a filesystem path and return the basename.
    '''
    return Path(self).name

  @format_attribute
  def dirname(self):
    ''' Treat as a filesystem path and return the dirname.
    '''
    return Path(self).parent

  @format_attribute
  def f(self):
    ''' Parse `self` as a `float`.
    '''
    return float(self)

  @format_attribute
  def i(self, base=10):
    ''' Parse `self` as an `int`.
    '''
    return int(self, base=base)

  @format_attribute
  def lc(self):
    ''' Lowercase using `lc_()`.
    '''
    return lc_(self)

  @format_attribute
  def path(self):
    ''' Convert to a native filesystem `pathlib.Path`.
    '''
    return Path(self)

  @format_attribute
  def posix_path(self):
    ''' Convert to a Posix filesystem `pathlib.Path`.
    '''
    return PurePosixPath(self)

  @format_attribute
  def windows_path(self):
    ''' Convert to a Windows filesystem `pathlib.Path`.
    '''
    return PureWindowsPath(self)

class FNumericMixin(FormatableMixin):
  ''' A `FormatableMixin` subclass.
  '''

  @format_attribute
  def utctime(self):
    ''' Treat this as a UNIX timestamp and return a UTC `datetime`.
    '''
    return unixtime2datetime(self, tz=UTC)

  @format_attribute
  def localtime(self):
    ''' Treat this as a UNIX timestamp and return a localtime `datetime`.
    '''
    return unixtime2datetime(self, tz=tzlocal())

@has_format_attributes
class FFloat(FNumericMixin, float):
  ''' Formattable `float`.
  '''

@has_format_attributes
class FInt(FNumericMixin, int):
  ''' Formattable `int`.
  '''

@dataclass
class BaseToken(Promotable):
  ''' A mixin for token dataclasses.

      Presently I use this in `cs.app.tagger.rules` and `cs.app.pilfer.parse`.
  '''

  # additional token classes to consider during the parse
  EXTRAS = ()

  source_text: str
  offset: int
  end_offset: int

  def __str__(self):
    return self.matched_text

  @property
  def matched_text(self):
    ''' The text from `self.source_text` which matches this token.
    '''
    return self.source_text[self.offset:self.end_offset]

  @classmethod
  def token_classes(cls):
    ''' Return the `baseToken` subclasses to consider when parsing a token stream.
    '''
    return public_subclasses(cls, extras=cls.EXTRAS)

  @classmethod
  @pfx_method
  def parse(cls,
            text: str,
            offset: int = 0,
            *,
            skip=False) -> Tuple["BaseToken", int]:
    ''' Parse a token from `test` at `offset` (default `0`).
        Return a `BaseToken` subclass instance.
        Raise `SyntaxError` if no subclass parses it.
        Raise `EOFError` if at the end of the `text`,
        checked after any whitespace if `skip` is true.
        The returned token's `.end_offset` is the next parse point.

        This base class method attempts the `.parse` method of all
        the public subclasses.

        Parameters:
        * `text`: the text being parsed
        * `offset`: the offset within the `text` of the the parse cursor
        * `skip`: if true (default `False`), skip any leading
          whitespace before matching
    '''
    if skip:
      offset = skipwhite(text, offset)
    if offset >= len(text):
      raise EOFError(f'end of text encountered at offset {offset}')
    token_classes = cls.token_classes()
    if not token_classes:
      raise RuntimeError("no token classes")
    for subcls in token_classes:
      if subcls is cls:
        continue
      try:
        return subcls.parse(text, offset=offset)
      except SyntaxError:
        pass
    raise SyntaxError(
        'no subclass.parse succeeded,'
        f'tried {",".join(subcls.__name__ for subcls in token_classes)}'
    )

  @classmethod
  @pfx_method
  @typechecked
  def from_str(cls, text: str) -> "BaseToken":
    ''' Parse `test` as a token of type `cls`, return the token.
        Raises `SyntaxError` on a parse failure.
        This is a wrapper for the `parse` class method.
    '''
    token = cls.parse(text)
    if token.end_offset != len(text):
      raise SyntaxError(
          f'unparsed text at offset {token.end_offset}:'
          f' {text[token.end_offset:]!r}'
      )
    return token

  @classmethod
  def scan(cls,
           text: str,
           offset: int = 0,
           *,
           skip=True) -> Iterable["BaseToken"]:
    ''' Scan `text`, parsing tokens using `BaseToken.parse` and yielding them.
        Parameters are as for `BaseToken.parse` except as follows:
        - encountering end of text end the iteration instead of raising `EOFError`
        - `skip` defaults to `True` to allow whitespace between tokens
    '''
    while True:
      try:
        token = cls.parse(text, offset, skip=skip)
      except EOFError:
        break
      yield token
      offset = token.end_offset

class CoreTokens(BaseToken):
  ''' A mixin for token dataclasses whose subclasses include `Identifier`,
      'NumericValue` and `QuotedString`.
  '''

@dataclass
class Identifier(CoreTokens, BaseToken):
  ''' A dotted identifier.
  '''

  name: str

  @classmethod
  def parse(cls,
            text: str,
            offset: int = 0,
            *,
            skip=False) -> Tuple[str, CoreTokens, int]:
    ''' Parse a dotted identifier from `test`.
    '''
    if skip:
      offset = skipwhite(text, offset)
    name, end_offset = get_dotted_identifier(text, offset)
    if not name:
      raise SyntaxError(
          f'{offset}: expected dotted identifier, found {text[offset:offset+3]!r}...'
      )
    return cls(
        source_text=text, offset=offset, end_offset=end_offset, name=name
    )

class _LiteralValue(CoreTokens):
  value: Any

@dataclass
class NumericValue(_LiteralValue, BaseToken):
  ''' An `int` or `float` literal.
  '''

  value: Union[int, float]

  # anything this matches should be a valid Python int/float
  _token_re = re.compile(r'[-+]?\d+(\.\d*([eE]-?\d+)?)?')

  def __str__(self):
    return str(self.value)

  @classmethod
  def parse(cls, text: str, offset: int = 0, *, skip=False) -> "NumericValue":
    ''' Parse a Python style `int` or `float`.
    '''
    if skip:
      offset = skipwhite(text, offset)
    start_offset = skipwhite(text, offset)
    m = cls._token_re.match(text, start_offset)
    if not m:
      raise SyntaxError(
          f'{start_offset}: expected int or float, found {text[start_offset:start_offset+16]!r}'
      )
    try:
      value = int(m.group())
    except ValueError:
      value = float(m.group())
    return cls(
        source_text=text, offset=offset, end_offset=m.end(), value=value
    )

@dataclass
class QuotedString(_LiteralValue, BaseToken):
  ''' A double quoted string.
  '''

  value: str
  quote: str = '"'

  def __str__(self):
    return slosh_quote(self.value, self.quote)

  @classmethod
  def parse(cls, text: str, offset: int = 0, *, skip=False) -> "QuotedString":
    ''' Parse a double quoted string from `text`.
    '''
    if skip:
      offset = skipwhite(text, offset)
    if not text.startswith('"', offset):
      raise SyntaxError(
          f'{offset}: expected ", found {text[offset:offset+1]!r}'
      )
    q = text[offset]
    value, end_offset = get_qstr(text, offset)
    return cls(
        source_text=text,
        offset=offset,
        end_offset=end_offset,
        value=value,
        quote=q,
    )

if __name__ == '__main__':
  import cs.lex_tests
  cs.lex_tests.selftest(sys.argv)
