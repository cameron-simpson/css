#!/usr/bin/python
#
# Convenience routines for working with HTTP 1.1 (RFC2616).
#   - Cameron Simpson <cs@cskk.id.au> 28dec2014
#

import sys
import datetime
from email.parser import BytesFeedParser
from itertools import takewhile
from string import ascii_letters, ascii_uppercase, ascii_lowercase, digits
from cs.fileutils import copy_data
from cs.lex import get_hexadecimal, get_chars, get_other_chars
from cs.logutils import warning
from cs.timeutils import time_func
from cs.x import X

DISTINFO = {
    'description': "RFC2616 (HTTP 1.1) facilities",
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        ],
    'install_requires': ['cs.fileutils', 'cs.lex', 'cs.logutils', 'cs.timeutils'],
}

# character classes: see RFC2616 part 2.2
CR = '\r'
CHAR = ''.join( chr(o) for o in range(128) )
LF = '\n'
SP = ' '
HT = '\t'
SP_HT = SP + HT
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

# encode and decode bytes<->str for HTTP stream: 8-bit 1-to-1
enc8 = lambda s: s.encode('iso8859-1')
dec8 = lambda b: b.decode('iso8859-1')

# CRLF as bytes
CRLFb = enc8(CRLF)

def get_lws(s, offset=0):
  ''' Gather up an LWS.
  '''
  if not s.startswith(CRLF, offset):
    raise ValueError("missing CRLF at start of LWS at offset %d" % (offset,))
  spacing, offset = get_chars(s, offset+2, SP+HT)
  if not spacing:
    raise ValueError("missing SP/HT after CRLF at offset %d" % (offset,))
  return CRLF + spacing, offset

def get_space(s, offset=0):
  ''' Gather up a sequence of SP or HT, possibly empty.
  '''
  return get_chars(s, offset, SP_HT)

def get_text(s, offset=0):
  ''' Gather up a sequence of TEXT characters (possibly empty).
  '''
  return get_chars(s, offset, TEXT)

def get_token(s, offset=0):
  ''' Get an RFC2616 token from the string `s` starting at `offset`.
      Return token, new_offset.
      See RFC2616 part 2.2.
  '''
  token, offset = get_other_chars(s, offset, CTL+SEPARATORS)
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
      part, offset2 = get_other_chars(s, offset, '\\"')
      qs_parts.append(part)
      offset = offset2
  return ''.join(qs_parts), offset

def get_chunk_ext_val(s, offset=0):
  if s.startswith('"', offset):
    return get_quoted_string(s, offset)
  else:
    return get_token(s, offset)

def parse_chunk_line1(bline):
  ''' Parse the opening line of a chunked-encoding chunk.
  '''
  line = bline.decode('iso8859-1')
  # collect chunk-size
  chunk_size, offset = get_hexadecimal(line)
  if not chunk_size:
    raise ValueError("missing chunk-size")
  chunk_size = int(chunk_size, 16)
  chunk_exts = []
  # collect chunk-extensions
  _, offset = get_space(line, offset)
  while offset < len(line) and line.startswith(';', offset):
    chunk_ext_name, offset = get_token(line, offset+1)
    if not line.startswith('=', offset):
      raise ValueError("missing '=' after chunk-ext-name at offset %d" % (offset,))
    chunk_ext_val, offset = get_chunk_ext_val(line, offset+1)
    chunk_exts.append( (chunk_ext_name, chunk_ext_val) )
    _, offset = get_space(line, offset)
  if not line.startswith(CRLF, offset):
    raise ValueError("missing CRLF at end of opening chunk line at offset %d" % (offset,))
  offset += 2
  if offset != len(line):
    raise ValueError("extra data after CRLF at offset %d: %r" % (offset, line[offset:]))
  return chunk_size, chunk_exts

def read_http_request_line(fp):
  ''' Read HTTP Request-Line from the binary file `fp`, return method, uri, version.
      See RFC2616 section 5.1.
      If an empty request line is received return None, None, None.
  '''
  elapsed, bline = time_func(fp.readline)
  X("GOT REQUEST-LINE: %r", bline)
  httprq = dec8(bline).strip()
  if not httprq:
    ##info("end of client requests")
    return None, None, None
  method, uri, version = httprq.split()
  return method, uri, version

def read_headers(fp):
  ''' Read headers from a binary file such as an HTTP stream, return the raw binary data and the corresponding Message object.
  '''
  def is_header_line(line):
    return line.startswith(b' ') or line.startswith(b'\t') or line.rstrip()
  header_lines = list(takewhile(is_header_line, fp))
  parser = BytesFeedParser()
  parser.feed(b''.join(header_lines))
  return b''.join(header_lines), parser.close()

def datetime_from_http_date(s):
  ''' Parse an HTTP-date from a string, return a datetime object.
      See RFC2616 section 3.3.1.
  '''
  try:
    return datetime_from_rfc1123_date(s)
  except ValueError as e:
    X("datetime_from_rfc1123_date(%r): %s", s, e)
    try:
      return datetime_from_rfc850_date(s)
    except ValueError as e:
      X("datetime_from_rfc850_date(%r): %s", s, e)
      return datetime_from_asctime_date(s)

def datetime_from_rfc1123_date(s):
  ''' Parse an rfc1123-date from a string, return a datetime object.
      See RFC2616 section 3.3.1.
      Format: wkday, dd mon yyyy hh:mm:ss GMT
  '''
  wkday, etc = s.split(',', 1)
  if wkday not in ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'):
    raise ValueError("invalid wkday: %r" % (wkday,))
  dt = datetime.datetime.strptime(etc, " %d %b %Y %H:%M:%S GMT")
  dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta()))
  return dt

def datetime_from_rfc850_date(s):
  ''' Parse an rfc850-date from a string, return a datetime object.
      See RFC2616 section 3.3.1.
      Format: weekday, dd-mon-yy hh:mm:ss GMT
  '''
  weekday, etc = s.split(',', 1)
  if weekday not in ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'):
    raise ValueError("invalid weekday: %r" % (weekday,))
  dt = datetime.datetime.strptime(etc, " %d-%b-%y %H:%M:%S GMT")
  dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta()))
  return dt

def datetime_from_asctime_date(s):
  ''' Parse an asctime-date from a string, return a datetime object.
      See RFC2616 section 3.3.1.
      Format: wkday, mon d hh:mm:ss yyyy
  '''
  wkday, etc = s.split(',', 1)
  if wkday not in ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'):
    raise ValueError("invalid wkday: %r" % (wkday,))
  dt = datetime.datetime.strptime(etc + " GMT", " %b %d %H:%M:%S %Y %Z")
  dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta()))
  return dt

def message_has_body(headers):
  ''' Does this message have a message body to forward?
      See RFC2616, part 4.3 and 4.4.
      Note that HTTP certain requests preempty this; for example HEAD never has a body.
      That aspect is not considered here.
  '''
  content_length = headers.get('Content-Length')
  if content_length is not None:
    return True
  transfer_encoding = headers.get('Transfer-Encoding')
  if transfer_encoding is not None:
    return True
  return False

def read_chunked(fpin, has_trailer=False):
  ''' Generator that reads "chunked" data from `fpin`, and an optional trailer section (default False).
      See RFC2616, part 3.6.1.
  '''
  while True:
    bline = fpin.readline()
    yield bline
    chunk_size, chunk_exts = parse_chunk_line1(bline)
    if chunk_size == 0:
      break
    yield read_length(fpin, chunk_size)
    crlf = fpin.read(2)
    if len(crlf) == 0:
      raise ValueError("pass_chunked: empty data received after chunk-data")
    if crlf != CRLFb:
      raise ValueError("missing CRLF after chunk data, found: %r" % (crlf,))
    yield crlf
  if has_trailer:
    trailer_data, trailer_headers = read_headers(fpin)
    yield trailer_data

def pass_chunked(fpin, fpout, has_trailer):
  ''' Copy "chunked" data from `fpin` to `fpout`, and an optional trailer section (default False).
      See RFC2616, part 3.6.1.
  '''
  for data in read_chunked(fpin, has_trailer=has_trailer):
    fpout.write(data)

def pass_length(fpin, fpout, length):
  ''' Copy a specific amount of data from `fpin` to `fpout`.
  '''
  return copy_data(fpin, fpout, length)

if __name__ == '__main__':
  import cs.rfc2616_tests
  cs.rfc2616_tests.selftest(sys.argv)
