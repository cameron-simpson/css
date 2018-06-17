#!/usr/bin/env python3
#

''' Conversion functions.
'''

from cs.lex import skipwhite
from cs.pfx import Pfx
from cs.units import multiparse as multiparse_units, \
    BINARY_BYTES_SCALE, DECIMAL_BYTES_SCALE, DECIMAL_SCALE

def get_integer(s, offset):
  ''' Parse an integer followed by an optional scale and return computed value.
  '''
  return multiparse_units(
      s,
      (BINARY_BYTES_SCALE, DECIMAL_BYTES_SCALE, DECIMAL_SCALE),
      offset
  )

def scaled_value(s):
  ''' Convert a scaled value such as "8 GiB" into an int.
  '''
  value, offset = get_integer(s, 0)
  offset = skipwhite(s, offset)
  if offset < len(s):
    raise ValueError("unparsed text: %r" % (s[offset:],))
  return value

def convert_param(params, key, *, decoder=None):
  ''' Convert a parameter.
  '''
  param = params.get(key)
  if isinstance(param, str):
    with Pfx("%s: %r", key, param):
      params[key] = scaled_value(param)

def convert_param_int(params, key):
  return convert_param(params, key, decoder=int)

def convert_param_scaled_int(params, key):
  ''' Convert a scaled value into an int.
  '''
  return convert_param(params, key, decoder=scaled_value)
