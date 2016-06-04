#!/usr/bin/python
#
# Convenience functions editing things.
#   - Cameron Simpson <cs@zip.com.au> 02jun2016
#

from __future__ import print_function, absolute_import
import os
import os.path
import sys
from subprocess import Popen
from tempfile import NamedTemporaryFile

# default editor
EDITOR = 'vi'

def edit_strings(strs, editor=None, environ=None):
  ''' Edit a list of string, return tuples of changed string pairs.
      Honours $EDITOR envvar, defaults to "vi".
  '''
  if editor is None:
    if environ is None:
      environ = os.environ
    editor = environ.get('EDITOR', EDITOR)
  strs = list(strs)
  changes = []
  with NamedTemporaryFile(mode='w') as T:
    for oldstr in strs:
      if not oldstr or not oldstr.isprintable():
        raise ValueError("unprintable: %r" % (oldstr,))
      print(oldstr, file=T)
    T.flush()
    P = Popen([editor, T.name])
    P.wait()
    if P.returncode != 0:
      raise RuntimeError("editor fails, aborting")
    ok = True
    newstrs = []
    with open(T.name, 'r') as fp:
      for lineno, newstr in enumerate(fp, 1):
        if not newstr.endswith('\n'):
          raise ValueError("%s:%d: missing newline" % (T.name, lineno))
        newstrs.append(newstr[:-1])
  if len(newstrs) != len(strs):
    raise ValueError("%d old strs, %d new strs" % (len(strs), len(newstrs)))
  changes = [ old_new
              for old_new in zip(strs, newstrs)
              if old_new[0] != old_new[1]
            ]
  return changes
