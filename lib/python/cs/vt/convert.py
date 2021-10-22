#!/usr/bin/env python3
#

''' Conversion functions.
'''

from functools import partial
from os.path import expanduser, isabs as isabspath, join as joinpath
from cs.lex import skipwhite
from cs.pfx import Pfx
from cs.units import multiparse as multiparse_units, \
    BINARY_BYTES_SCALE, DECIMAL_BYTES_SCALE, DECIMAL_SCALE

def get_integer(s, offset):
  ''' Parse an integer followed by an optional scale and return computed value.
  '''
  return multiparse_units(
      s, (BINARY_BYTES_SCALE, DECIMAL_BYTES_SCALE, DECIMAL_SCALE), offset
  )

def scaled_value(s):
  ''' Convert a scaled value such as "8 GiB" into an int.
  '''
  value, offset = get_integer(s, 0)
  offset = skipwhite(s, offset)
  if offset < len(s):
    raise ValueError("unparsed text: %r" % (s[offset:],))
  return value

def truthy_word(s):
  ''' Convert a word into `True` or `False`.
  '''
  s = s.lower()
  if s in ('true', 'yes'):
    return True
  if s in ('false', 'no'):
    return False
  raise ValueError("invalid true/flase value: %r" % (s,))

def convert_param(params, key, *, decoder=None):
  ''' Convert a parameter.
  '''
  param = params.get(key)
  if isinstance(param, str):
    with Pfx("%s: %r", key, param):
      params[key] = decoder(param)

def convert_param_int(params, key):
  ''' Convert an integer parameter to an int.
  '''
  return convert_param(params, key, decoder=int)

def convert_param_scaled_int(params, key):
  ''' Convert a scaled value into an int.
  '''
  return convert_param(params, key, decoder=scaled_value)

def convert_param_bool(params, key):
  ''' Convert an integer parameter to an bool.
  '''
  return convert_param(params, key, decoder=truthy_word)

def expand_path(path, basedir=None):
  ''' Expand a path specification.
  '''
  path = expanduser(path)
  if basedir and not isabspath(path):
    path = joinpath(basedir, path)
  return path

def convert_param_path(params, key, basedir=None):
  ''' Convert a path parameter to an absolute pathname.
  '''
  return convert_param(
      params, key, decoder=partial(expand_path, basedir=basedir)
  )
