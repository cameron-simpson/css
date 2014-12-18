#!/usr/bin/python
#
# Self tests for cs.lex.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from cs.lex import texthexify, untexthexify, get_sloshed_text, SLOSH_CHARMAP
from cs.py3 import makebytes
##from cs.logutils import X

class TestLex(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def test00untexthexify(self):
    self.assertEqual(b'', untexthexify(''))
    self.assertEqual(b'A', untexthexify('41'))
    self.assertEqual(b'ABC', untexthexify('41[BC]'))
    self.assertRaises(TypeError, untexthexify, 'a')

  def test01texthexify(self):
    self.assertEqual('', texthexify(b''))
    self.assertEqual('00', texthexify(makebytes( (0x00,) )))

  def test02get_sloshed_text(self):
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
        test_pairs.append( ('\\'+c, dc) )
      if delim is not None:
        test_pairs.append( ('\\'+delim, delim) )
      for enc0, dec0 in test_pairs:
        if delim is None:
          enc = enc0
        else:
          enc = enc0 + delim
        offset_expected = len(enc)
        ##X("enc = %r", enc)
        dec, offset = get_sloshed_text(enc, delim)
        self.assertEqual( dec, dec0, "%r ==> %r, expected %r" % (enc, dec, dec0) )
        self.assertEqual( offset, offset_expected,
                          "get_sloshed_text(%r): returned offset=%d, expected %d"
                          % (enc, offset, offset_expected) )

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
