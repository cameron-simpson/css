#!/usr/bin/python
#
# Convenience routines for working with HTTP 1.1 (RFC2616).
#   - Cameron Simpson <cs@zip.com.au> 28dec2014
#

import sys
from string import ascii_letters, ascii_uppercase, ascii_lowercase, digits
from cs.lex import get_hexadecimal, get_chars, get_other_chars

# character classes: see RFC2616 part 2.2
CR = '\r'
CHAR = ''.join( chr(o) for o in range(128) )
LF = '\n'
SP = ' '
HT = '\t'
CRLF = '\r\n'
LWS = CR + LF + SP + HT
DQ = '"'
DIGIT = digits
UPALPHA = ascii_uppercase
LOALPHA = ascii_lowercase
ALPHA = UPALPHA + LOALPHA
CTL = ''.join( chr(o) for o in list(range(32))+[127] )
SEPARATORS = '()<>@,;:\\' + DQ + '/[]?={}' + SP + HT
TEXT = ''.join( c for c in [ chr(o) for o in range(256) ]
                  if c in LWS or c not in CTL
              )
QDTEXT = TEXT.replace('"', '').replace('\\', '')

def get_lws(s, offset=0):
  ''' Gather up an LWS.
  '''
  if not s.startswith(CRLF, offset):
    raise ValueError("missing CRLF at start of LWS at offset %d" % (offset,))
  spacing, offset = get_chars(s, SP+HT, offset+2)
  if not spacing:
    raise ValueError("missing SP/HT after CRLF at offset %d" % (offset,))
  return CRLF + spacing, offset

def get_text(s, offset=0):
  ''' Gather up a sequence of TEXT characters (possibly empty).
  '''
  return get_chars(s, TEXT, offset)

def get_token(s, offset=0):
  ''' Get an RFC2616 token from the string `s` starting at `offset`.
      Return token, new_offset.
      See RFC2616 part 2.2.
  '''
  token, offset = get_other_chars(s, CTL+SEPARATORS, offset)
  if not token:
    raise ValueError("expected RFC2616 token at offset=%d" % (offset,))
  return token, offset

def get_quoted_string(s, offset=0):
  ''' Match a quoted-string in `s` starting at `offset`.
  '''
  if not s.startswith('"', offset):
    raise ValueError("missing double quote at offset %d" % (offset,))
  offset += 1
  qs_parts = []
  while offset < len(s):
    if s.startswith('\\', offset):
      offset += 1
      if offset >= len(s):
        raise ValueError("short string after slosh at offset %d" % (offset,))
      qs_parts.append(s[offset])
      offset += 1
    elif s.startswith('"', offset):
      offset += 1
      break
    else:
      part, offset2 = get_chars(s, '\\"', offset)
      qs_part.append(part)
      offset = offset2
  return ''.join(qs_parts), offset

def get_chunk_ext_val(s, offset=0):
  offset0 = offset
  if not s.startswith(';', offset):
    raise ValueError("missing semicolon at offset %d" % (offset,))
  if s.startswith('"', offset0+1):
    qstr, offset = get_qstr(s, offset0+1)
  else:
    token, offset = get_token(s, offset0+1)

def parse_chunk_line1(bline):
  ''' Parse the opening line of a chunked-encoding chunk.
  '''
  line = bline.decode('iso8859-1')
  if not line.endswith('\n'):
    raise ValueError("unexpected EOF reading chunk-size")
  # collect chunk-size
  chunk_size, offset = get_hexadecimal(line_chunk_size)
  if not chunk_size:
    raise ValueError("missing chunk-size")
  chunk_size = int(chunk_size, 16)
  chunk_exts = []
  # collect chunk-extensions
  while offset < len(line) and line.startswith(';'):
    token, offset = get_token(line, offset+1)

if __name__ == '__main__':
  import cs.rfc2616_tests
  cs.rfc2616_tests.selftest(sys.argv)
