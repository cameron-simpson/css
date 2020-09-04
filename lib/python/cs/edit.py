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
from cs.deco import fmtdoc
from cs.pfx import Pfx

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.deco', 'cs.pfx'],
}

# default editor
EDITOR = 'vi'

@fmtdoc
def choose_editor(editor=None, environ=None):
  ''' Choose an editor,
      honouring the `$EDITOR` environment variable.

      Parameters:
      * `editor`: optional editor,
        default from `environ['EDITOR']`
        or from `EDITOR` (`{EDITOR!r}`).
      * `environ`: optional environment mapping,
        default `os.environ`
  '''
  if editor is None:
    if environ is None:
      environ = os.environ
    editor = environ.get('EDITOR', EDITOR)
  return editor

def edit_strings(strs, editor=None, environ=None):
  ''' Edit an iterable list of `str`, return tuples of changed string pairs.

      The editor is chosen by `choose_editor(editor=editor,environ=environ)`.
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

      The editor is chosen by `choose_editor(editor=editor,environ=environ)`.
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
