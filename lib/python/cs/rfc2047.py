#!/usr/bin/python
#
# RFC2047 - MIME Part 3 - http://tools.ietf.org/html/rfc2047
#

r'''
unrfc2047: a decoder for RFC2047 (MIME Part 3) encoded text.
'''

from __future__ import print_function
import base64
import quopri
import re
from cs.gimmicks import warning
from cs.pfx import Pfx
from cs.py3 import unicode

__version__ = '20200524-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.gimmicks',
        'cs.pfx',
        'cs.py3',
    ],
}

# regexp to match RFC2047 text chunks
re_RFC2047 = re.compile(r'=\?([^?]+)\?([QB])\?([^?]*)\?=', re.I)

# The fallback character set for unknown charsets.
# This has been arbitrarily chosen, and parochially assumes the
# author's European heritage is the likely source of incoming email.
# The most useful fallbacks should be 8-bit character sets which
# (a) are always available and (b) never fail to decode.
FALLBACK_CHARSET = 'iso8859-1'

def unrfc2047(s):
  ''' Accept a string `s` containing RFC2047 text encodings (or the whitespace
      littered varieties that come from some low quality mail clients) and
      decode them into flat Unicode.

      See http://tools.ietf.org/html/rfc2047 for the specification.
  '''
  if not isinstance(s, unicode):
    # TODO: should this come from the locale? that seems arbitrary as well
    s = unicode(s, FALLBACK_CHARSET)
  chunks = []
  sofar = 0
  for m in re_RFC2047.finditer(s):
    encoded_word = m.group()
    with Pfx(encoded_word):
      start = m.start()
      end = m.end()
      if start > sofar:
        chunks.append(s[sofar:start])
      charset = m.group(1)
      encoding = m.group(2).upper()
      encoded_text = m.group(3)
      decoded = None
      realtext = None
      if encoding == 'B':
        try:
          decoded = base64.b64decode(encoded_text)
        except (ValueError, TypeError) as e:
          warning("%r: %s", encoded_text, e)
          realtext = encoded_word
      elif encoding == 'Q':
        try:
          decoded = quopri.decodestring(encoded_text.replace('_', ' '))
        except (UnicodeEncodeError, ValueError) as e:
          warning("%r: %s", encoded_text, e)
          realtext = encoded_word
      else:
        warning("unhandled RFC2047 encoding %r, not decoding", encoding)
        realtext = encoded_word
      if realtext is None:
        try:
          realtext = decoded.decode(charset, 'replace')
        except LookupError as e:
          warning(
              "charset %r: %s; falling back to %r", charset, e,
              FALLBACK_CHARSET
          )
          try:
            realtext = decoded.decode(FALLBACK_CHARSET, 'replace')
          except LookupError:
            warning("fallback charset %r: %s; not decoding", charset, e)
            realtext = encoded_word
      chunks.append(realtext)
      sofar = end
  if sofar < len(s):
    chunks.append(s[sofar:])
  return unicode('').join(chunks)
