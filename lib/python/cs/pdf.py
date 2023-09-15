#!/usr/bin/env python3

''' Simple PDF parser/decoder with no rendering support at all.

    This is based on the PDF 32000-1:2008 document:
    https://web.archive.org/web/20081203002256/https://www.adobe.com/devnet/acrobat/pdfs/PDF32000_2008.pdf
'''

import binascii
from dataclasses import dataclass
import re
from typing import Any, Callable

from cs.buffer import CornuCopyBuffer
from cs.deco import promote
from cs.logutils import setup_logging, warning
from cs.pfx import Pfx
from cs.lex import r

from cs.debug import trace
from cs.x import X

# Binary regexps for PDF tokens.
# These consist of a pair:
# a regexp FOO_re_bs to match the entire token
# and a regexp FOO_LEADIN_re_bs to match the required starting bytes.
EOL_re_bs = br'(\r?\n|\r(?!\n))'
WS_re_bs = br'[\x00\x09\x0a\x0c\x0d\x20]'
WSS_re_bs = WS_re_bs + b'+'
ARRAY_OPEN_re_bs = br'\['
ARRAY_OPEN_LEADIN_re_bs = br'\['
ARRAY_CLOSE_re_bs = br'\]'
ARRAY_CLOSE_LEADIN_re_bs = br'\]'
DICT_OPEN_re_bs = br'<<'
DICT_OPEN_LEADIN_re_bs = br'<<'
DICT_CLOSE_re_bs = br'>>'
DICT_CLOSE_LEADIN_re_bs = br'>>'
FLOAT_re_bs = br'[-+]?(\.\d+|\d+\.\d*)'
FLOAT_LEADIN_re_bs = br'[-+.\d]'
INT_re_bs = br'[-+]?\d+(?!\.)'
INT_LEADIN_re_bs = br'[-+\d]'
# I have not yet found a formall definition of a keyword :-(
KEYWORD_re_bs = br'[a-zA-Z][_\w]*'
KEYWORD_LEADIN_re_bs = br'[a-zA-Z]'
NAME_HASHHEX_re_bs = br'#[0-9a-fA-F][0-9a-fA-F]'
NAME_NONHASH_re_bs = br'[^/\[\]<>#\x00\x09\x0a\x0c\x0d\x20]'
NAME_re_bs = b''.join(
    (
        br'/(',
        br'(',
        NAME_HASHHEX_re_bs,
        br'|',
        NAME_NONHASH_re_bs,
        br')*',
        br')',
    )
)
NAME_LEADIN_re_bs = br'/'
COMMENT_re_bs = br'%[^\r\n]*' + EOL_re_bs
COMMENT_LEADIN_re_bs = br'%'
HEXSTRING_re_bs = br'<[0-9a-zA-F]+>'
HEXSTRING_LEADIN_re_bs = br'<'
STRING_NONSLOSH_re_bs = br'[^\\\()\r\n]'
# STRING slosh escape
STRING_SLOSHED_re_bs = br'\\([nrtbf()\\]|[0-7]{3})'
SIMPLE_STRING_re_bs = b''.join(
    (
        br'\(',
        br'(',
        STRING_NONSLOSH_re_bs,
        br'|',
        STRING_SLOSHED_re_bs,
        br')*',
        br'\)',
    )
)
SIMPLE_STRING_LEADIN_re_bs = br'\('
## streams
STREAM_LEADIN_re_bs = br'stream\r?\n'

@dataclass
class Reaction:
  ''' A regular expression based token recogniser.

      Fields:
      - `re`: an `re.Pattern` matching the whole token
      - `re_leading`: an `re.Pattern` matching the leading byte
      - `tokenise`: a function of an `re.Match` object matching a token,
        returning the token's native value, eg a `str`, `int` etc.
      - `min_len`: the minimum length of this token, default `1`
  '''

  re: re.Pattern
  re_leadin: re.Pattern
  tokenise: Callable[re.Match, Any] = lambda m: m.group()
  min_len: int = 1
  buf_inc: int = 16

  def match(self, buf: CornuCopyBuffer, previous_object=None):
    ''' Match a token at the start of `buf`.
        Return the result of `self.tokenise(m)`
        where `m` is the `re.Match` object.
        Returns `None` if the leadin pattern does not match
        or the pattern cannot be matched before EOF.

        The optional parameter `preious_object` may be supplied
        as the previous semantic object from the PDF token stream.
        This intended is to support parsing `stream` objects
        which require access to a preceeding dictionary
        in order to know the payload length.

        This requires a match followed by at least one byte post
        the token or followed by EOF. The buffer is extended until
        this is achieved.  This implies that the tokens are
        distinguishable by their leadin patterns.
    '''
    # probe for the leading regexp
    m = self.re_leadin.match(buf.peek(self.min_len, short_ok=True))
    if m is None:
      # no leadin, do no further matching
      return None
    return self.match_re(buf, self.re)

  def match_re(
      self, buf: CornuCopyBuffer, R: re.Pattern = None, tokenise=None
  ):
    if R is None:
      R = self.re
    if tokenise is None:
      tokenise = self.tokenise
    while True:
      buflen = len(buf)
      m = R.match(buf.peek(buflen))
      if m is not None:
        # a match, which might be incomplete (eg an int)
        if m.end() < buflen:
          # must be complete because the match did not consume the buffer
          buf.take(m.end())
          return tokenise(m)
      buf.extend(self.buf_inc, short_ok=True)
      if buflen == len(buf):
        # EOF, no more data to match
        if m is not None:
          # must be a EOF, return the match
          buf.take(m.end())
          return tokenise(m)
        # no match possible
        return None

@dataclass
class StreamReaction(Reaction):

  # the end of stream marker
  ENDSTREAM_bre = re.compile(EOL_re_bs + br'endstream(?:\W)')

  def match(self, buf: CornuCopyBuffer, previous_object=None):
    # probe for the leading regexp
    m = self.re_leadin.match(buf.peek(self.min_len, short_ok=True))
    if m is None:
      # no leadin, do no further matching
      return None
    # advance past the EOL
    buf.skip(m.end())
    assert isinstance(previous_object, DictObject)
    length = previous_object[b'Length']
    assert isinstance(length, int)
    assert length >= 0
    payload = buf.take(length)
    endtoken = self.match_re(
        buf, self.ENDSTREAM_bre, tokenise=lambda m: EndStream(m.group(1))
    )
    assert isinstance(endtoken, EndStream)
    return Stream(previous_object, payload)

class _Token(bytes):

  def __repr__(self):
    return f'{self.__class__.__name__}:{bytes(self)!r}'

class ArrayOpen(_Token):
  ''' A `bytes` instance representing a PDF array open.
  '''

class ArrayClose(_Token):
  ''' A `bytes` instance representing a PDF array close.
  '''

class Comment(_Token):
  ''' A `bytes` instance representing a PDF comment.
  '''

class DictOpen(_Token):
  ''' A `bytes` instance representing a PDF dictionary open.
  '''

class DictClose(_Token):
  ''' A `bytes` instance representing a PDF dictionary close.
  '''

class EndStream(_Token):
  ''' A `bytes` instance representing a PDF endstream.
  '''

class HexString(_Token):
  ''' A `bytes` instance representing a PDF hex string.
  '''

  def __bytes__(self):
    return b'<' + binascii.hexlify(self) + b'>'

class Keyword(_Token):
  ''' A `bytes` instance representing a PDF keyword.
  '''

class Name(_Token):
  ''' A `bytes` instance representing a PDF name.
  '''

  def __bytes__(self):
    return br'/' + self

class ArrayObject(list):
  ''' A PDF array.
  '''

  def __bytes__(self):
    return b''.join(
        b'[ ',
        *map(bytes, self),
        b' ]',
    )

class DictObject(dict):
  ''' A PDF dictionary.
  '''

  def __bytes__(self):
    return b''.join(
        b'<<\r\n',
        *chain(
            (b'  ', bytes(k), b'\rn\    ', bytes(v), b'\r\n')
            for k, v in sorted(self.items())
        ),
        b'>>',
    )

@dataclass
class Stream:
  ''' A PDF Stream.
  '''

  context_dict: DictObject
  payload: bytes

  def __bytes__(self):
    return (
        bytes(self.context_dict) + b'\r\nstream\r\n' + self.payload +
        b'\r\nendstream\r\n'
    )

class WhiteSpace(bytes):
  ''' A `bytes` instance representing PDF whitespace.
  '''

# regular expressions for tokens in recognition order
# for example the FLOAT_re must preceed the INT_re
tokenisers = [
    Reaction(
        re.compile(COMMENT_re_bs),
        re.compile(COMMENT_LEADIN_re_bs),
        lambda m: Comment(m.group(0)),
    ),
    StreamReaction(
        None,
        re.compile(STREAM_LEADIN_re_bs),
        None,
        8,
    ),
    Reaction(
        re.compile(KEYWORD_re_bs),
        re.compile(KEYWORD_LEADIN_re_bs),
        lambda m: Keyword(decode_pdf_name(m.group(0))),
    ),
    Reaction(
        re.compile(NAME_re_bs),
        re.compile(NAME_LEADIN_re_bs),
        lambda m: Name(decode_pdf_name(m.group(0)[1:])),
    ),
    Reaction(
        re.compile(WSS_re_bs),
        re.compile(WS_re_bs),
        lambda m: WhiteSpace(m.group(0)),
    ),
    Reaction(
        re.compile(ARRAY_OPEN_re_bs),
        re.compile(ARRAY_OPEN_LEADIN_re_bs),
        lambda m: ArrayOpen(m.group()),
    ),
    Reaction(
        re.compile(ARRAY_CLOSE_re_bs),
        re.compile(ARRAY_CLOSE_LEADIN_re_bs),
        lambda m: ArrayClose(m.group()),
    ),
    Reaction(
        re.compile(DICT_OPEN_re_bs),
        re.compile(DICT_OPEN_LEADIN_re_bs),
        lambda m: DictOpen(m.group()),
        2,
    ),
    Reaction(
        re.compile(DICT_CLOSE_re_bs),
        re.compile(DICT_CLOSE_LEADIN_re_bs),
        lambda m: DictClose(m.group()),
        2,
    ),
    Reaction(
        re.compile(INT_re_bs),
        re.compile(INT_LEADIN_re_bs),
        lambda m: int(m.group()),
        1,
    ),
    Reaction(
        re.compile(FLOAT_re_bs),
        re.compile(FLOAT_LEADIN_re_bs),
        lambda m: float(m.group()),
        3,
    ),
    Reaction(
        re.compile(HEXSTRING_re_bs),
        re.compile(HEXSTRING_LEADIN_re_bs),
        lambda m: HexString(decode_pdf_hex(m.group(0)[1:-1])),
    ),
    Reaction(
        re.compile(SIMPLE_STRING_re_bs),
        re.compile(SIMPLE_STRING_LEADIN_re_bs),
        lambda m: decode_pdf_simple_string(m.group(1)),
    ),
]

@promote
def tokenise(buf: CornuCopyBuffer):
  ''' Scan `buf` and yield tokens, which may be `bytes` or some higher level construct.
  '''
  in_obj = None
  old_in_obj = []
  in_dict_key = None
  old_in_dict_key = []
  previous_object = None
  while True:
    with Pfx("tokenise(%s)", buf):
      for reaction in tokenisers:
        token = reaction.match(buf, previous_object=previous_object)
        if token is not None:
          break
      else:
        if buf.at_eof():
          # end parse loop
          break
        # nothing matched - yield the starting byte
        warning(
            "tokenise: no match at offset %d:%r..., taking the first byte",
            buf.offset,
            buf.peek(8, short_ok=True),
        )
        token = buf.take(1)
      if isinstance(token, ArrayOpen):
        old_in_obj.append(in_obj)
        in_obj = ArrayObject()
        previous_object = None
        continue
      if isinstance(token, DictOpen):
        old_in_obj.append(in_obj)
        old_in_dict_key.append(in_dict_key)
        in_obj = DictObject()
        in_dict_key = None
        previous_object = None
        continue
      if isinstance(token, ArrayClose):
        # replace token with the array
        if isinstance(in_obj, ArrayObject):
          token = in_obj
          in_obj = old_in_obj.pop()
          previous_object = None
        else:
          warning("unexpected %r, in_obj is %r", token, in_obj)
      elif isinstance(token, DictClose):
        # replace token with the dictionary
        if isinstance(in_obj, DictObject):
          if in_dict_key is not None:
            warning(
                "%r: trailing key %r, associating with null", token,
                in_dict_key
            )
            in_obj[in_dict_key] = Keyword(b'null')
            in_dict_key = None
          token = in_obj
          in_obj = old_in_obj.pop()
          in_dict_key = old_in_dict_key.pop()
        else:
          warning("unexpected %r, in_obj is %r", token, in_obj)
    if in_obj is None:
      yield token
    else:
      if not isinstance(token, (Comment, WhiteSpace)):
        # another object - stash it
        if isinstance(in_obj, ArrayObject):
          in_obj.append(token)
        elif isinstance(in_obj, DictObject):
          if in_dict_key is None:
            # key
            in_dict_key = token
          else:
            # value - store key and value
            in_obj[in_dict_key] = token
            in_dict_key = None
        else:
          raise RuntimeError(
              f'unhandled in_obj:{in_obj!r}, expected ArrayObject or DictObject'
          )
    if not isinstance(token, (Comment, WhiteSpace)):
      previous_object = token
  while in_obj is not None:
    warning("tokenise(%s): unclosed %r at EOF", buf, in_obj)
    try:
      in_obj = old_in_obj.pop()
    except IndexError:
      break

def decode_pdf_hex(bs: bytes):
  ''' Decode a PDF hex string body.
  '''
  if len(bs) % 2:
    bs += b'0'
  return binascii.unhexlify(bs)

def decode_pdf_name(bs: bytes):
  ''' Decode a PDF name.
  '''
  NAME_HASHHEX_bre = re.compile(NAME_HASHHEX_re_bs)
  NAME_NONHASH_bre = re.compile(NAME_NONHASH_re_bs + br'+')
  bss = []
  offset = 0
  while offset < len(bs):
    offset0 = offset
    m = NAME_HASHHEX_bre.match(bs)
    if m:
      bss.append(binascii.unhexlify(m.group()[1:]))
      offset = m.end()
    else:
      m = NAME_NONHASH_bre.match(bs)
      bss.append(m.group())
      offset = m.end()
    assert offset > offset0
  return b''.join(bss)

def decode_pdf_simple_string(bs: bytes):
  ''' Decode a PDF simple string, one with no internal brackets.
  '''
  STRING_NONSLOSH_bre = re.compile(STRING_NONSLOSH_re_bs + br'*')
  STRING_SLOSHED_bre = re.compile(STRING_SLOSHED_re_bs)
  bss = []
  offset = 0
  while offset < len(bs):
    offset0 = offset
    m = STRING_SLOSHED_bre.match(bs, offset)
    if m:
      offset = m.end()
      mbs = m.group()
      assert mbs.startswith(b'\\')
      if mbs == b'\\n':
        mbs = b'\n'
      elif mbs == b'\\r':
        mbs = b'\r'
      elif mbs == b'\\t':
        mbs = b'\t'
      elif mbs == b'\\b':
        mbs = b'\b'
      elif mbs == b'\\f':
        mbs = b'\f'
      elif mbs == b'\\(':
        mbs = b'('
      elif mbs == b'\\)':
        mbs = b')'
      elif mbs == b'\\\\':
        mbs = b'\\'
      else:
        mbs = bytes((int(mbs[1:], 8),))
    else:
      m = STRING_NONSLOSH_bre.match(bs, offset)
      offset = m.end()
      mbs = m.group()
    bss.append(mbs)
    assert offset > offset0
  return b''.join(mbs)

if __name__ == '__main__':
  setup_logging()
  buf = CornuCopyBuffer.from_fd(0)
  offset = buf.offset
  for token in tokenise(buf):
    print('=>', offset, r(token))
    offset = buf.offset
    ##break
