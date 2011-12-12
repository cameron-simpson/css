#!/usr/bin/python
#
# Parse the $x and $(x) syntaxes.
#       - Cameron Simpson <cs@zip.com.au> 12dec2011
#

import re
import unittest
from .lex import ParseError, FileContext

RE_WHITESPACE = re.compile( r'\s+' )
RE_PLAINTEXT = re.compile( r'[^\s$]+' )

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
      M, offset = parseMacro(context, offset)
      permutations.append(M)
    elif ch.isspace():
      wh_offset = offset
      offset += 1
      while offset < len(s) and s[offset].isspace():
        offset += 1
      permutations.append(s[wh_offset:offset])
    else:
      m = re_plaintext.match(s, offset)
      permutations.append(m.group())
      offset = m.end()

  return permutations, offset

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

    return Macro(context, mtext, modifiers, mpermute), offset

  # $x
  ch = s[offset]
  if ch == '_' or ch.isalnum() or ch in SPECIAL_MACROS:
    offset += 1
    return Macro(context, ch), offset

  raise ParseError(context, offset, 'unknown special macro name "%s"' % (ch,))

class Macro(object):
  ''' A macro.
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
                             ''.join(modifiers)
                             ( '))' if self.permute else ')' ),
                           )

class TestAll(unittest.TestCase):

  def setUp(self):
    from .lex import FileContext
    self.context = FileContext('<unittest>', 1, None, '<dummy-text>')

  def tearDown(self):
    pass

  def test00parseMacro(self):
    M, offset = parseMacro('$x')
    self.assertEqual(offset, 2)
    self.assertEqual(str(M), '$x')
    M, offset = parseMacro('$.')
    self.assertEqual(offset, 2)
    self.assertEqual(str(M), '$.')

  def test10parsePlainText(self):
    self.assertEquals(parseMacroExpression(''),  ([], 0))
    self.assertEquals(parseMacroExpression('x'), (['x'], 1))
    self.assertEquals(parseMacroExpression(' '), ([' '], 1))
    self.assertEquals(parseMacroExpression('x y'), (['x', ' ', 'y'], 3))
    self.assertEquals(parseMacroExpression('abc  xyz'), (['abc', '  ', 'xyz'], 8))

if __name__ == '__main__':
  unittest.main()
