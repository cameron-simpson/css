#!/usr/bin/python
#

import sys
from collections import namedtuple
from itertools import product
import re
from string import whitespace, letters, digits
import unittest
from cs.lex import get_chars, get_other_chars, get_white, get_identifier
from cs.logutils import Pfx, error, info, debug, exception

# macro assignment, including leading whitespace
#
# match
#  identifier =
# or
#  identifier(param[,param...]) =
#
re_identifier = r'[A-Za-z]\w*'
re_assignment = r'^\s*(' + re_identifier + ')(\(' + re_identifier + '(\s*,\s*' + re_identifier + ')*\s*\))?\s*='
RE_ASSIGNMENT = re.compile( re_assignment )

RE_COMMASEP = re.compile( r'\s*,\s*' )

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

def parseMakefile(M, fp, namespaces, parent_context=None):
  ''' Read a Mykefile and yield Macros and Targets.
  '''
  if type(fp) is str:
    # open file, yield contents
    filename = fp
    with open(filename) as fp:
      for O in parseMakefile(M, fp, namespaces, parent_context):
        yield O
    return

  from .make import Target, Action

  try:
    filename = fp.name
  except AttributeError:
    filename = str(fp)

  with Pfx(filename):
    M.debug_parse("begin parse")
    ok = True
    action_list = None       # not in a target
    ifStack = []        # active ifStates (state, firstbranch)
    ifState = None      # ifStack[-1]
    context = None      # FileContext(filename, lineno, line)

    lineno = 0
    prevline = None
    for line in fp:
      lineno += 1
      with Pfx("%d" % (lineno,)):
        if prevline is not None:
          # prepend previous continuation line if any
          line = prevline + '\n' + line
          prevline = None
        else:
          # start of line - new context
          context = FileContext(filename, lineno, line.rstrip(), parent_context)

        try:
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
              # action line
              _, offset = get_white(line)
              if offset >= len(line) or line[offset] != ':':
                # ordinary shell action
                action_silent = False
                if offset < len(line) and line[offset] == '@':
                  action_silent = True
                  offset += 1
                A = Action(context, 'shell', line[offset:], silent=action_silent)
                M.debug_parse("add action: %s", A)
                action_list.append(A)
                continue
              # in-target directive like ":make"
              _, offset = get_white(line, offset+1)
              directive, offset = get_identifier(line, offset)
              if not directive:
                raise ParseError(context, offset, "missing in-target directive after leading colon")
              A = Action(context, directive, line[offset:].lstrip())
              M.debug_parse("add action: %s", A)
              action_list.append(A)
              continue

          m = RE_ASSIGNMENT.match(line)
          if m:
            yield Macro(context, m.group(1), RE_COMMASEP.split(m.group(1)), line[m.end():])
            continue

          # presumably a target definition
          # gather up the target as a macro expression
          target_mexpr, offset = parseMacroExpression(context, stopchars=':')
          if context.text[offset] != ':':
            raise ParseError(context, offset, "no colon in target definition")
          prereqs_mexpr, offset = parseMacroExpression(context, offset=offset+1, stopchars=':')
          if offset < len(context.text) and context.text[offset] == ':':
            postprereqs_mexpr, offset = parseMacroExpression(context, offset=offset+1)
          else:
            postprereqs_mexpr = []

          action_list = []
          for target in target_mexpr.eval(namespaces).split():
            yield Target(target, context, prereqs=prereqs_mexpr, postprereqs=postprereqs_mexpr, actions=action_list)
          continue

          raise ParseError(context, 0, 'unparsed line')
        except ParseError, e:
          exception("%s", e)

    if prevline is not None:
      # incomplete continuation line
      error("unexpected EOF: unterminated slosh continued line")

    M.debug_parse("finish parse")

# mapping of special macro names to evaluation functions
SPECIAL_MACROS = { '.':         None,
                   '@':         None,
                   '?':         None,
                   '/':         None,
                   '$':         lambda x: '$',
                 }

def parseMacroExpression(context, text=None, offset=0, stopchars=''):
  ''' A macro expression is a concatenation of permutations.
      Return (MacroExpression, offset).
  '''
  if type(context) is str:
    context = FileContext('<string>', 1, context, None)

  if text is None:
    text = context.text

  permutations = []
  while offset < len(text):
    ch = text[offset]
    if ch == '$':
      # macro
      M, offset = parseMacro(context, text=text, offset=offset)
      permutations.append(M)
    elif ch.isspace():
      # whitespace
      wh, offset = get_white(text, offset)
      # keep non-leading whitespace
      if permutations:
        permutations.append(wh)
    else:
      # non-white, non-macro
      plain, offset = get_other_chars(text, stopchars+'$'+whitespace, offset)
      if plain:
        permutations.append(plain)
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
    wordlists = None    # accruing word permutations
    for item in self.permutations:
      if type(item) is str:
        if item.isspace():
          # whitespace - end of word
          # stash existing word if any
          if wordlists:
            # non-empty word accruing
            words = [ ''.join(wordlists) for wordlists in product( *wordlists ) ]
            strs.append(" ".join(words))
            wordlists = None
          strs.append(item)
        else:
          # word
          if wordlists is None:
            wordlists = [ [item] ]
          else:
            if len(wordlists) > 0:
              wordlists.append([item])
      else:
        # should be a MacroTerm
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
            if wordlists is None:
              wordlists = [ [text] ]
            else:
              wordlists.append([text])

    if wordlists:
      # non-empty word accruing - flatten it and store
      strs.append(" ".join( [ ''.join(wordlist) for wordlist in product(*wordlists) ] ))

    result = ''.join(strs)
    debug("eval returns %s", result)
    return result

SIMPLE_MODIFIERS = 'DEG?FPpSsv?<?'

def parseMacro(context, text=None, offset=0):
  if type(context) is str:
    context = FileContext('<string>', 1, context, None)

  if text is None:
    text = context.text

  mmark = None
  mtext = None
  param_mexprs = []
  modifiers = []
  mpermute = False
  mliteral = False

  try:

    if text[offset] != '$':
      raise ParseError(context, offset, 'expected "$" at start of macro')

    offset += 1
    ch = text[offset]

    # $x
    if ch == '_' or ch.isalpha() or ch in SPECIAL_MACROS:
      offset += 1
      M = MacroTerm(context, ch), offset
      return M

    # $(foo) or ${foo}
    if ch == '(':
      mmark = ch
      mmark2 = ')'
    elif ch == '{':
      mmark = ch
      mmark2 = '}'
    else:
      raise ParseError(context, offset, 'invalid special macro "%s"' % (ch,))

    # $((foo)) or ${{foo}} ?
    offset += 1
    ch = text[offset]
    if ch == mmark:
      mpermute = True
      offset += 1

    _, offset = get_white(text, offset)

    mtext, offset = get_identifier(text, offset)
    if mtext:
      # $(macro_name)
      # check for macro parameters
      _, offset = get_white(text, offset)
      if text[offset] == '(':
        # $(macro_name(param,...))
        offset += 1
        _, offset = get_white(text, offset)
        while text[offset] != ')':
          mexpr, offset = parseMacroExpression(context, text=text, offset=offset, stopchars=',)')
          param_mexprs.append(mexpr)
          _, offset = get_white(text, offset)
          if text[offset] == ',':
            # gather comma and following whitespace
            _, offset = get_white(text, offset+1)
            continue
          if text[offset] != ')':
            raise ParseError(context, offset, 'macro paramaters: expected comma or closing parenthesis, found: '+text[offset:])
        offset += 1
    else:
      q = text[offset]
      if q == '"' or q == "'":
        # $('qstr')
        mliteral = True
        offset += 1
        text_offset = offset
        while text[offset] != q:
          offset += 1
        mtext = text[text_offset:offset]
        offset += 1
      elif q in SPECIAL_MACROS:
        # $(@ ...) etc
        mtext = q
        offset += 1
      else:
        raise ParseError(context, offset, 'unknown special macro name "%s"' % (q,))

    _, offset = get_white(text, offset)

    # collect modifiers
    while True:
      try:
        ch = text[offset]
        if ch == mmark2:
          # macro closing bracket
          break
        if ch.isspace():
          # whitespace
          offset += 1
          continue
        if ch == '?':
          raise ParseError(context, offset, 'bare query "?" found in modifiers at: %s' % (text[offset:],))
        pos = SIMPLE_MODIFIERS.find(ch)
        if pos >= 0:
          modifiers.append(ch)
          offset += 1
          if offset < len(text) and text[offset] == '?':
            if pos >= len(SIMPLE_MODIFIERS) or SIMPLE_MODIFIERS[pos+1] != '?':
              raise ParseError(context, offset, 'modifier "%s" does not accept a query "?"' % (ch,))
            modifiers.append('?')
            offset += 1
        else:
          raise ParseError(context, offset, 'unknown macro modifier "%s": "%s"' % (ch, text[offset:]))
      except ParseError, e:
        error("%s", e)
        offset += 1

    assert ch == mmark2, "should be at \"%s\", but am at: %s" % (mmark, text[offset:])
    offset += 1
    if mpermute:
      if text[offset] != mmark2:
        raise ParseError(context, offset, 'incomplete macro closing brackets')
      else:
        offset += 1

    M = MacroTerm(context, mtext, modifiers, param_mexprs, permute=mpermute), offset
    return M

  except IndexError, e:
    raise ParseError(context, offset, 'parse incomplete, offset=%d, remainder: %s' % (offset, text[offset:]))

  raise ParseError(context, offset, 'unhandled parse failure at offset %d: %s' % (offset, text[offset:]))

class MacroTerm(object):
  ''' A macro reference such as $x or $(foo(a,b,c) xyz).
  '''

  def __init__(self, context, text, modifiers='', params=(), permute=False, literal=False):
    self.context = context
    self.text = text
    self.modifiers = modifiers
    self.params = params
    self.permute = permute
    self.literal = literal

  def __str__(self):
    # return $x for simplest macro
    if len(self.text) == 1 and not self.permute and not self.literal:
      return '$' + self.text

    # otherwise $(x) notation
    return '$%s%s%s%s%s' % ( ( '((' if self.permute else '(' ),
                             ('"%s"' % (self.text,) if self.literal else self.text),
                             ( ' ' if self.modifiers else '' ),
                             ''.join(self.modifiers),
                             ( '))' if self.permute else ')' ),
                           )

  __repr__ = __str__

  def eval(self, namespaces, params=[]):
    with Pfx(self.context):
      text = self.text
      modifiers = self.modifiers

      if len(self.params) != len(params):
        raise ValueError("parameter count mismatch, expected %d, received %d" % (len(self.params), len(params)))

      # assemble paramaters for namespace use
      param_map = {}
      for param, mexpr in zip(self.params, params):
        param_map[param] = Macro(context, param, (), mexpr)
      namespaces = [param_map] + namespaces

      modifiers = list(modifiers)
      while modifiers:
        m = modifiers.pop(0)
        if m == 'v':
          # TODO: accept lax?
          text = ' '.join( MacroTerm(context, word).eval(context, namespaces) for word in text.split() )
        else:
          raise NotImplementedError('unimplemented macro modifier "%s"' % (m,))

      return text

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
