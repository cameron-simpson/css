#!/usr/bin/python
#

import sys
import glob
from collections import namedtuple
from itertools import chain, product
import os
import os.path
from os.path import dirname, realpath, isabs
import re
from string import whitespace, letters, digits
import unittest
from cs.lex import get_chars, get_other_chars, get_white, get_identifier
from cs.logutils import Pfx, error, info, debug, exception

# mapping of special macro names to evaluation functions
SPECIAL_MACROS = { '.':         lambda c, ns: os.getcwd(),       # TODO: cache
                   '$':         lambda c, ns: '$',
                 }

TARGET_MACROS = "@?/"

# macro assignment, including leading whitespace
#
# match
#  identifier =
# or
#  identifier(param[,param...]) =
#
re_identifier = r'[A-Za-z]\w*'
re_assignment = r'^\s*(' + re_identifier + r')\s*(\(\s*(' + re_identifier + r'(\s*,\s*' + re_identifier + r')*)\s*\)\s*)?='
RE_ASSIGNMENT = re.compile( re_assignment )

RE_COMMASEP = re.compile( r'\s*,\s*' )

_FileContext = namedtuple('FileContext', 'filename lineno text parent')
class FileContext(_FileContext):
  def __init__(self, filename, lineno, text, parent):
    assert type(filename) is str, "filename should be str, got %s" % (type(filename),)
    assert type(lineno) is int, "lineno should be int, got %s" % (type(lineno),)
    assert type(text) is str, "text should be str, got %s" % (type(text),)
    if parent is not None:
      assert type(parent) is FileContext, "parent should be FileContext, got %s" % (type(parent),)
    _FileContext.__init__(self, filename, lineno, text, parent)
    
  def __str__(self):
    tag = "%s:%d" % (self.filename, self.lineno)
    if self.parent:
      tag = str(parent) + ": " + tag
    return tag

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

def nsget(namespaces, mname):
  for ns in namespaces:
    M = ns.get(mname)
    if M:
      if type(M) is str:
        return lambda c, ns: M
      return M
  print >>sys.stderr, "not found: %s\nnamespaces =\n  %s" % (mname, nsstr(namespaces),)
  return None

def nsstr(namespaces):
  return \
    "\n  ".join( "{ %s\n  }" % ( ",\n    ".join( "%s: %s" % (k, ns[k])
                                                 for k in sorted(ns.keys())
                                               ), )
                 for ns in namespaces
               )

class Macro(object):
  ''' A macro definition.
  '''

  def __init__(self, context, name, params, text):
    ''' Initialise macro definition.
        `context`: source context.
        `name`: macro name.
        `params`: list of paramater names.
        `text`: replacement text, unparsed.
    '''
    self.context = context
    self.name = name
    self.params = params
    self.text = text
    self._mexpr = None

  def __str__(self):
    if self.params:
      return "$(%s(%s))" % (self.name, ",".join(self.params), )
    return ( "$(%s)" if len(self.name) > 1 else "$%s" ) % (self.name,)

  __repr__ = __str__

  @property
  def mexpr(self):
    if self._mexpr is None:
      self._mexpr, offset = parseMacroExpression(self.context, self.text)
      assert offset == len(self.text)
    return self._mexpr

  def __call__(self, context, namespaces, *param_values):
    assert type(namespaces) is list, "namespaces = %r" % (namespaces,)
    if len(param_values) != len(self.params):
      raise ValueError(
              "mismatched Macro parameters: self.params = %r (%d items) but got %d param_values: %r"
              % (self.params, len(self.params), len(param_values), param_values))
    if self.params:
      namespaces = [ dict(zip(self.params, param_values)) ] + namespaces
    return self.mexpr(context, namespaces)

def readMakefileLines(M, fp, parent_context=None):
  ''' Read a Mykefile and yield text lines.
      This generator parses slosh extensions and
      :if/ifdef/ifndef/else/endif directives.

  '''
  if type(fp) is str:
    # open file, yield contents
    filename = fp
    with open(filename) as fp:
      for O in readMakefileLines(M, fp, parent_context):
        yield O
    return

  try:
    filename = fp.name
  except AttributeError:
    filename = str(fp)

  ifStack = []        # active ifStates (state, in-first-branch)
  context = None      # FileContext(filename, lineno, line)

  lineno = 0
  prevline = None
  for line in fp:
    lineno += 1
    if not line.endswith('\n'):
      raise ParseError(context, len(line), '%s:%d: unexpected EOF (missing final newline)' % (filename, lineno))

    if prevline is not None:
      # prepend previous continuation line if any
      line = prevline + '\n' + line
      prevline = None
    else:
      # start of line - new context
      context = FileContext(filename, lineno, line.rstrip(), parent_context)

    with Pfx(str(context)):
      if line.endswith('\\\n'):
        # continuation line - gather next line before parse
        prevline = line[:-2]
        continue

      # skip blank lines and comments
      w1 = line.lstrip()
      if not w1 or w1.startswith('#'):
        continue

      try:
        # look for :if etc
        if line.startswith(':'):
          # top level directive
          _, offset = get_white(line, 1)
          word, offset = get_identifier(line, offset)
          if not word:
            raise SyntaxError("missing directive name")
          with Pfx(word):
            if word == 'ifdef':
              _, offset = get_white(line, offset)
              mname, offset = get_identifier(line, offset)
              if not mname:
                raise ParseError(context, offset, "missing macro name")
              _, offset = get_white(line, offset)
              if offset < len(line):
                raise ParseError(context, offset, "extra arguments after macro name: %s" % (line[offset:],))
              newIfState = [ False, True ]
              if all( [ item[0] for item in ifStack ] ):
                newIfState[0] = nsget(M.namespaces, mname) is not None
              ifStack.append(newIfState)
              continue
            if word == "ifndef":
              _, offset = get_white(line, offset)
              mname, offset = get_identifier(line, offset)
              if not mname:
                raise ParseError(context, offset, "missing macro name")
              _, offset = get_white(line, offset)
              if offset < len(line):
                raise ParseError(context, offset, "extra arguments after macro name: %s" % (line[offset:],))
              newIfState = [ True, True ]
              if all( [ item[0] for item in ifStack ] ):
                newIfState[0] = nsget(M.namespaces, mname) is None
              ifStack.append(newIfState)
              continue
            if word == "if":
              raise ParseError(context, offset, "\":if\" not yet implemented")
              continue
            if word == "else":
              if not ifStack[-1][1]:
                raise ParseError(context, 0, ":else inside :else")
              else:
                ifStack[-1][1] = False
            if word == "endif":
              if not ifStack:
                raise ParseError(context, 0, ":endif: no active :if directives in this file")
              ifStack.pop()
              continue

        if not all( [ item[0] for item in ifStack ] ):
          # in false branch of "if"; skip line
          continue

      except SyntaxError, e:
        error(e)
        continue

      yield context, line

  if prevline is not None:
    # incomplete continuation line
    error("unexpected EOF: unterminated slosh continued line")

  if ifStack:
    raise SyntaxError("%s: EOF with open :if directives" % (filename,))

def parseMakefile(M, fp, parent_context=None):
  ''' Read a Mykefile and yield Macros and Targets.
  '''
  from .make import Target, Action
  action_list = None       # not in a target
  for context, line in readMakefileLines(M, fp, parent_context=parent_context):
    with Pfx(str(context)):
      try:
        if line.startswith(':'):
          # top level directive
          _, doffset = get_white(line, 1)
          word, offset = get_identifier(line, doffset)
          if not word:
            raise ParseError("missing directive name")
          with Pfx(word):
            if word == 'append':
              mexpr, offset = parseMacroExpression(context, line, offset)
              assert offset == len(line)
              for include_file in mexpr(context, M.namespaces).split():
                if include_file:
                  if not os.path.isabs(include_file):
                    include_file = os.path.join( realpath(dirname(filename)), include_file )
                  M.add_appendfile(include_file)
              continue
            if word == 'import':
              ok = True
              for envvar in line[offset:].split():
                if envvar:
                  envvalue = os.environ.get(envvar)
                  if envvalue is None:
                    error("no $%s" % (envvar,))
                    ok = False
                  else:
                    yield Macro(context, envvar, (), envvalue.replace('$', '$$'))
              if not ok:
                raise ValueError("missing environment variables")
              continue
            raise ParseError(context, doffset, "unrecognised directive")

        if action_list is not None:
          # currently collating a Target
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
          macro_name = m.group(1)
          params_text = m.group(3)
          param_names = RE_COMMASEP.split(params_text) if params_text else ()
          macro_text = line[m.end():].rstrip()
          yield Macro(context, macro_name, param_names, macro_text)
          continue

        # presumably a target definition
        # gather up the target as a macro expression
        target_mexpr, offset = parseMacroExpression(context, stopchars=':')
        if offset >= len(context.text):
          print >>sys.stderr, "\n\noffset = %d\ncontext.text = [%s]\n\n" % (offset, context.text)
        if context.text[offset] != ':':
          raise ParseError(context, offset, "no colon in target definition")
        prereqs_mexpr, offset = parseMacroExpression(context, offset=offset+1, stopchars=':')
        if offset < len(context.text) and context.text[offset] == ':':
          postprereqs_mexpr, offset = parseMacroExpression(context, offset=offset+1)
        else:
          postprereqs_mexpr = []

        action_list = []
        for target in target_mexpr(context, M.namespaces).split():
          yield Target(target, context, prereqs=prereqs_mexpr, postprereqs=postprereqs_mexpr, actions=action_list)
        continue

        raise ParseError(context, 0, 'unparsed line')
      except ParseError, e:
        exception("%s", e)

  M.debug_parse("finish parse")

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
  ''' A MacroExpression represents a piece of text into which macro
      substitution is to occur.
  '''

  def __init__(self, context, permutations):
    self.context = context
    self.permutations = permutations
    self._result = None

  def __str__(self):
    return repr(self.permutations)

  __repr__ = __str__

  def __eq__(self, other):
    return other == self.permutations

  def __ne__(self, other):
    return not self == other

  __hash__ = None

  def __call__(self, context, namespaces):
    assert type(namespaces) is list, "namespaces = %r" % (namespaces,)
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
        mterm = item
        assert isinstance(mterm, MacroTerm), "expected MacroTerm, got %r" % (mterm,)
        if wordlists is not None and len(wordlists) == 0:
          # word already short circuited - skip evaluating the MacroTerm
          pass
        else:
          text = mterm(context, namespaces, mterm.params)
          if mterm.permute:
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

SIMPLE_MODIFIERS = 'DEG?Fv?<?'

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
    if ch == '_' or ch.isalpha() or ch in SPECIAL_MACROS or ch in TARGET_MACROS:
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
      # must be "qtext" or a special macro name
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
      elif q in SPECIAL_MACROS or q in TARGET_MACROS:
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
          modifier = ch
          offset += 1
          if offset < len(text) and text[offset] == '?':
            if pos >= len(SIMPLE_MODIFIERS) or SIMPLE_MODIFIERS[pos+1] != '?':
              raise ParseError(context, offset, 'modifier "%s" does not accept a query "?"' % (ch,))
            modifier += '?'
            offset += 1
          modifiers.append(modifier)
          continue

        if ch in 'PpSs':
          modifier = ch
          offset += 1
          sep = '.'
          if offset < len(text) and text[offset] == '[':
            # [x] separator specification
            offset += 1
            if offset >= len(text):
              raise ParseError(context, offset, 'incomplete %s[sep] modifier - missing separator' % (modifier,))
            sep = text[offset]
            offset += 1
            if offset >= len(text) or text[offset] != ']':
              raise ParseError(context, offset, 'incomplete %s[sep] modifier - missing ]' % (modifier,))
            offset += 1
          modifiers.append(modifier + sep)
          continue

        if ch == '-':
          modifier = '-'
          moffset = offset
          _, offset = get_white(text, offset+1)
          submname, offset = get_identifier(text, offset)
          if not submname:
            raise ParseError(context, moffset, 'missing macro name after "-" modifier')
          modifiers.append(modifier+submname)
          continue

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

    print >>sys.stderr, "new MacroTerm: mtext=<%s>" % (mtext,)
    M = MacroTerm(context, mtext, modifiers, param_mexprs, permute=mpermute, literal=mliteral), offset
    return M

  except IndexError, e:
    raise ParseError(context, offset, 'parse incomplete, offset=%d, remainder: %s' % (offset, text[offset:]))

  raise ParseError(context, offset, 'unhandled parse failure at offset %d: %s' % (offset, text[offset:]))

class MacroTerm(object):
  ''' A macro reference such as $x or $(foo(a,b,c) xyz).
  '''

  def __init__(self, context, text, modifiers='', params=(), permute=False, literal=False):
    ''' Initialise a MacroTerm.
        `context`: source context.
        `text`: macro name.
        `modifiers`: list of modifier terms eg: ['P', 'G?',]
        `params`: list of parameter MacroExpressions.
        `permute`: whether inside $((...)).
        `literal`: if true, text is just text, not a macro name.
    '''
    if literal:
      if params:
        raise ValueError(
              "no paramaters permitted with a literal MacroTerm: params=%r"
                % (params,))
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

  def __call__(self, context, namespaces, param_mexprs):
    ''' Evaluate a macro expression.
    '''
    assert type(namespaces) is list, "namespaces = %r" % (namespaces,)
    with Pfx(str(self.context)):
      text = self.text
      if not self.literal:
        macro = nsget(namespaces, text)
        if macro is None:
          raise ValueError("unknown macro: $(%s)" % (text,))
        param_values = [ mexpr(context, namespaces) for mexpr in param_mexprs ]
        text = macro(context, namespaces, *param_values)

      for modifier in self.modifiers:
        with Pfx(modifier):
          if modifier == 'E':
            mexpr, offset = parseMacroExpression(self.context, text)
            assert offset == len(text)
            text = mexpr(context, namespaces)
          elif modifier == 'D':
            text = " ".join( os.path.dirname(s) for s in text.split() if len(s) )
          elif modifier == 'F':
            text = " ".join( os.path.basename(s) for s in text.split() if len(s) )
          elif modifier == 'G' or modifier == 'G?':
            lax = modifier == 'G?'
            globs = []
            for ptn in text.split():
              if len(ptn):
                with Pfx("glob(\"%s\")" % (ptn,)):
                  globbed = glob.glob(ptn)
                  if globbed:
                    globs.append(globbed)
                  else:
                    if not lax:
                      raise ValueError("no matches")
            text = " ".join(chain(globbed))
          elif modifier[0] in 'PpSs':
            mode, sep = modifier
            with Pfx("\"%s\"" % (text,)):
              if mode == 'P':
                text, _ = text.rsplit(sep, 1)
              elif mode == 'p':
                text, _ = text.split(sep, 1)
              elif mode == 'S':
                _, text = text.rsplit(sep, 1)
              elif mode == 's':
                _, text = text.split(sep, 1)
              else:
                raise NotImplementedError('unimplemented macro modifier')
          elif modifier == 'v':
            # TODO: accept lax?
            text = ' '.join( MacroTerm(context, word)(context, namespaces) for word in text.split() )
          elif modifier.startswith('-'):
            submname = modifier[1:]
            M = nsget(namespaces, submname)
            if not M:
              raise ValueError("no macro $(%s)" % (submname,))
            subwords = set( subword for subword in M(context, namespaces).split() if subword )
            text = " ".join( word for word in text.split() if word and word not in subwords )
          else:
            raise NotImplementedError('unimplemented macro modifier')

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
    self.assertEquals(parseMacroExpression(' '), ([], 1))
    self.assertEquals(parseMacroExpression('x y'), (['x', ' ', 'y'], 3))
    self.assertEquals(parseMacroExpression('abc  xyz'), (['abc', '  ', 'xyz'], 8))

  def test20parseMakeLines(self):
    from StringIO import StringIO
    from .make import Maker
    M = Maker()
    parsed = list(parseMakefile(M, StringIO("abc = def\n"), []))
    self.assertEquals(len(parsed), 1)
    self.assertEquals([ type(O) for O in parsed ], [ Macro ])

if __name__ == '__main__':
  unittest.main()
