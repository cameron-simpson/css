#!/usr/bin/python
#
# Self tests for cs.lex.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Self tests for cs.lex.
'''

import sys
from functools import partial
import unittest
from cs.lex import texthexify, untexthexify, \
                   get_envvar, \
                   get_sloshed_text, SLOSH_CHARMAP, \
                   get_qstr, \
                   get_identifier, get_dotted_identifier
from cs.py3 import bytes

##from cs.logutils import X

class TestLex(unittest.TestCase):
  ''' Unit tests for cs.lex.
  '''

  def setUp(self):
    ''' Test seup.
    '''
    self.env = {'A': 'AA', 'B1': 'BB1'}
    self.env_specials = {'!': '99'}

  def tearDown(self):
    ''' Undo test setup.
    '''

  def test00untexthexify(self):
    ''' Test untexthexify.
    '''
    self.assertEqual(b'', untexthexify(''))
    self.assertEqual(b'A', untexthexify('41'))
    self.assertEqual(b'ABC', untexthexify('41[BC]'))
    self.assertRaises(ValueError, untexthexify, 'a')

  def test01texthexify(self):
    ''' Test texthexify.
    '''
    self.assertEqual('', texthexify(b''))
    self.assertEqual('00', texthexify(bytes((0x00,))))

  def test02get_envvar(self):
    ''' Test get_envvar.
    '''
    self.assertEqual(get_envvar('$!', specials=self.env_specials), ('99', 2))
    for envvar in self.env:
      envval, offset = get_envvar('$' + envvar, 0, self.env)
      self.assertEqual(
          envval, self.env[envvar], "get_envvar($%s) ==> %r, expected %r" %
          (envvar, envval, self.env[envvar])
      )
      self.assertEqual(offset, len(envvar) + 1)
    self.assertEqual(get_envvar('$x', environ={}, default=''), ('', 2))
    self.assertRaises(ValueError, get_envvar, '$x', environ={})

  def test03get_sloshed_text(self):
    ''' Test get_sloshed_text.
    '''
    self.assertRaises(ValueError, get_sloshed_text, '\\', None)
    self.assertRaises(ValueError, get_sloshed_text, '', '"')
    self.assertRaises(ValueError, get_sloshed_text, '\\', '"')
    self.assertRaises(ValueError, get_sloshed_text, '\\"', '"')
    self.assertRaises(ValueError, get_sloshed_text, '\\x0', '"')
    self.assertRaises(ValueError, get_sloshed_text, '\\x0zz', '"')
    self.assertRaises(ValueError, get_sloshed_text, '\\u0', '"')
    self.assertRaises(ValueError, get_sloshed_text, '\\u000zz', '"')
    self.assertRaises(ValueError, get_sloshed_text, '\\U0', '"')
    self.assertRaises(ValueError, get_sloshed_text, '\\U000000zz', '"')
    for delim in None, '"', "'", ']':
      test_pairs = [
          ('', ''),
          ('abc', 'abc'),
          ('\\\\', '\\'),
          ('\\x40', '@'),
          ('\\u0040', '@'),
          ('\\U00000040', '@'),
      ]
      for c, dc in SLOSH_CHARMAP.items():
        test_pairs.append(('\\' + c, dc))
      if delim is not None:
        test_pairs.append(('\\' + delim, delim))
      for enc0, dec0 in test_pairs:
        if delim is None:
          enc = enc0
        else:
          enc = enc0 + delim
        offset_expected = len(enc)
        ##X("enc = %r", enc)
        dec, offset = get_sloshed_text(enc, delim)
        self.assertEqual(
            dec, dec0, "%r ==> %r, expected %r" % (enc, dec, dec0)
        )
        self.assertEqual(
            offset, offset_expected,
            "get_sloshed_text(%r): returned offset=%d, expected %d" %
            (enc, offset, offset_expected)
        )
    special_func = partial(
        get_envvar, environ=self.env, specials=self.env_specials
    )
    specials = {'$': special_func}
    self.assertEqual(
        get_sloshed_text(r'\$', None, specials=specials), ('$', 2)
    )
    self.assertEqual(
        get_sloshed_text('$A', None, specials=specials), ('AA', 2)
    )
    self.assertEqual(
        get_sloshed_text('$B1', None, specials=specials), ('BB1', 3)
    )
    self.assertEqual(
        get_sloshed_text('$!', None, specials=specials), ('99', 2)
    )
    self.assertEqual(
        get_sloshed_text('-$A-$B1-$!-', None, specials=specials),
        ('-AA-BB1-99-', 11)
    )
    self.assertEqual(
        get_sloshed_text('-$A-\\$B1-$!-', None, specials=specials),
        ('-AA-$B1-99-', 12)
    )

  def test04get_qstr(self):
    ''' Test get_qstr.
    '''
    self.assertRaises(ValueError, get_qstr, '')
    self.assertRaises(ValueError, get_qstr, 'x')
    self.assertRaises(ValueError, get_qstr, '"x')
    self.assertEqual(get_qstr('""'), ('', 2))
    self.assertEqual(get_qstr("''", q="'"), ('', 2))
    self.assertEqual(get_qstr('"x"'), ('x', 3))
    self.assertEqual(get_qstr('x', q=None), ('x', 1))
    self.assertEqual(get_qstr('"\\""'), ('"', 4))
    self.assertEqual(get_qstr('"\\\\"'), ('\\', 4))
    self.assertEqual(get_qstr('"\\t"'), ('\t', 4))
    self.assertEqual(get_qstr('"$B1"', environ=self.env), ('BB1', 5))
    self.assertEqual(get_qstr('"\\$B1"', environ=self.env), ('$B1', 6))

  def test05get_identifier(self):
    ''' Test get_identifier.
    '''
    self.assertEqual(get_identifier(''), ('', 0))
    self.assertEqual(get_identifier('a'), ('a', 1))
    self.assertEqual(get_identifier('a1'), ('a1', 2))
    self.assertEqual(get_identifier('1a'), ('', 0))
    self.assertEqual(get_identifier('1a', 1), ('a', 2))

  def test05get_dotted_identifier(self):
    ''' test get_gotted_identifier.
    '''
    self.assertEqual(get_dotted_identifier(''), ('', 0))
    self.assertEqual(get_dotted_identifier('a'), ('a', 1))
    self.assertEqual(get_dotted_identifier('a1'), ('a1', 2))
    self.assertEqual(get_dotted_identifier('1a'), ('', 0))
    self.assertEqual(get_dotted_identifier('1a', 1), ('a', 2))
    self.assertEqual(get_dotted_identifier('a.b'), ('a.b', 3))
    self.assertEqual(get_dotted_identifier('a1.b'), ('a1.b', 4))
    self.assertEqual(get_dotted_identifier('a1..b'), ('a1', 2))
    self.assertEqual(get_dotted_identifier('a1.b.c'), ('a1.b.c', 6))
    self.assertEqual(get_dotted_identifier('a1.b.c+'), ('a1.b.c', 6))

def selftest(argv):
  ''' Run selftests.
  '''
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
