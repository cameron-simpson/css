#!/usr/bin/python
#
# Convenience functions for constructing shell commands.
#   - Cameron Simpson <cs@zip.com.au>
#

DISTINFO = {
    'description': "Convenience functions for constructing shell commands.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
}

import string

# characters than do not need to be quoted
SAFECHARS = string.digits + string.ascii_letters + '-+_./:'

def quote(args):
  ''' Quote the supplied strings, return a list of the quoted strings.
  '''
  return [ quotestr(s) for s in args ]

def quotestr(s):
  ''' Quote a string for use on a shell command line.
  '''
  if not s:
    return "''"
  qparts = []
  start = offset = 0
  safemode = True       # not in quotes
  def flush():
    if offset > start:
      part = s[start:offset]
      if not safemode:
        if len(part) == 1:
          qparts.append("\\")
        else:
          qparts.append("'")
      qparts.append(s[start:offset])
      if not safemode:
        if len(part) > 1:
          qparts.append("'")
  while offset < len(s):
    c = s[offset]
    if c in "'\\":
      flush()
      start = offset
      qparts.append('\\')
      qparts.append(c)
      safemode = True
      start = offset + 1
    else:
      safe = s[offset] in SAFECHARS
      if safe ^ safemode:
        # mode change
        flush()
        safemode = safe
        start = offset
    offset += 1
  flush()
  return ''.join(qparts)
