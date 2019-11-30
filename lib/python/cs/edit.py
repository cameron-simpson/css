#!/usr/bin/python
#

r'''
Convenience functions for editing things.
- Cameron Simpson <cs@cskk.id.au> 02jun2016
'''

from __future__ import print_function, absolute_import
import os
import os.path
from subprocess import Popen
from tempfile import NamedTemporaryFile
from cs.pfx import Pfx

# default editor
EDITOR = 'vi'

def choose_editor(editor=None, environ=None):
  ''' Choose an editor.
  '''
  if editor is None:
    if environ is None:
      environ = os.environ
    editor = environ.get('EDITOR', EDITOR)
  return editor

def edit_strings(strs, editor=None, environ=None):
  ''' Edit a list of string, return tuples of changed string pairs.
      Honours $EDITOR envvar, defaults to "vi".
  '''
  oldstrs = list(strs)
  newstrs = edit(strs, editor, environ)
  if len(newstrs) != len(oldstrs):
    raise ValueError("%d old strs, %d new strs" % (len(oldstrs), len(newstrs)))
  changes = [
      old_new for old_new in zip(oldstrs, newstrs) if old_new[0] != old_new[1]
  ]
  return changes

def edit(lines, editor=None, environ=None):
  ''' Write lines to a temporary file, edit the file, return the new lines.
  '''
  editor = choose_editor(editor, environ)
  with NamedTemporaryFile(mode='w') as T:
    for lineno, line in enumerate(lines, 1):
      with Pfx("%d: %r", lineno, line):
        if '\n' in line:
          raise ValueError("newline in line")
        T.write(line)
        T.write('\n')
    T.flush()
    P = Popen([editor, T.name])
    P.wait()
    if P.returncode != 0:
      raise RuntimeError("editor fails, aborting")
    with open(T.name, 'r') as f:
      lines = []
      for lineno, line in enumerate(f, 1):
        with Pfx("%d: %r", lineno, line):
          if not line.endswith('\n'):
            raise ValueError("missing newline")
          lines.append(line[:-1])
  return lines
