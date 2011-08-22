#!/usr/bin/python

''' Convenience facilities to use templates with syntax like:
        {a.b(foo).c}
    where a.b(foo).c is a simple Python expression.
'''

import sys
import re
import string

def re_alt(res, name=None):
  return '(' + '|'.join(res) + ')'
def re_opt(re):
  return '(' + re + ')?'

IDENTIFIER = r'[a-zA-Z][a-zA-Z_0-9]*'
CONST_VALUE = re_alt( (r'\d+',
                       r'"[^\\"]*"',
                       r"'[^\\']*'",
                      ) )
CONST_VALUES = re_opt( CONST_VALUE + '(\s*,\s*' + CONST_VALUE + ')*' )
CALLISH    = IDENTIFIER + re_opt( re_alt( ( '\(\s*' + CONST_VALUES + '\s*\)',
                                            '\[\s*' + CONST_VALUE + '\s*\]',
                                          ) )
                                )
DOTTED     = CALLISH + '(\.' + CALLISH + ')*'

CURLY      = re_alt( ( r'\{(?P<braced>' + DOTTED + r')\}',
                       r'(?P<named>)(?!)',
                       r'(?P<escaped>)(?!)',
                       r'(?P<invalid>\{)',
                     )
                   )
re_CURLY   = re.compile(CURLY)
re_CURLY_SIMPLE = re.compile(r'\{(?P<braced>' + DOTTED + r')\}')

def curly_substitute(s, mapfn, safe=False):
  ''' Replace '{foo}' patterns in `s` according to `mapfn(foo)`.
      If `safe`, values of 'foo' that throw an exception from mapfn
      are replaced by '{foo}'. Otherwise the exception is rethrown.
      Return the string with the replacements made.
  '''
  sofar = 0
  strings = []
  for M in re_CURLY_SIMPLE.finditer(s):
    strings.append(s[sofar:M.start()])
    try:
      repl = str(mapfn(M.group('braced')))
    except Exception, e:
      if not safe:
        raise
      repl = M.group()
    strings.append(repl)
    sofar = M.end()
  if sofar < len(s):
    strings.append(s[sofar:])
  subst = ''.join(strings)
  return subst

class CurlyTemplate(string.Template):
  ''' A CurlyTemplate is a subclass of string.Template that supports a
      fairly flexible substitution scheme of the form {a.b.c} permitting any
      dotted expression consisting of regular letter-initialised identifiers,
      which may also include calls such as {a.b(3).c}.
      By using an EvalMapping with the .substitute() or .safe_substitute()
      methods it should be possible to implement fairly safe embedding
      of simple expressions in the template.
  '''
  delimiter = ''
  pattern = CURLY

class EvalMapping(object):
  ''' An EvalMapping is a mapping suitable for use in with a CurlyTemplate
      or more generally with a string.Template.
  '''

  def __init__(self, locals=None, globals=None):
    ''' Initialise the EvalMapping. The optional parameters `locals` and
        `globals` are used for the locals and globals parameters of the
        eval() call within __getitem__.
    '''
    self.locals = {} if locals is None else locals
    self.globals = {} if globals is None else globals

  def __getitem__(self, expr):
    try:
      value = eval(expr, self.locals, self.globals)
    except Exception, e:
      raise KeyError(str(e))
    return value
