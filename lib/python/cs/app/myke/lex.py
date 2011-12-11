#!/usr/bin/python
#

from cs.logutils import error, Pfx

class FileContext(object):
  def __init__(self, filename, lineno, parent=None):
    self.filename = filename
    self.lineno = lineno
    self.parent = parent

def readMakefile(filename, context=None):
  ''' Read a Mykefile and yield objects: Macros, Targets and finally Boolean
      indicating an error free parse of the file.
  '''
  with Pfx(filename):
    ok = True
    inTarget = False    # not in a target
    ifStack = []        # active ifStates (state, firstbranch)
    ifState = None      # ifStack[-1]
    with open(filename) as fp:
      lineno = 0
      prevline = None
      for line in fp:
        lineno += 1

        if not line.endswith('\n'):
          error("unexpected EOF")
          ok = False
          break

        if prevline is not None:
          # prepend previous continuation line if any
          line = prevline + line
          prevline = None

        if line.endswith('\\\n'):
          # continuation line - gather next line before parse
          prevline = line
          continue

        if line.startswith(':'):
          assert False, "directives unimplemented"

        assert False

      if prevline is not None:
        # incomplete continuation line
        error("unexpected EOF: unterminated slosh continued line")
        ok = False

    yield ok
