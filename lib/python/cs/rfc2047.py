#!/usr/bin/python
#
# RFC2047 - MIME Part 3 - http://tools.ietf.org/html/rfc2047
#

import base64
import quopri
import re
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
    start = m.start()
    end = m.end()
    if start > sofar:
      chunks.append(s[sofar:start])
    enccset = m.group(1)
    enctype = m.group(2).upper()
    # default to undecoded text
    enctext = m.group(3)
    if enctype == 'B':
      try:
        enctext = base64.b64decode(enctext)
      except TypeError as e:
        warning("%r: %e", enctext, e)
        enctext = m.group()
    elif enctype == 'Q':
      try:
        enctext = quopri.decodestring(enctext.replace('_', ' '))
      except UnicodeEncodeError as e:
        warning("%r: %e", enctext, e)
        ##enctext = enctext.decode('iso8859-1')
    else:
      raise RuntimeError("unhandled RFC2047 string: %r" % (m.group(),))
    try:
      enctext = enctext.decode(enccset)
    except LookupError as e:
      warning("decode(%s): %e: %r", enccset, e, enctext)
      enctext = enctext.decode('iso8859-1')
    except UnicodeDecodeError as e:
      warning("decode(%s): %s: %r", enccset, e, enctext)
      enctext = enctext.decode(enccset, 'replace')
    except UnicodeEncodeError as e:
      warning("decode(%s): %e: %r", enccset, e, enctext)
      enctext = enctext.decode(enccset, 'replace')
    chunks.append(enctext)
    sofar = end
  if sofar < len(s):
    chunks.append(s[sofar:])
  return unicode('').join(chunks)
