#!/usr/bin/python
#

r'''
Convenience functions for constructing shell commands

Functions for safely constructing shell command lines from bare strings.
Somewhat like the inverse of the shlex stdlib module.

As of Python 3.3 the function `shlex.quote()` does what `quotestr()` does.

As of Python 3.8 the function `shlex.join()` does what `quotecmd()` does.
'''

import string
import sys

__version__ = '20210316'

DISTINFO = {
    'description': "Convenience functions for constructing shell commands.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
    'entry_points': {
        'console_scripts': [
            'shqstr = cs.sh:main_shqstr'
        ],
    },
}

# characters than do not need to be quoted
SAFECHARS = string.digits + string.ascii_letters + '-+_.,/:'

def quote(args):
  ''' Quote the supplied strings, return a list of the quoted strings.

      As of Python 3.8 the function `shlex.join()` is available for this.
  '''
  return [quotestr(s) for s in args]

def quotestr(s):
  ''' Quote a string for use on a shell command line.

      As of Python 3.3 the function `shlex.quote()` is available for this.
  '''
  # decide how to quote the string:
  # - empty string: return ''
  # - all safe: do not quote
  # - no single quotes: just single quote the string
  # - funky quoting
  if not s:
    return "''"
  q_count = 0
  unsafe_count = 0
  switch_count = 0
  safe = True
  for c in s:
    if c == "'":
      q_count += 1
      new_safe = False
    elif c not in SAFECHARS:
      unsafe_count += 1
      new_safe = False
    else:
      new_safe = True
    if safe ^ new_safe:
      switch_count += 1
      safe = new_safe
  if q_count == 0 and unsafe_count == 0:
    # all safe
    return s
  if q_count == 0:
    # unsafe but no single quotes
    return "'" + s + "'"
  def flush():
    ''' Output the pending characters, which are either all safe or all unsafe.
    '''
    if offset > start:
      part = s[start:offset]
      if not safe:
        if len(part) == 1:
          qparts.append("\\")
        else:
          qparts.append("'")
      qparts.append(part)
      if not safe:
        if len(part) > 1:
          qparts.append("'")
  qparts = []
  start = 0
  safe = True       # not in quotes
  for offset, c in enumerate(s):
    if c == "'" or (safe and c == '\\'):
      flush()
      qparts.append('\\')
      qparts.append(c)
      start = offset + 1    # do not include this character in the flushable set
      new_safe = False
    else:
      new_safe = c in SAFECHARS
      if safe ^ new_safe:
        # mode change
        flush()
        start = offset
        safe = new_safe
  # advance past final position
  offset += 1
  flush()
  return ''.join(qparts)

def quotecmd(argv):
  ''' Quote strings, assemble into command string.
  '''
  return ' '.join(quote(argv))

def main_shqstr(argv=None):
  ''' shqstr: emit shell-quoted form of the command line arguments.
  '''
  if argv is None:
    argv = sys.argv
  argv.pop(0)
  print(quotecmd(argv))

if __name__ == '__main__':
  sys.exit(main_shqstr())
