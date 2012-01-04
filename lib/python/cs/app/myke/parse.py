#!/usr/bin/python
#

import sys
from collections import namedtuple
from itertools import product
import re
import unittest
from cs.logutils import error, Pfx

# match
#  identifier =
# or
#  identifier(param[,param...]) =
#
RE_ASSIGNMENT = re.compile( r'^\s*([A-Za-z_]\w*)(\([A-Za-z_]\w*(\s*,\s*[A-Za-z_]\w*)*\s*\))?\s*=' )

RE_COMMASEP = re.compile( r'\s*,\s*' )

RE_WHITESPACE = re.compile( r'\s+' )

# text that isn't whitespace or a macro
# in target definitions RE_PLAINTEXT_NOCOLON is used
RE_PLAINTEXT = re.compile( r'[^\s$]+' )
RE_PLAINTEXT_NO_COLON = re.compile( r'[^\s$:]+' )

FileContext = namedtuple('FileContext', 'filename lineno text parent')

class ParseError(SyntaxError):
  ''' A ParseError subclasses SyntaxError in order to change the initialiser.
      This object has an additional attribute .context for the relevant FileContext
      (which has a .parent attribute).
  '''

  def __init__(self, context, offset, message):
    ''' Initialise a ParseError given a FileContext and the offset into `context.text`.
    '''
    self.msg = message
    self.filename = context.filename
    self.lineno = context.lineno
    self.text = context.text
    self.offset = offset
    self.context = context

class Macro(object):
  ''' A macro definition.
  '''

  def __init__(self, context, name, params, text):
    self.context = context
    self.name = name
    self.params = params
    self.text = text

  def __str__(self):
    if self.params:
      return "<Macro %s(%s) = %s>" % (self.name, ", ".join(self.params), self.text)
    return "<Macro %s = %s>" % (self.name, self.text)

def parseMakefile(fp, namespaces, parent_context=None):
  ''' Read a Mykefile and yield Macros and Targets.
  '''
  if type(fp) is str:
    # open file, yield contents
    filename = fp
    with open(filename) as fp:
      for O in parseMakefile(fp, parent_context):
        yield O
    return

  from .make import Target, Action

  try:
    filename = fp.name
  except AttributeError:
    filename = str(fp)

  with Pfx(filename):
    ok = True
    action_list = None       # not in a target
    ifStack = []        # active ifStates (state, firstbranch)
    ifState = None      # ifStack[-1]
    context = None      # FileContext(filename, lineno, line)

    lineno = 0
    prevline = None
    for line in fp:
      lineno += 1
      if prevline is not None:
        # prepend previous continuation line if any
        line = prevline + '\n' + line
        prevline = None
      else:
        # start of line - new context
        context = FileContext(filename, lineno, line, parent_context)

      if not line.endswith('\n'):
        raise ParseError(context, len(line), 'unexpected EOF (missing final newline)')

      if line.endswith('\\\n'):
        # continuation line - gather next line before parse
        prevline = line[:-2]
        continue

      line = line.rstrip()
      if not line or line.lstrip().startswith('#'):
        # skip blank lines and comments
        continue

      if line.startswith(':'):
        raise NotImplementedError, "directives unimplemented"

      if action_list is not None:
        if not line[0].isspace():
          # new target or unindented assignment etc - fall through
          # action_list is already attached to targets,
          # so simply reset it to None to keep state
          action_list = None
        else:
          line = line.strip()
          if line.startswith(':'):
            # in-target directive like ":make"
            raise NotImplementedError, "in-target directives unimplemented"
          else:
            action_list.append(Action(context, line))
          continue

      m = RE_ASSIGNMENT.match(line)
      if m:
        yield Macro(context, m.group(1), RE_COMMASEP.split(m.group(1)), line[m.end():])
        continue

      # presumably a target definition
      # gather up the target as a macro expression
      target_mexpr, offset = parseMacroExpression(context, re_plaintext=RE_PLAINTEXT_NO_COLON)
      print >>sys.stderr, "targets_mexpr =", repr(target_mexpr), "offset =", offset
      if context.text[offset] != ':':
        raise ParseError(context, offset, "no colon in target definition")
      prereqs_mexpr, offset = parseMacroExpression(context, re_plaintext=RE_PLAINTEXT_NO_COLON, offset=offset+1)
      print >>sys.stderr, "prereqs_mexpr =", repr(prereqs_mexpr), "offset =", offset
      if offset < len(context.text) and context.text[offset] == ':':
        postprereqs_mexpr, offset = parseMacroExpression(context, re_plaintext=RE_PLAINTEXT_NO_COLON, offset=offset+1)
      else:
        postprereqs_mexpr = []
      print >>sys.stderr, "postprereqs_mexpr =", repr(postprereqs_mexpr), "offset =", offset, "etc =", repr(context.text[offset:])

      action_list = []
      for target in target_mexpr.eval(namespaces).split():
        yield Target(target, context, prereqs=prereqs_mexpr, postprereqs=postprereqs_mexpr, actions=action_list)
      continue

      raise ParseError(context, 0, 'unparsed line')

    if prevline is not None:
      # incomplete continuation line
      error("unexpected EOF: unterminated slosh continued line")

  if target:
    yield target

# mapping of special macro names to evaluation functions
SPECIAL_MACROS = { '.':         None,
                 }

def parseMacroExpression(context, offset=0, re_plaintext=None):
  ''' A macro expression is a concatenation of permutations.
  '''
  if type(context) is str:
    context = FileContext('<string>', 1, context, None)

  if re_plaintext is None:
    re_plaintext = RE_PLAINTEXT

  s = context.text
  permutations = []
  while offset < len(s):
    ch = s[offset]
    if ch == '$':
      # macro
      M, offset = parseMacro(context, offset)
      permutations.append(M)
    elif ch.isspace():
      # whitespace
      wh_offset = offset
      offset += 1
      while offset < len(s) and s[offset].isspace():
        offset += 1
      # skip leading whitespace
      if permutations:
        permutations.append(s[wh_offset:offset])
    else:
      # non-white, non-macro
      m = re_plaintext.match(s, offset)
      if m:
        permutations.append(m.group())
        offset = m.end()
      else:
        # end of parsable string
        break

  # drop trailing whitespace
  if permutations:
    last = permutations[-1]
    if type(last) is str and last.isspace():
      permutations.pop()

  return MacroExpression(context, permutations), offset

class MacroExpression(object):

  def __init__(self, context, permutations):
    self.context = context
    self.permutations = permutations
    self._result = None

  def __str__(self):
    return repr(self.permutations)

  __repr__ = __str__

  def eval(self, namespaces):
    if self._result is not None:
      return self._result
    strs = []           # strings to collate
    wordlists = None        # accruing word permutations
    for item in self.permutations:
      if type(item) is str:
        if item.isspace():
          # whitespace - end of word
          # stash existing word if any
          if wordlists:
            # non-empty word accruing
            strs.append(" ".join( [ ''.join(wordlists) for wordlists in product(wordlists) ] ))
            wordlists = None
          strs.append(item)
        else:
          # word
          if wordlists is None:
            wordlists = [ [item] ]
          else:
            if len(wordlists) > 0:
              wordlists.append([word])
      else:
        # MacroTerm
        if wordlists is not None and len(wordlists) == 0:
          # word already short circuited - skip evaluating the MacroTerm
          pass
        else:
          text = item.eval(namespaces)
          if item.permute:
            textwords = text.split()
            if len(textwords) == 0:
              # short circuit
              wordlists = []
            else:
              if wordlists is None:
                wordlists = [ textwords ]
              else:
                wordlists.append(textwords)
          else:
            wordlists.append([text])

    if wordlists:
      # non-empty word accruing - flatten it and store
      strs.append(" ".join( [ ''.join(wordlist) for wordlist in product(*wordlists) ] ))

    result = ''.join(strs)
    return result

def parseMacro(context, offset=0):
  if type(context) is str:
    context = FileContext('<string>', 1, context, None)

  mmark = None
  mtext = None
  mpermute = False
  modifiers = []

  s = context.text
  if s[offset] != '$':
    raise ParseError(context, offset, 'expected "$" at start of macro')

  offset += 1
  s2 = s[offset]

  if s2 == '(' or s2 == '{':
    # $(foo)
    offset += 1
    mmark = s2
    s3 = s[offset]
    if s3 == mmark:
      # $((foo))
      offset += 1
      mpermute = True
    while s[offset].isspace():
      offset += 1
    q = s[offset]
    if q == '"' or q == "'":
      # $('qstr')
      offset += 1
      text_offset = offset
      while s[offset] != q:
        offset += 1
      mtext = s[text_offset:offset]
      offset += 1
    elif q == '_' or q.isalpha():
      # $(macro_name)
      name_offset = offset
      offset += 1
      while s[offset] == '_' or s[offset].isalnum():
        offset += 1
      mtext = s[name_offset:offset]
      modifiers.append('v')
    elif q in SPECIAL_MACROS:
      mtext = q
      offset += 1
    else:
      raise ParseError(context, offset, 'unknown special macro name "%s"' % (q,))

    # skip past whitespace
    while s[offset].isspace():
      offset += 1

    # collect modifiers
    while True:
      ch = s[offset]
      if ch == mmark:
        break
      if ch.isspace():
        pass
      elif ch in 'DF':
        # simple modifiers
        modifiers.append(ch)
      elif ch in 'Gv':
        # modifiers with optional '?'
        if s[offset+1] == '?':
          modifiers.append(s[offset:offset+2])
          offset += 1
        else:
          modifiers.append(ch)
      else:
        raise ParseError(context, offset, 'unknown macro modifier "%s"' % (ch,))
      offset += 1

    assert ch == mmark, "should be at \"%s\", but am at: %s" % (mmark, s[offset:])
    offset += 1
    if mpermute:
      if s[offset] != mmark:
        raise ParseError(context, offset, 'incomplete macro closing brackets')
      else:
        offset += 1

    return MacroTerm(context, mtext, modifiers, mpermute), offset

  # $x
  ch = s[offset]
  if ch == '_' or ch.isalnum() or ch in SPECIAL_MACROS:
    offset += 1
    return MacroTerm(context, ch), offset

  raise ParseError(context, offset, 'unknown special macro name "%s"' % (ch,))

class MacroTerm(object):
  ''' A macro use.
  '''

  def __init__(self, context, text, modifiers=(), permute=False):
    self.context = context
    self.text = text
    self.modifiers = modifiers
    self.permute = permute

  def __str__(self):
    # return $x for simplest macro
    if len(self.text) == 1 and not self.permute and not self.modifiers:
      return '$' + self.text

    # otherwise $(x) notation
    if self.modifiers and self.modifiers[0] == 'v':
      text = self.text
      modifiers = self.modifiers[1:]
    else:
      text = '"%s"' % self.text
      modifiers = self.modifiers
    return '$%s%s%s%s%s' % ( ( '((' if self.permute else '(' ),
                             text,
                             ( ' ' if modifiers else '' ),
                             ''.join(modifiers),
                             ( '))' if self.permute else ')' ),
                           )

class TestAll(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def test00parseMacro(self):
    M, offset = parseMacro('$x')
    self.assertEqual(offset, 2)
    self.assertEqual(str(M), '$x')
    M, offset = parseMacro('$.')
    self.assertEqual(offset, 2)
    self.assertEqual(str(M), '$.')

  def test10parseMacroExpr_PlainText(self):
    self.assertEquals(parseMacroExpression(''),  ([], 0))
    self.assertEquals(parseMacroExpression('x'), (['x'], 1))
    self.assertEquals(parseMacroExpression(' '), ([' '], 1))
    self.assertEquals(parseMacroExpression('x y'), (['x', ' ', 'y'], 3))
    self.assertEquals(parseMacroExpression('abc  xyz'), (['abc', '  ', 'xyz'], 8))

  def test20parseMakeLines(self):
    from StringIO import StringIO
    assert False, str(list(parseMakefile(StringIO("abc = def\n"))))

if __name__ == '__main__':
  unittest.main()
