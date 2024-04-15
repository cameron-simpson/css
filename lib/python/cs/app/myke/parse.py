#!/usr/bin/env python3
#

''' Makefile parsing functions.
'''

from dataclasses import dataclass
import errno
from functools import cached_property
import glob
from collections import namedtuple
from io import StringIO
from itertools import product
import os
from os.path import (
    basename,
    dirname,
    isabs as isabspath,
    join as joinpath,
    normpath,
)
import re
from string import whitespace
from typing import List, Optional, Tuple, Union
import unittest

from cs.deco import strable
from cs.lex import get_other_chars, get_white, get_identifier, r
from cs.logutils import error, warning, debug
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.py.func import prop

# mapping of special macro names to evaluation functions
SPECIAL_MACROS = {
    '.': lambda c, ns: os.getcwd(),  # TODO: cache
    '$': lambda c, ns: '$',
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
re_assignment = "".join(
    (
        r'^\s*(',
        re_identifier,
        r')\s*(\(\s*(',
        re_identifier,
        r'(\s*,\s*',
        re_identifier,
        r')*)\s*\)\s*)?=',
    )
)
RE_ASSIGNMENT = re.compile(re_assignment)

RE_COMMASEP = re.compile(r'\s*,\s*')

if sys.version_info >= (3, 10):
  _fcdc = dataclass(frozen=True, slots=True)
else:
  _fcdc = dataclass(frozen=True)

@_fcdc
class FileContext(HasThreadState):
  ''' Context information for parse objects and exceptions.
  '''
  filename: str
  lineno: int
  text: str
  parent: Optional["FileContext"] = None

  def __str__(self):
    tag = f'{self.filename}:{self.lineno}'
    if self.parent:
      tag = f'{self.parent}::{tag}'
    return tag

class ParseError(SyntaxError):
  ''' A ParseError subclasses SyntaxError in order to change the initialiser.
      This object has an additional attribute .context for the relevant FileContext
      (which has a .parent attribute).
  '''

  def __init__(self, context, offset, message, *a):
    ''' Initialise a ParseError given a FileContext and the offset into `context.text`.
        Accept optional arguments `*a` after the `message`; if supplied these
        are embedded into `message` with %-formatting.
    '''
    if a:
      message = message % a
    self.msg = message
    self.filename = context.filename
    self.lineno = context.lineno
    self.text = context.text
    self.offset = offset
    self.context = context

def nsget(namespaces, mname):
  ''' Look up `macro name `mname` in `namespaces`.
  '''
  for ns in namespaces:
    M = ns.get(mname)
    if M:
      if isinstance(M, str):
        return lambda c, ns: M
      return M
  return None

def nsstr(namespaces):
  ''' Transcribe a list of namespaces.
  '''
  return "\n  ".join(
      "{ %s\n  }" %
      (",\n    ".join("%s: %s" % (k, ns[k])
                      for k in sorted(ns.keys())),)
      for ns in namespaces
  )

class Modifier(object):
  ''' Base class for modifiers.
  '''

  def __init__(self, context, modtext):
    self.context = context
    self.modtext = modtext

  def __str__(self):
    return "<Mod %s>" % (self.modtext,)

  def __call__(self, text, namespaces):
    with Pfx("%r %s", text, self):
      ntext = self.modify(text, namespaces)
      ##info("%r -> %r", text, ntext)
    return ntext

  def words(self, text):
    return [word for word in text.split() if len(word) > 0]

  def foreach(self, text, f):
    ''' Apply the operator function `f` to each word in `text`.
        Return the results joined together with spaces.
    '''
    return " ".join(
        [word for word in map(f, self.words(text)) if len(word) > 0]
    )

class ModDirpart(Modifier):
  ''' A modifier to get the directory part of a filename.
  '''

  @pfx_method
  def modify(self, text, namespaces):
    return self.foreach(text, dirname)

class ModFilepart(Modifier):
  ''' A modifier to get the file part of a filename.
  '''

  @pfx_method
  def modify(self, text, namespaces):
    return self.foreach(text, basename)

class ModifierSplit1(Modifier):
  ''' A modifier that splits a word on a separator and returns one half.
  '''

  def __init__(self, context, modtext, separator, keepright, rightmost):
    Modifier.__init__(self, context, modtext)
    self.separator = separator
    self.keepright = keepright
    self.rightmost = rightmost

  def splitword(self, word):
    sep = self.separator
    keepright = self.keepright
    right = self.rightmost
    return (word.rsplit(sep, 1)
            if right else word.split(sep, 1))[1 if keepright else 0]

  @pfx_method
  def modify(self, text, namespaces):
    return self.foreach(text, self.splitword)

class ModPrefixLong(ModifierSplit1):
  ''' A modifier that splits on the last separator and returns the prefix.
  '''

  def __init__(self, context, modtext, separator):
    ModifierSplit1.__init__(self, context, modtext, separator, False, True)

class ModPrefixShort(ModifierSplit1):
  ''' A modifier that splits on the first separator and returns the prefix.
  '''

  def __init__(self, context, modtext, separator):
    ModifierSplit1.__init__(self, context, modtext, separator, False, False)

class ModSuffixLong(ModifierSplit1):
  ''' A modifier that splits on the first separator and returns the suffix.
  '''

  def __init__(self, context, modtext, separator):
    ModifierSplit1.__init__(self, context, modtext, separator, True, False)

class ModSuffixShort(ModifierSplit1):
  ''' A modifier that splits on the last separator and returns the suffix.
  '''

  def __init__(self, context, modtext, separator):
    ModifierSplit1.__init__(self, context, modtext, separator, True, True)

class ModUnique(Modifier):
  ''' A modifier which returns only the first occurence of each word in `text`.
  '''

  @pfx_method
  def modify(self, text, namespaces):
    ''' Return the unique words.
    '''
    seen = set()
    words = []
    for word in self.words(text):
      if word not in seen:
        seen.add(word)
        words.append(word)
    return " ".join(words)

class ModNormpath(Modifier):
  ''' A modifier which returns `normpath(word)` for each `word` in `text`.
  '''

  def modify(self, text, namespaces):
    ''' Normalise the path `text`.
    '''
    return self.foreach(normpath)

class ModGlob(Modifier):
  ''' A modifier which returns each word of `text` replaced by its glob match.
  '''

  def __init__(self, context, modtext, muststat, lax):
    Modifier.__init__(self, context, modtext)
    self.muststat = muststat
    self.lax = lax

  def modify(self, text, namespaces):
    ''' Expand the glob.
    '''
    globbed = []
    for ptn in self.words(text):
      with Pfx("%r G", ptn):
        matches = pfx_call(glob.glob, ptn)
        if matches:
          if self.muststat:
            for match in matches:
              with Pfx(match):
                os.stat(match)
          globbed.extend(matches)
        else:
          if not self.lax:
            raise ValueError("no matches")
    return " ".join(sorted(globbed))

class ModEval(Modifier):
  ''' A modifier which evaluates text as a macro expression.
  '''

  def modify(self, text, namespaces):
    ''' Evaluate `text` as a macro expression.
    '''
    mexpr = MacroExpression.from_text(self.context, text)
    return mexpr(self.context, namespaces)

class ModFromFiles(Modifier):
  ''' Read file contents.
  '''

  def __init__(self, context, modtext, lax):
    Modifier.__init__(self, context, modtext)
    self.lax = lax

  def modify(self, text, namespaces):
    ''' Read file contents.
    '''
    newwords = []
    for filename in self.words(text):
      with Pfx(filename):
        try:
          with open(filename) as f:
            newwords.extend(self.words(f.read()))
        except IOError as e:
          if self.lax:
            warning("%s", e)
          raise
    return " ".join(newwords)

class ModSelectRegexp(Modifier):
  ''' A regular expression selector.
  '''

  def __init__(self, context, modtext, regexp_mexpr, invert):
    Modifier.__init__(self, context, modtext)
    self.regexp_mexpr = regexp_mexpr
    self.invert = bool(invert)

  def modify(self, text, namespaces):
    ''' Select by regular expression.
    '''
    invert = self.invert
    regexp = re.compile(self.regexp_mexpr(self.context, namespaces))
    f = lambda word: word if invert ^ bool(regexp.search(word)) else ''
    return self.foreach(text, f)

class ModSelectRange(Modifier):
  ''' A range selection `Modifier`.
  '''

  def __init__(self, context, modtext, select_range, invert):
    Modifier.__init__(self, context, modtext)
    self.range = select_range
    self.invert = bool(invert)

  def modify(self, text, namespaces):
    ''' Select the range.
    '''
    invert = self.invert
    select_range = self.range
    newwords = []
    i = 0
    for word in self.words(text):
      if (i in select_range) ^ invert:
        newwords.append(word)
    return " ".join(newwords)

## TODO: check against below ## class ModSubstitute(Modifier):
## TODO: check against below ##   ''' A modifier which returns `text` with substitutions.
## TODO: check against below ##   '''
## TODO: check against below ##
## TODO: check against below ##   def __init__(self, context, modtext, regexp_mexpr, replacement):
## TODO: check against below ##     Modifier.__init__(self, context, modtext)
## TODO: check against below ##     self.regexp_mexpr = regexp_mexpr
## TODO: check against below ##     self.replacement = replacement
## TODO: check against below ##
## TODO: check against below ##   @pfx_method
## TODO: check against below ##   def modify(self, text, namespaces):
## TODO: check against below ##     ''' Apply the substitution.
## TODO: check against below ##     '''
## TODO: check against below ##     return re.sub(
## TODO: check against below ##         regexp_mexpr(self.context, namespaces), self.replacement, text
## TODO: check against below ##     )

class ModSubstitute(Modifier):
  ''' A substituion `Modifier`.
  '''

  def __init__(self, context, modtext, ptn, repl):
    Modifier.__init__(self, context, modtext)
    self.ptn = ptn
    self.repl = repl

  @pfx_method
  def modify(self, text, namespaces):
    ''' Apply the substitution.
    '''
    regexp_mexpr = MacroExpression.from_text(self.context, text=self.ptn)
    return re.compile(regexp_mexpr(self.context,
                                   namespaces)).sub(self.repl, text)

class ModSetOp(Modifier):
  ''' A set operation `Modifier`.
  '''

  def __init__(self, context, modtext, op, macroname, literal):
    Modifier.__init__(self, context, modtext)
    self.op = op
    self.macroname = macroname
    self.literal = literal

  def modify(self, text, namespaces):
    ''' Apply the set operation.
    '''
    words = set(self.words(text))
    if self.literal:
      mtext = self.macroname
    else:
      mtext = nsget(namespaces, self.macroname)(self.context, namespaces)
    subwords = self.words(mtext)
    if self.op == '-':
      words.difference_update(subwords)
    elif self.op == '+':
      words.update(subwords)
    elif self.op == '*':
      words = words.intersection(subwords)
    else:
      raise NotImplementedError(f'unimplemented set op {self.op!r}')
    return " ".join(words)

@dataclass
class Macro:
  ''' A macro definition.
  '''
  context: FileContext
  name: str
  params: List[str]
  text: str

  def __str__(self):
    if self.params:
      return f'$({self.name}({",".join(self.params)})'
    if len(self.name) == 1:
      return f'${self.name}'
    return f'$({self.name})'

  __repr__ = __str__

  @classmethod
  def from_assignment(
      cls,
      context: FileContext,
      assignment_text: str,
  ) -> Union["Macro", None]:
    ''' Try to parse `assignment_text` as a macro definition.
        If it does not look like an assignment (does not match `RE_ASSIGNMENT`),
        return `None`.
        Otherwise return a `Macro`.
    '''
    with Pfx("%s.from_assignment(%r)", cls.__name__, assignment_text):
      m = RE_ASSIGNMENT.match(assignment_text)
      if not m:
        raise ValueError("does not match RE_ASSIGNMENT")
      macro_name = m.group(1)
      params_text = m.group(3)
      param_names = RE_COMMASEP.split(params_text) if params_text else ()
      macro_text = assignment_text[m.end():].rstrip()
      return cls(context, macro_name, param_names, macro_text)

  @cached_property
  def mexpr(self):
    ''' The parsed `MacroExpression`.
    '''
    mexpr, offset = MacroExpression.parse(self.context, self.text)
    assert offset == len(self.text)
    return mexpr

  def __call__(self, context, namespaces, *param_values):
    ''' Call the macro with namespaces and parameter values as keywork arguments.
    '''
    with Pfx(self):
      assert isinstance(namespaces, list)
      if len(param_values) != len(self.params):
        raise ValueError(
            "mismatched Macro parameters: self.params = %r (%d items) but got %d param_values: %r"
            % (self.params, len(self.params), len(param_values), param_values)
        )
      # provide any parameters as the leading namespace
      if self.params:
        namespaces = [dict(zip(self.params, param_values))] + namespaces
      return self.mexpr(context, namespaces)

def scan_makefile(
    M,
    f,
    parent_context=None,
    start_lineno=1,
    missing_ok=False,
):
  ''' Read a Mykefile and yield `(FileContext,str)` tuples.
      This generator parses slosh extensions and
      :if/ifdef/ifndef/else/endif directives.

  '''
  if isinstance(f, str):
    if start_lineno != 1:
      raise ValueError(
          "start_lineno must be 1 (the default) if f is a filename"
      )
    # open file, yield contents
    filename = f
    try:
      with pfx_call(open, filename) as f:
        yield from scan_makefile(M, f, parent_context, missing_ok=missing_ok)
    except OSError as e:
      if e.errno == errno.ENOENT and missing_ok:
        return
      raise
    return

  if missing_ok:
    raise ValueError("missing_ok may not be true unless f is a filename")

  try:
    filename = f.name
  except AttributeError:
    filename = str(f)

  ifStack = []  # active ifStates (state, in-first-branch)
  context = None  # FileContext(filename, lineno, line)

  prevline = None
  for lineno, line in enumerate(f, start_lineno):
    if not line.endswith('\n'):
      raise ParseError(
          context, len(line), '%s:%d: unexpected EOF (missing final newline)',
          filename, lineno
      )
    if prevline is not None:
      # prepend previous continuation line if any
      # keep the same FileContext
      line = prevline + '\n' + line
      prevline = None
    else:
      # start of line - new FileContext
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
          _, offset = get_white(line, offset)
          with Pfx(word):
            if word == 'ifdef':
              mname, offset = get_identifier(line, offset)
              if not mname:
                raise ParseError(context, offset, "missing macro name")
              _, offset = get_white(line, offset)
              if offset < len(line):
                raise ParseError(
                    context, offset, "extra arguments after macro name: %s",
                    line[offset:]
                )
              newIfState = [False, True]
              if all([item[0] for item in ifStack]):
                newIfState[0] = nsget(M.namespaces, mname) is not None
              ifStack.append(newIfState)
              continue
            if word == "ifndef":
              mname, offset = get_identifier(line, offset)
              if not mname:
                raise ParseError(context, offset, "missing macro name")
              _, offset = get_white(line, offset)
              if offset < len(line):
                raise ParseError(
                    context, offset, "extra arguments after macro name: %s",
                    line[offset:]
                )
              newIfState = [True, True]
              if all([item[0] for item in ifStack]):
                newIfState[0] = nsget(M.namespaces, mname) is None
              ifStack.append(newIfState)
              continue
            if word == "if":
              raise ParseError(context, offset, '":if" not yet implemented')
              continue
            if word == "else":
              # extra text permitted
              if not ifStack:
                raise ParseError(
                    context, 0, ":else: no active :if directives in this file"
                )
              if not ifStack[-1][1]:
                raise ParseError(context, 0, ":else inside :else")
              ifStack[-1][1] = False
              continue
            if word == "endif":
              # extra text permitted
              if not ifStack:
                raise ParseError(
                    context, 0, ":endif: no active :if directives in this file"
                )
              ifStack.pop()
              continue
            if word == "include":
              if all(ifState[0] for ifState in ifStack):
                if offset == len(line):
                  raise ParseError(
                      context, offset, ":include: no include files specified"
                  )
                include_mexpr = MacroExpression.from_text(
                    context, offset=offset
                )
                for include_file in include_mexpr(context,
                                                  M.namespaces).split():
                  if len(include_file) == 0:
                    continue
                  if not isabspath(include_file):
                    include_file = joinpath(dirname(filename), include_file)
                  yield from scan_makefile(
                      M,
                      include_file,
                      parent_context=context,
                  )
              continue
        if not all(ifState[0] for ifState in ifStack):
          # in false branch of "if"; skip line
          continue
      except SyntaxError as e:
        error(e)
        continue
    # NB: yield is outside the Pfx context manager because Pfx does
    # not play nicely with generators
    yield context, line

  if prevline is not None:
    # incomplete continuation line
    error("%s: unexpected EOF: unterminated slosh continued line")

  if ifStack:
    raise SyntaxError("%s: EOF with open :if directives" % (filename,))

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

  @classmethod
  def from_text(cls, context, text=None, offset=0, stopchars=''):
    ''' Obtain a `MacroExpression` from `text` or `context.text`.
    '''
    if text is None:
      text = context.text
    mexpr, offset = cls.parse(
        context, text=text, offset=offset, stopchars=stopchars
    )
    if offset != len(text):
      raise ValueError(
          "unparsed text after MacroExpression.from_text(%r): %r" %
          (text, text[offset:])
      )
    return mexpr

  @classmethod
  def parse(cls,
            context,
            text=None,
            offset=0,
            stopchars='') -> Tuple["MacroExpression", int]:
    ''' Parse a macro expression from `text` or `context.text` if `text` is `None`.
        Return `(MacroExpression,offset)`.

        A macro expression is a concatenation of permutations.
    '''
    if isinstance(context, str):
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
        plain, offset = get_other_chars(
            text, offset, stopchars + '$' + whitespace
        )
        if plain:
          permutations.append(plain)
        else:
          # end of parsable string
          break
    # drop trailing whitespace
    if permutations:
      last = permutations[-1]
      if isinstance(last, str) and last.isspace():
        permutations.pop()
    return cls(context, permutations), offset

  def __call__(self, context, namespaces):
    assert isinstance(namespaces, list)
    if self._result is not None:
      return self._result
    strs = []  # strings to collate
    wordlists = None  # accruing word permutations
    for item in self.permutations:
      if isinstance(item, str):
        if item.isspace():
          # whitespace - end of word
          # stash existing word if any
          if wordlists:
            # non-empty word accruing
            words = [''.join(wordlists) for wordlists in product(*wordlists)]
            strs.append(" ".join(words))
            wordlists = None
          strs.append(item)
        else:
          # word
          if wordlists is None:
            wordlists = [[item]]
          else:
            if wordlists:
              wordlists.append([item])
      else:
        # should be a MacroTerm
        mterm = item
        assert isinstance(mterm,
                          MacroTerm), "expected MacroTerm, got %r" % (mterm,)
        if wordlists is not None and not wordlists:
          # word already short circuited - skip evaluating the MacroTerm
          pass
        else:
          text = mterm(context, namespaces, mterm.params)
          if mterm.permute:
            textwords = text.split()
            if not textwords:
              # short circuit
              wordlists = []
            else:
              if wordlists is None:
                wordlists = [textwords]
              else:
                wordlists.append(textwords)
          else:
            if wordlists is None:
              wordlists = [[text]]
            else:
              wordlists.append([text])

    if wordlists:
      # non-empty word accruing - flatten it and store
      strs.append(
          " ".join([''.join(wordlist) for wordlist in product(*wordlists)])
      )

    result = ''.join(strs)
    debug("eval returns %r", result)
    return result

SIMPLE_MODIFIERS = 'DEG?Fv?<?'

@strable(open_func=lambda context: FileContext('<string>', 1, context, None))
def parseMacro(context, text=None, offset=0):
  ''' Parse macro from `text` from `FileContext` `context` at `offset`.
      Return `(MacroTerm,offset)`.
  '''
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
    if ch == '_' or ch.isalpha(
    ) or ch in SPECIAL_MACROS or ch in TARGET_MACROS:
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
      raise ParseError(context, offset, 'invalid special macro "%s"', ch)

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
          mexpr, offset = MacroExpression.parse(
              context, text=text, offset=offset, stopchars=',)'
          )
          param_mexprs.append(mexpr)
          _, offset = get_white(text, offset)
          if text[offset] == ',':
            # gather comma and following whitespace
            _, offset = get_white(text, offset + 1)
            continue
          if text[offset] != ')':
            raise ParseError(
                context, offset,
                'macro parameters: expected comma or closing parenthesis, found: %s',
                text[offset:]
            )
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
        raise ParseError(context, offset, 'unknown special macro name "%s"', q)

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
          raise ParseError(
              context, offset, 'bare query "?" found in modifiers at: %s',
              text[offset:]
          )

        mod0 = ch
        modargs = ()
        with Pfx(mod0):
          offset0 = offset
          offset += 1

          if mod0 == 'D':
            modclass = ModDirpart
          elif mod0 == 'E':
            modclass = ModEval
          elif mod0 == 'F':
            modclass = ModFilepart
          elif mod0 == 'G':
            modclass = ModGlob
            if offset < len(text) and text[offset] == '?':
              offset += 1
              modargs = (
                  False,
                  True,
              )
            else:
              modargs = (
                  False,
                  False,
              )
          elif mod0 == 'g':
            modclass = ModGlob
            if offset < len(text) and text[offset] == '?':
              offset += 1
              modargs = (
                  True,
                  True,
              )
            else:
              modargs = (
                  True,
                  False,
              )
          elif mod0 in 'PpSs':
            if offset < len(text) and text[offset] == '[':
              offset += 1
              if offset >= len(text):
                raise ParseError(context, offset, 'missing separator')
              sep = text[offset]
              offset += 1
              if offset >= len(text) or text[offset] != ']':
                raise ParseError(context, offset, 'missing closing "]"')
              offset += 1
            else:
              sep = '.'
            modargs = (sep,)
            if mod0 == 'P':
              modclass = ModPrefixLong
            elif mod0 == 'p':
              modclass = ModPrefixShort
            elif mod0 == 'S':
              modclass = ModSuffixShort
            elif mod0 == 's':
              modclass = ModSuffixLong
            else:
              raise NotImplementedError(
                  f'parse error: unhandled PpSs letter {mod0!r}'
              )
          elif mod0 == '<':
            modclass = ModFromFiles
            if offset < len(text) and text[offset] == '?':
              offset += 1
              modargs = (True,)
            else:
              modargs = (False,)
          elif mod0 in '-+*':
            modclass = ModSetOp
            _, offset = get_white(text, offset)
            q = text[offset:offset + 1]
            if q == '"' or q == "'":
              # 'qstr'
              offset += 1
              text_offset = offset
              while text[offset] != q:
                offset += 1
              mtext = text[text_offset:offset]
              offset += 1
              modargs = (mod0, mtext, True)
            else:
              submname, offset = get_identifier(text, offset)
              if not submname:
                raise ParseError(
                    context, offset,
                    'missing macro name or string after "%s" modifier', mod0
                )
              modargs = (mod0, submname, False)
          elif mod0 == ':':
            _, offset = get_white(text, offset)
            if offset >= len(text):
              raise ParseError(
                  context, offset, 'missing opening delimiter in :,ptn,rep,'
              )
            delim = text[offset]
            if delim == mmark2:
              raise ParseError(
                  context, offset,
                  'found closing bracket instead of leading delimiter in :,ptn,rep,'
              )
            if delim.isalnum():
              raise ParseError(
                  context, offset,
                  'invalid delimiter in :,ptn,rep, - must be nonalphanumeric'
              )
            modclass = ModSubstitute
            offset += 1
            try:
              ptn, repl, etc = text[offset:].split(delim, 2)
            except ValueError as e:
              raise ParseError(
                  context, offset, 'incomplete :%sptn%srep%s', delim, delim,
                  delim
              ) from e
            offset = len(text) - len(etc)
            modargs = (ptn, repl)
          else:
            invert = False
            if ch == '!':
              invert = True
              # !/regexp/ or !{commalist}?
              _, offset2 = get_white(text, offset)
              if offset2 == len(text) or text[offset2] not in '/{':
                raise ParseError(
                    context, offset2,
                    '"!" not followed by /regexp/ or {comma-list} at %r',
                    text[offset2:]
                )
              offset = offset2
              ch = text[offset]

            if ch == '/':
              modclass = ModSelectRegexp
              offset += 1
              mexpr, end = MacroExpression.parse(
                  context, text=text, offset=offset, stopchars='/'
              )
              if end >= len(text):
                raise ParseError(
                    context, offset, 'incomplete /regexp/: %r', text[offset:]
                )
              assert text[end] == '/'
              offset = end + 1
              modargs = (mexpr, invert)
            else:
              raise ParseError(
                  context, offset0, 'unknown macro modifier "%s": "%s"', mod0,
                  text[offset0:]
              )

          modifiers.append(modclass(context, text[offset0:offset], *modargs))

      except ParseError as e:
        error("%s", e)
        offset += 1

    assert ch == mmark2, "should be at %r, but am at: %s" % (
        mmark, text[offset:]
    )
    offset += 1
    if mpermute:
      if offset >= len(text) or text[offset] != mmark2:
        raise ParseError(context, offset, 'incomplete macro closing brackets')
      else:
        offset += 1

    M = MacroTerm(
        context,
        mtext,
        modifiers,
        param_mexprs,
        permute=mpermute,
        literal=mliteral
    )
    return M, offset

  except IndexError as e:
    raise ParseError(
        context, offset, 'parse incomplete, offset=%d, remainder: %s', offset,
        text[offset:]
    ) from e

  raise ParseError(
      context, offset, 'unhandled parse failure at offset %d: %s', offset,
      text[offset:]
  )

class MacroTerm(object):
  ''' A macro reference such as $x or $(foo(a,b,c) xyz).
  '''

  def __init__(
      self,
      context,
      text,
      modifiers='',
      params=(),
      permute=False,
      literal=False
  ):
    ''' Initialise a MacroTerm.

        Parameters:
        * `context`: source context.
        * `text`: macro name.
        * `modifiers`: list of modifier terms eg: `['P','G?']`
        * `params`: list of parameter `MacroExpression`s.
        * `permute`: whether inside `$((`*...*`))`.
        * `literal`: if true, text is just text, not a macro name.
    '''
    if literal:
      if params:
        raise ValueError(
            "no parameters permitted with a literal MacroTerm: params=%r" %
            (params,)
        )
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
    return '$%s%s%s%s%s' % (
        ('((' if self.permute else '('),
        ('"%s"' % (self.text,) if self.literal else self.text),
        (' ' if self.modifiers else ''),
        ''.join(str(mod) for mod in self.modifiers),
        ('))' if self.permute else ')'),
    )

  __repr__ = __str__

  def __call__(self, context, namespaces, param_mexprs):
    ''' Evaluate a macro expression.
    '''
    assert isinstance(namespaces, list)
    with Pfx(self.context):
      text = self.text
      if not self.literal:
        macro = nsget(namespaces, text)
        if macro is None:
          raise ValueError("unknown macro: $(%s)" % (text,))
        param_values = [mexpr(context, namespaces) for mexpr in param_mexprs]
        text = macro(context, namespaces, *param_values)
      for modifier in self.modifiers:
        with Pfx("%r %s", text, modifier):
          text = modifier(text, namespaces)
      return text

class TestAll(unittest.TestCase):
  ''' Parsing unit tests.
  '''

  def test00parseMacro(self):
    ''' Basic $foo macro tests.
    '''
    M, offset = parseMacro('$x')
    self.assertEqual(offset, 2)
    self.assertEqual(str(M), '$x')
    M, offset = parseMacro('$.')
    self.assertEqual(offset, 2)
    self.assertEqual(str(M), '$.')

  def test10parseMacroExpr_PlainText(self):
    ''' Plain text parse.
    '''
    self.assertEqual(MacroExpression.parse(''), ([], 0))
    self.assertEqual(MacroExpression.parse('x'), (['x'], 1))
    self.assertEqual(MacroExpression.parse(' '), ([], 1))
    self.assertEqual(MacroExpression.parse('x y'), (['x', ' ', 'y'], 3))
    self.assertEqual(
        MacroExpression.parse('abc  xyz'), (['abc', '  ', 'xyz'], 8)
    )

  def test20parseMakeLines(self):
    ''' Test parse of Mykefile.
    '''
    from .make import Maker
    with Maker("myke") as M:
      parsed = list(M.parse(StringIO("abc = def\n")))
      self.assertEqual(len(parsed), 1)
      self.assertEqual([type(obj) for obj in parsed], [Macro])

if __name__ == '__main__':
  unittest.main()
