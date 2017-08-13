#!/usr/bin/python
#
# RFC2047 - MIME Part 3 - http://tools.ietf.org/html/rfc2047
#

import base64
import quopri
import re
from cs.pfx import Pfx
from cs.py3 import unicode

# regexp to match RFC2047 text chunks
re_RFC2047 = re.compile(r'=\?([^?]+)\?([QB])\?([^?]*)\?=', re.I)

def unrfc2047(s, warning=None):
  ''' Accept a string `s` containing RFC2047 text encodings (or the whitespace
      littered varieties that come from some low quality mail clients) and
      decode them into flat Unicode.
      `warning`: optional parameter specifying function to report warning messages, default cs.logutils.warning
  '''
  if warning is None:
    from cs.logutils import warning
  if not isinstance(s, unicode):
    s = unicode(s, 'iso8859-1')
  chunks = []
  sofar = 0
  for m in re_RFC2047.finditer(s):
    with Pfx("%r", m.group(0)):
      start = m.start()
      end = m.end()
      if start > sofar:
        chunks.append(s[sofar:start])
      charset = m.group(1)
      coding = m.group(2).upper()
      coded = m.group(3)
      realtext = None
      if coding == 'B':
        try:
          decoded = base64.b64decode(coded)
        except TypeError as e:
          warning("%r: %e", coded, e)
          realtext = m.group()
      elif coding == 'Q':
        try:
          decoded = quopri.decodestring(coded.replace('_', ' '))
        except (UnicodeEncodeError, ValueError) as e:
          warning("%r: %e", coded, e)
          realtext = m.group()
      else:
        raise RuntimeError("unhandled RFC2047 string: %r" % (m.group(),))
      if realtext is None:
        try:
          realtext = decoded.decode(charset)
        except LookupError as e:
          warning("decode(%r): %e", decoded, e)
          realtext = decoded.decode('iso8859-1')
      chunks.append(realtext)
      sofar = end
  if sofar < len(s):
    chunks.append(s[sofar:])
  return unicode('').join(chunks)
